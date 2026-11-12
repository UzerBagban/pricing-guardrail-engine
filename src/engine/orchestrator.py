"""Autonomous Pricing Guardrail & Margin Defense Engine Pipeline Orchestrator.

Phase 5: Production Hardening
Coordinates the complete end-to-end pricing guardrail lifecycle:
  1. Ingestion & Market Telemetry Stream Evaluation
  2. Guardrail Margin & VRP Drift Enforcement
  3. Suppression Queue & Cost Floor Defense
  4. Automated Price Correction & Immutable Audit Logging
  5. Alert Notification Dispatch (Console Mock or Live Webhooks)
  6. Machine Learning 7-Day Floor Breach Risk Radar Refresh
  7. Executive Dashboard & Audit Memo Publication

Usage:
  python -m src.engine.orchestrator --once
  python -m src.engine.orchestrator --once --commit --simulate-telemetry 20
  python -m src.engine.orchestrator --daemon --interval 30 --max-cycles 5
  python -m src.engine.main
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.analytics.dashboard import get_full_dashboard_data, save_html_dashboard
from src.analytics.memo_generator import (
    format_html_memo,
    format_markdown_memo,
    generate_executive_memo_data,
)
from src.engine import adjuster
from src.engine.predictive import predict_catalog_risks
from src.ingestion import db, notifier
from src.ingestion.db import TelemetryEvent, add_suppression, get_cursor, persist_batch
from src.telemetry.simulator import generate_telemetry

log = logging.getLogger("engine.orchestrator")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class CycleReport:
    """Detailed metrics report for a single pipeline orchestration cycle."""
    cycle_id: str
    timestamp: str
    mode: str
    duration_ms: float
    events_ingested: int = 0
    breaches_detected: int = 0
    warnings_detected: int = 0
    suppressions_queued: int = 0
    adjustments_attempted: int = 0
    adjustments_succeeded: int = 0
    adjustments_skipped: int = 0
    alerts_dispatched: int = 0
    critical_risk_skus_count: int = 0
    invariant_passed: bool = True
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PipelineOrchestrator:
    """Autonomous coordinator that drives the multi-stage pricing defense pipeline."""

    def __init__(
        self,
        commit: bool = False,
        mock_alerts: bool = True,
        batch_size: int = 50,
        output_dir: str = "docs",
    ):
        self.commit = commit
        self.mock_alerts = mock_alerts
        self.batch_size = batch_size
        self.output_dir = output_dir
        self._running = False

    def verify_database(self) -> bool:
        """Ensure PostgreSQL schema and connections are operational."""
        try:
            db.init_schema()
            return db.test_connection()
        except Exception as exc:
            log.error("Database connection check failed: %s", exc)
            return False

    def stage_ingest_and_evaluate(
        self,
        input_records: Optional[List[Dict[str, Any]]] = None,
        simulate_count: int = 0,
    ) -> Tuple[int, int, int]:
        """Stage 1 & 2: Ingest market events, evaluate guardrails, and queue suppressions."""
        ingested = 0
        breaches = 0
        warnings = 0

        # Generate synthetic records if requested and no input provided
        records_to_process = input_records or []
        if simulate_count > 0 and not input_records:
            simulated = generate_telemetry(num_records=simulate_count)
            records_to_process = [asdict(r) for r in simulated]

        if not records_to_process:
            return 0, 0, 0

        telemetry_events: List[TelemetryEvent] = []
        for r in records_to_process:
            event = TelemetryEvent(
                event_id=r.get("event_id") or str(uuid.uuid4()),
                timestamp=r.get("timestamp") or datetime.now(timezone.utc),
                product_id=r.get("product_id", ""),
                sku=r.get("sku"),
                asin=r.get("asin"),
                category=r.get("category"),
                channel=r.get("channel", "amazon"),
                event_type=r.get("event_type", "price_update"),
                scenario=r.get("scenario", "normal"),
                our_price=r.get("our_price"),
                floor_price=r.get("floor_price"),
                vrp_price=r.get("vrp_price"),
                buybox_price=r.get("buybox_price"),
                lowest_competitor_price=r.get("lowest_competitor_price"),
                competitor_count=r.get("competitor_count"),
                cogs=r.get("cogs"),
                shipping=r.get("shipping"),
                fees=r.get("fees"),
                min_margin=r.get("min_margin"),
                calculated_margin_pct=r.get("calculated_margin_pct"),
                vrp_drift_pct=r.get("vrp_drift_pct"),
                vrp_threshold_pct=r.get("vrp_threshold_pct"),
                drift_detected=r.get("drift_detected", False),
                elasticity_score=r.get("elasticity_score"),
                elasticity_threshold=r.get("elasticity_threshold"),
                elasticity_breach=r.get("elasticity_breach", False),
                guardrail_status=r.get("guardrail_status", "healthy"),
                suppression_triggered=r.get("suppression_triggered", False),
                suppression_reason=r.get("suppression_reason"),
                metadata=r.get("metadata", {}),
            )
            telemetry_events.append(event)

            status = (event.guardrail_status or "").lower()
            if status == "breach":
                breaches += 1
            elif status == "warning":
                warnings += 1

            # Auto-queue suppression if predatory undercut or below floor
            lowest_comp = float(event.lowest_competitor_price or 0)
            floor = float(event.floor_price or 0)
            if (lowest_comp > 0 and lowest_comp < floor) or status == "breach":
                if self.commit and event.sku:
                    add_suppression(
                        sku=event.sku,
                        reason="below_floor_price",
                        channel=event.channel or "amazon",
                        floor_price=floor,
                        target_price=lowest_comp or floor,
                    )

        if self.commit and telemetry_events:
            ingested = persist_batch(telemetry_events)
        else:
            ingested = len(telemetry_events)

        return ingested, breaches, warnings

    def stage_auto_adjust(self) -> Tuple[int, int, int, bool]:
        """Stage 3: Process pending suppressions, enforce recovery floor, log adjustments."""
        try:
            pending_rows = adjuster.fetch_pending(limit=self.batch_size)
        except Exception as exc:
            log.warning("Could not fetch pending rows from DB: %s", exc)
            pending_rows = []

        attempted = len(pending_rows)
        succeeded = 0
        skipped = 0
        invariant_ok = True

        for row in pending_rows:
            try:
                rec = adjuster.process_row(row, commit=self.commit)
                if rec.action == "adjust":
                    succeeded += 1
                    # Enforce the non-negotiable invariant: recovered >= floor
                    if rec.recovered_price is not None and rec.floor_price is not None:
                        if rec.recovered_price < rec.floor_price:
                            invariant_ok = False
                            log.critical(
                                "INVARIANT VIOLATION: SKU %s recovered %s < floor %s",
                                rec.sku, rec.recovered_price, rec.floor_price
                            )
                else:
                    skipped += 1
            except Exception as exc:
                log.error("Failed to process adjustment for row %s: %s", row.get("id"), exc)

        return attempted, succeeded, skipped, invariant_ok

    def stage_dispatch_alerts(self) -> int:
        """Stage 4: Process suppression queue and emit webhook alerts."""
        try:
            return notifier.process_batch(
                batch_size=self.batch_size,
                use_mock=self.mock_alerts,
            )
        except Exception as exc:
            log.warning("Alert dispatch stage completed with note: %s", exc)
            return 0

    def stage_predictive_risk_radar(self) -> int:
        """Stage 5: Run machine learning 7-day breach probability forecaster."""
        try:
            risks = predict_catalog_risks()
            critical_count = sum(1 for r in risks if r.get("risk_tier") in ("CRITICAL", "HIGH"))
            return critical_count
        except Exception as exc:
            log.warning("Predictive model scoring: %s", exc)
            return 0

    def stage_publish_reports(self) -> None:
        """Stage 6: Refresh HTML executive dashboard and markdown audit memos."""
        try:
            save_html_dashboard("dashboard.html")
            memo_data = generate_executive_memo_data()
            md_memo = format_markdown_memo(memo_data)
            html_memo = format_html_memo(memo_data)

            os.makedirs(self.output_dir, exist_ok=True)
            with open(os.path.join(self.output_dir, "executive_audit_memo.md"), "w", encoding="utf-8") as f:
                f.write(md_memo)
            with open(os.path.join(self.output_dir, "executive_audit_memo.html"), "w", encoding="utf-8") as f:
                f.write(html_memo)
        except Exception as exc:
            log.warning("Report refresh stage: %s", exc)

    def run_cycle(
        self,
        input_records: Optional[List[Dict[str, Any]]] = None,
        simulate_count: int = 0,
    ) -> CycleReport:
        """Execute a single full cycle of all 6 pricing defense pipeline stages."""
        t0 = time.perf_counter()
        cycle_id = f"CYC-{uuid.uuid4().hex[:8].upper()}"
        mode = "commit" if self.commit else "dry-run"
        errors: List[str] = []

        log.info("Starting Pipeline Cycle [%s] (mode=%s)", cycle_id, mode)

        # 1. Telemetry Ingestion & Evaluation
        ingested, breaches, warnings = 0, 0, 0
        try:
            ingested, breaches, warnings = self.stage_ingest_and_evaluate(
                input_records=input_records,
                simulate_count=simulate_count,
            )
        except Exception as exc:
            errors.append(f"Ingestion stage error: {exc}")

        # 2. Auto-Adjustment & Price Correction
        attempted, succeeded, skipped, invariant_ok = 0, 0, 0, True
        try:
            attempted, succeeded, skipped, invariant_ok = self.stage_auto_adjust()
        except Exception as exc:
            errors.append(f"Auto-adjust stage error: {exc}")

        # 3. Alert Notifications
        alerts_sent = 0
        try:
            alerts_sent = self.stage_dispatch_alerts()
        except Exception as exc:
            errors.append(f"Alert dispatch stage error: {exc}")

        # 4. ML 7-Day Breach Risk Radar
        critical_risks = 0
        try:
            critical_risks = self.stage_predictive_risk_radar()
        except Exception as exc:
            errors.append(f"Predictive stage error: {exc}")

        # 5. Dashboard & Memo Publication
        try:
            self.stage_publish_reports()
        except Exception as exc:
            errors.append(f"Report publication error: {exc}")

        duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        report = CycleReport(
            cycle_id=cycle_id,
            timestamp=_utcnow_iso(),
            mode=mode,
            duration_ms=duration_ms,
            events_ingested=ingested,
            breaches_detected=breaches,
            warnings_detected=warnings,
            suppressions_queued=breaches,
            adjustments_attempted=attempted,
            adjustments_succeeded=succeeded,
            adjustments_skipped=skipped,
            alerts_dispatched=alerts_sent,
            critical_risk_skus_count=critical_risks,
            invariant_passed=invariant_ok,
            errors=errors,
        )

        log.info(
            "Completed Cycle [%s] in %.2fms | Ingested: %d | Breaches: %d | Adjusted: %d | Alerts: %d",
            cycle_id, duration_ms, ingested, breaches, succeeded, alerts_sent
        )
        return report

    def run_daemon(
        self,
        interval_seconds: int = 60,
        max_cycles: Optional[int] = None,
        simulate_per_cycle: int = 10,
    ) -> List[CycleReport]:
        """Run the orchestrator continuously as an autonomous background daemon."""
        self._running = True
        cycles_run = 0
        history: List[CycleReport] = []

        def handle_stop(sig, frame):
            log.info("Shutdown signal received. Stopping orchestrator daemon...")
            self._running = False

        try:
            signal.signal(signal.SIGINT, handle_stop)
            signal.signal(signal.SIGTERM, handle_stop)
        except (ValueError, AttributeError):
            pass

        log.info(
            "Autonomous Pricing Defense Daemon started. Interval: %ds, Mode: %s",
            interval_seconds, "LIVE" if self.commit else "DRY-RUN"
        )

        while self._running:
            cycles_run += 1
            log.info("--- Cycle #%d ---", cycles_run)
            report = self.run_cycle(simulate_count=simulate_per_cycle)
            history.append(report)

            if max_cycles and cycles_run >= max_cycles:
                log.info("Reached maximum cycles limit (%d). Exiting daemon.", max_cycles)
                break

            if not self._running:
                break

            log.info("Sleeping for %d seconds until next cycle...", interval_seconds)
            time.sleep(interval_seconds)

        log.info("Daemon finished. Total cycles executed: %d", cycles_run)
        return history


def main():
    parser = argparse.ArgumentParser(description="Autonomous Pricing Defense Pipeline Orchestrator")
    parser.add_argument("--once", action="store_true", help="Execute a single pipeline cycle and exit")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in background daemon loop")
    parser.add_argument("--interval", type=int, default=60, help="Interval in seconds between daemon cycles (default: 60)")
    parser.add_argument("--commit", action="store_true", help="Persist adjustments and database mutations (live mode)")
    parser.add_argument("--live", action="store_true", help="Alias for --commit")
    parser.add_argument("--mock-alerts", action="store_true", default=True, help="Use mock console webhook dispatcher")
    parser.add_argument("--live-alerts", action="store_true", help="Use live external webhook dispatcher")
    parser.add_argument("--simulate-telemetry", type=int, default=15, help="Simulate N market telemetry events per cycle")
    parser.add_argument("--max-cycles", type=int, default=None, help="Stop daemon after N cycles (for testing)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [orchestrator] %(message)s",
    )

    commit_mode = args.commit or args.live
    mock_mode = not args.live_alerts

    orchestrator = PipelineOrchestrator(
        commit=commit_mode,
        mock_alerts=mock_mode,
    )

    if not orchestrator.verify_database():
        print(json.dumps({"status": "db_error", "message": "Failed to connect to PostgreSQL database."}))
        sys.exit(1)

    if args.daemon:
        orchestrator.run_daemon(
            interval_seconds=args.interval,
            max_cycles=args.max_cycles,
            simulate_per_cycle=args.simulate_telemetry,
        )
        sys.exit(0)

    # Default to single cycle (--once)
    report = orchestrator.run_cycle(simulate_count=args.simulate_telemetry)
    print("\n=======================================================")
    print(f"  PIPELINE ORCHESTRATION CYCLE [{report.cycle_id}] SUMMARY")
    print("=======================================================")
    print(json.dumps(report.to_dict(), indent=2))

    if not report.invariant_passed:
        print("[!] CRITICAL INVARIANT VIOLATION: A price was adjusted below cost floor!")
        sys.exit(2)

    sys.exit(0)


if __name__ == "__main__":
    main()
