"""
Ingestion Pipeline Evaluator

Consumes telemetry payloads, evaluates against guardrails, and persists audit traces to PostgreSQL.
"""
import argparse
import json
import logging
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Optional

from src.ingestion.db import (
    get_connection_pool, get_cursor, close_pool, init_schema,
    TelemetryEvent, EvaluationResult, persist_event, persist_batch,
    get_suppressed_skus, add_suppression, cleanup_expired_suppressions,
    test_connection
)
from src.engine.guardrail import evaluate_price, GuardrailResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("ingestion.evaluator")


@dataclass
class TelemetryPayload:
    """Inbound telemetry payload structure (input format, maps to TelemetryEvent)."""
    event_id: Optional[str] = None
    timestamp: Optional[str] = None
    product_id: str = ""
    sku: Optional[str] = None
    asin: Optional[str] = None
    category: Optional[str] = None
    channel: Optional[str] = None
    event_type: Optional[str] = None
    scenario: Optional[str] = None
    our_price: Optional[float] = None
    floor_price: Optional[float] = None
    vrp_price: Optional[float] = None
    buybox_price: Optional[float] = None
    lowest_competitor_price: Optional[float] = None
    competitor_count: Optional[int] = None
    cogs: Optional[float] = None
    shipping: Optional[float] = None
    fees: Optional[float] = None
    min_margin: Optional[float] = None
    calculated_margin_pct: Optional[float] = None
    vrp_drift_pct: Optional[float] = None
    vrp_threshold_pct: Optional[float] = None
    drift_detected: Optional[bool] = None
    elasticity_score: Optional[float] = None
    elasticity_threshold: Optional[float] = None
    elasticity_breach: Optional[bool] = None
    guardrail_status: Optional[str] = None
    suppression_triggered: Optional[bool] = None
    suppression_reason: Optional[str] = None
    metadata: Optional[dict] = None

    def __post_init__(self):
        # Ensure product_id is set
        if not self.product_id and self.sku:
            self.product_id = self.sku


@dataclass
class ProductBaseline:
    """Product baseline from database."""
    product_id: str
    cost_price: float
    shipping_cost: float
    min_margin_pct: float
    pricing_rule: str


class IngestionEvaluator:
    """Evaluates telemetry payloads and persists audit traces."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.pool = None if dry_run else get_connection_pool()

    def close(self):
        if self.pool:
            close_pool()
            self.pool = None

    def fetch_product_baseline(self, product_id: str) -> Optional[ProductBaseline]:
        """Fetch active product baseline from PostgreSQL."""
        if self.dry_run:
            # Return mock baseline for dry-run
            return ProductBaseline(
                product_id=product_id,
                cost_price=50.0,
                shipping_cost=5.0,
                min_margin_pct=0.15,
                pricing_rule="standard"
            )

        query = """
            SELECT
                p.product_id,
                pc.cost_price,
                sp.shipping_cost,
                pr.min_margin_pct,
                pr.pricing_rule
            FROM products p
            JOIN product_costs pc ON p.product_id = pc.product_id
            JOIN shipping_profiles sp ON p.shipping_profile_id = sp.shipping_profile_id
            JOIN pricing_rules pr ON p.pricing_rule_id = pr.pricing_rule_id
            WHERE p.product_id = %s
              AND p.is_active = true
              AND pc.is_active = true
              AND sp.is_active = true
              AND pr.is_active = true
        """
        try:
            with get_cursor() as cur:
                cur.execute(query, (product_id,))
                row = cur.fetchone()
                if row:
                    return ProductBaseline(
                        product_id=row["product_id"],
                        cost_price=float(row["cost_price"]),
                        shipping_cost=float(row["shipping_cost"]),
                        min_margin_pct=float(row["min_margin_pct"]),
                        pricing_rule=row["pricing_rule"]
                    )
        except Exception as e:
            log.error(f"Failed to fetch baseline for {product_id}: {e}")
        return None

    def evaluate_payload(self, payload: TelemetryPayload) -> Optional[EvaluationResult]:
        """Evaluate a telemetry payload against guardrails."""
        # Use payload fields if available, otherwise fetch from database
        cost_price = payload.cogs if payload.cogs is not None else None
        shipping_cost = payload.shipping if payload.shipping is not None else None
        fees = payload.fees if payload.fees is not None else 0.0
        min_margin = payload.min_margin if payload.min_margin is not None else None
        proposed_price = payload.our_price if payload.our_price is not None else None
        vrp_price = payload.vrp_price if payload.vrp_price is not None else None

        # If missing critical fields, try database
        if cost_price is None or shipping_cost is None or min_margin is None or proposed_price is None:
            baseline = self.fetch_product_baseline(payload.product_id)
            if not baseline:
                log.warning(f"No baseline found for product {payload.product_id} and payload missing required fields")
                return None
            cost_price = cost_price if cost_price is not None else baseline.cost_price
            shipping_cost = shipping_cost if shipping_cost is not None else baseline.shipping_cost
            min_margin_pct = min_margin if min_margin is not None else baseline.min_margin_pct
            proposed_price = proposed_price if proposed_price is not None else baseline.cost_price * (1 + baseline.min_margin_pct)
        else:
            min_margin_pct = min_margin / 100.0  # Convert percentage to decimal

        # Total shipping includes fees
        total_shipping = shipping_cost + fees

        # Evaluate via guardrail engine
        guardrail_result = evaluate_price(
            proposed_price=proposed_price,
            cost_price=cost_price,
            shipping_cost=total_shipping,
            min_margin_pct=min_margin_pct,
            vrp_price=vrp_price
        )

        # Calculate VRP drift if VRP price provided
        vrp_drift_pct = None
        vrp_severity = None
        if vrp_price and vrp_price > 0 and proposed_price:
            vrp_drift_pct = ((proposed_price - vrp_price) / vrp_price) * 100
            if abs(vrp_drift_pct) > 5:
                vrp_severity = "HIGH" if abs(vrp_drift_pct) > 15 else "MEDIUM"

        # Map guardrail action to string and extract warnings
        guardrail_action = guardrail_result.action.value
        warnings = guardrail_result.warnings

        return EvaluationResult(
            product_id=payload.product_id,
            proposed_price=proposed_price,
            floor_price=guardrail_result.floor_price,
            margin_pct=guardrail_result.margin_pct,
            vrp_drift_pct=vrp_drift_pct,
            vrp_severity=vrp_severity,
            guardrail_action=guardrail_action,
            warnings=warnings,
            timestamp=datetime.now(timezone.utc)
        )

    def persist_vrp_drift_event(self, result: EvaluationResult) -> bool:
        """Log VRP drift breach to vrp_drift_events table."""
        if result.vrp_drift_pct is None or abs(result.vrp_drift_pct) <= 5:
            return True  # No drift breach to log

        if self.dry_run:
            log.info(f"[DRY-RUN] Would log VRP drift: {result.product_id} drift={result.vrp_drift_pct:.2f}% severity={result.vrp_severity}")
            return True

        # Look up product_id (UUID) from SKU
        from src.ingestion.db import get_product_id_by_sku
        sku = result.product_id  # In EvaluationResult, product_id is actually the SKU
        product_uuid = get_product_id_by_sku(sku)
        if not product_uuid:
            log.warning(f"Product not found for SKU: {sku}, skipping VRP drift log")
            return False

        # Calculate VRP price from drift percentage
        vrp_price = result.proposed_price / (1 + result.vrp_drift_pct / 100) if result.vrp_drift_pct != -100 else result.proposed_price

        # Determine drift type
        drift_type = "above_vrp" if result.vrp_drift_pct > 0 else "below_vrp"

        # Determine recommended action
        if result.vrp_severity == "HIGH":
            recommended_action = "immediate_review"
        elif result.vrp_severity == "MEDIUM":
            recommended_action = "price_adjustment"
        else:
            recommended_action = "monitor"

        query = """
            INSERT INTO vrp_drift_events (
                product_id, asin, drift_type, severity, previous_vrp, current_vrp,
                drift_pct, threshold_pct, our_price_at_event, floor_price_at_event,
                recommended_action, status, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        try:
            with get_cursor() as cur:
                cur.execute(query, (
                    product_uuid,
                    None,  # asin
                    drift_type,
                    result.vrp_severity,
                    vrp_price,  # previous_vrp
                    vrp_price,  # current_vrp (same for now)
                    result.vrp_drift_pct,
                    5.0,  # threshold_pct
                    result.proposed_price,
                    result.floor_price,
                    recommended_action,
                    "new",
                    result.timestamp
                ))
            log.info(f"Logged VRP drift event for {result.product_id}: {result.vrp_drift_pct:.2f}% ({result.vrp_severity})")
            return True
        except Exception as e:
            log.error(f"Failed to persist VRP drift event: {e}")
            return False

    def persist_suppression_queue(self, result: EvaluationResult) -> bool:
        """Enqueue margin-violating discounts to suppression_queue."""
        # Only enqueue if guardrail action is suppress (floor breach)
        if result.guardrail_action != "suppress":
            return True  # No suppression needed

        if self.dry_run:
            log.info(f"[DRY-RUN] Would enqueue suppression: {result.product_id} margin={result.margin_pct:.2%} floor={result.floor_price:.2f}")
            return True

        # Look up product_id (UUID) from SKU
        from src.ingestion.db import get_product_id_by_sku
        sku = result.product_id  # In EvaluationResult, product_id is actually the SKU
        product_uuid = get_product_id_by_sku(sku)
        if not product_uuid:
            log.warning(f"Product not found for SKU: {sku}, skipping suppression queue")
            return False

        query = """
            INSERT INTO suppression_queue (
                product_id, asin, channel, suppression_reason,
                target_price, floor_price, vrp_price, margin_pct,
                elasticity_score, severity, status, suppressed_at, released_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        try:
            with get_cursor() as cur:
                cur.execute(query, (
                    product_uuid,
                    None,  # asin
                    "all",  # channel
                    "margin_breach",  # suppression_reason
                    result.proposed_price,  # target_price
                    result.floor_price,
                    None,  # vrp_price
                    result.margin_pct,
                    None,  # elasticity_score
                    "medium",  # severity
                    "pending",  # status
                    result.timestamp,  # suppressed_at
                    None  # released_at (NULL until released)
                ))
            log.info(f"Enqueued suppression for {result.product_id}: proposed={result.proposed_price:.2f} floor={result.floor_price:.2f}")
            return True
        except Exception as e:
            log.error(f"Failed to persist suppression queue: {e}")
            return False

    def persist_price_change_log(self, result: EvaluationResult, old_price: Optional[float] = None) -> bool:
        """Record price decision to price_change_log."""
        if self.dry_run:
            log.info(f"[DRY-RUN] Would log price change: {result.product_id} old={old_price} new={result.proposed_price:.2f}")
            return True

        metadata = {
            "margin_pct": result.margin_pct,
            "floor_price": result.floor_price,
            "vrp_drift_pct": result.vrp_drift_pct,
            "vrp_severity": result.vrp_severity,
            "guardrail_action": result.guardrail_action,
            "warnings": result.warnings
        }

        # Map guardrail action to change_type
        action_to_type = {
            "hold": "scheduled_reprice",
            "increase_price": "floor_price_update",
            "decrease_price": "floor_price_update",
            "suppress": "suppression_triggered",
        }
        change_type = action_to_type.get(result.guardrail_action, "scheduled_reprice")

        # Look up product_id (UUID) from SKU
        from src.ingestion.db import get_product_id_by_sku
        sku = result.product_id  # In EvaluationResult, product_id is actually the SKU
        product_uuid = get_product_id_by_sku(sku)
        if not product_uuid:
            log.warning(f"Product not found for SKU: {sku}, skipping price change log")
            return False

        query = """
            INSERT INTO price_change_log (
                product_id, change_type, old_price, new_price, floor_price, vrp_price,
                margin_pct_before, margin_pct_after, elasticity_score, triggered_by, metadata, executed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        try:
            with get_cursor() as cur:
                cur.execute(query, (
                    product_uuid,
                    change_type,
                    old_price,
                    result.proposed_price,
                    result.floor_price,
                    None,  # vrp_price - not available in result
                    None,  # margin_pct_before
                    result.margin_pct,
                    None,  # elasticity_score
                    "system",
                    json.dumps(metadata),
                    result.timestamp
                ))
            log.info(f"Logged price change for {result.product_id}: {old_price} -> {result.proposed_price:.2f}")
            return True
        except Exception as e:
            log.error(f"Failed to persist price change log: {e}")
            return False

    def process_payload(self, payload: TelemetryPayload) -> bool:
        """Process a single telemetry payload through the full pipeline."""
        log.info(f"Processing payload for product {payload.product_id}")

        result = self.evaluate_payload(payload)
        if not result:
            return False

        # Persist audit traces
        success = True
        success &= self.persist_vrp_drift_event(result)
        success &= self.persist_suppression_queue(result)
        success &= self.persist_price_change_log(result)

        if success:
            log.info(f"Successfully processed {payload.product_id}: action={result.guardrail_action}")
        else:
            log.error(f"Failed to persist some audit traces for {payload.product_id}")

        return success

    def process_file(self, filepath: str) -> int:
        """Process telemetry payloads from a JSON or JSONL file."""
        try:
            with open(filepath, "r") as f:
                # Try JSON first (array or object)
                content = f.read().strip()
                if not content:
                    log.error(f"Empty file: {filepath}")
                    return 1
                
                # Check if it's JSONL (multiple lines, each valid JSON)
                lines = content.splitlines()
                if len(lines) > 1 and lines[0].strip().startswith('{'):
                    # Likely JSONL format - parse each line
                    payloads = []
                    for line in lines:
                        line = line.strip()
                        if line:
                            try:
                                payloads.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass  # Skip invalid lines
                    if not payloads:
                        log.error(f"No valid JSONL records in {filepath}")
                        return 1
                else:
                    # Standard JSON (array or single object)
                    data = json.loads(content)
                    payloads = data if isinstance(data, list) else [data]
                    
        except Exception as e:
            log.error(f"Failed to load telemetry file {filepath}: {e}")
            return 1

        success_count = 0
        for payload_data in payloads:
            payload = TelemetryPayload(**payload_data)
            if self.process_payload(payload):
                success_count += 1

        log.info(f"Processed {success_count}/{len(payloads)} payloads successfully")
        return 0 if success_count == len(payloads) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingestion Pipeline Evaluator")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without database connections (for testing)"
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        help="Path to telemetry JSON file"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    evaluator = IngestionEvaluator(dry_run=args.dry_run)

    try:
        if args.input_file:
            return evaluator.process_file(args.input_file)
        else:
            log.error("No input file provided")
            return 1
    finally:
        evaluator.close()


if __name__ == "__main__":
    sys.exit(main())