"""Phase 3 Task 1 — Auto-Adjustment & Price Correction Engine.

Consumes rows from `suppression_queue` where status='pending', classifies each
breach (margin breach vs negative VRP drift) using VRPGuardrailEngine and
SuppressionReason, computes a safe recovery price, and persists the adjustment
to `price_change_log` via `db.log_price_change(...)`.

Run modes
---------
python -m src.engine.adjuster --dry-run   # report only; exit 0; writes nothing
python -m src.engine.adjuster --commit    # live: writes price_change_log rows
python -m src.engine.adjuster --live      # alias for --commit

Live DB enum (per directive; verified against migrations):
  suppression_queue.status: 'pending' rows are consumed.
  suppression_queue.suppression_reason live values mapped in classify() below.
  price_change_log.channel CHECK: ('amazon','ebay','walmart','shopify','internal') —
    a suppression row with channel='all' is logged as 'internal'.
Do not read/write .env; access verified via db.init_schema() + db.test_connection().
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from src.ingestion import db
from src.engine.calculator import (
    CostBreakdown,
    PricingRule,
    calculate_floor_price,
    calculate_min_margin_amount,
)
from src.engine.guardrail import PriceEvaluationInput, VRPGuardrailEngine
from src.engine.suppression import SuppressionReason

MAX_ROWS_PER_RUN = 500
DEFAULT_MIN_MARGIN_PCT = Decimal("15.0")
DEFAULT_VRP_DRIFT_THRESHOLD_PCT = Decimal("5.0")
DEFAULT_ELASTICITY_THRESHOLD = Decimal("2.5")

# Live suppression_queue.suppression_reason values (CHECK constraint per migrations;
# 'vrp_violation' is the live drift value observed in the enum).
MARGIN_REASONS = {
    SuppressionReason.BELOW_FLOOR_PRICE.value,   # below_floor_price
    SuppressionReason.MARGIN_BREACH.value,       # margin_breach
    SuppressionReason.ELASTICITY_BREACH.value,   # elasticity_breach
}
VRP_REASONS = {
    SuppressionReason.VRP_DRIFT_CRITICAL.value,  # vrp_drift_critical
    "vrp_violation",                             # live DB enum value
}
MANUAL_REASONS = {SuppressionReason.MANUAL_OVERRIDE.value}  # never auto-adjust

VALID_CHANNELS = {"amazon", "ebay", "walmart", "shopify", "internal"}

log = logging.getLogger("engine.adjuster")


@dataclass
class AdjustmentRecord:
    sku: str
    asin: str
    channel: str
    reason: str
    classification: str
    original_price: Optional[Decimal]
    recovered_price: Optional[Decimal]
    floor_price: Optional[Decimal]
    vrp_price: Optional[Decimal]
    margin_before: Optional[Decimal]
    margin_after: Optional[Decimal]
    elasticity_score: Optional[Decimal]
    change_type: Optional[str]
    action: str  # "adjust" | "skip"
    skip_reason: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)


def _d(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def classify(reason: str) -> str:
    if reason in MARGIN_REASONS:
        return "margin_breach"
    if reason in VRP_REASONS:
        return "negative_vrp_drift"
    if reason in MANUAL_REASONS:
        return "manual_override"
    return "unknown"


def _channel_for_log(channel: Optional[str]) -> str:
    if channel in VALID_CHANNELS:
        return str(channel)
    return "internal"  # 'all' or unexpected → internal (channel CHECK constraint)


def fetch_pending(limit: int) -> List[Dict[str, Any]]:
    query = """
        SELECT sq.id, sq.product_id, p.sku, sq.asin, sq.channel,
               sq.suppression_reason, sq.target_price, sq.floor_price,
               sq.vrp_price, sq.margin_pct, sq.elasticity_score, sq.severity
        FROM suppression_queue sq
        JOIN products p ON p.id = sq.product_id
        WHERE sq.status = 'pending'
        ORDER BY sq.id ASC
        LIMIT %s
    """
    with db.get_cursor(commit=False) as cur:
        cur.execute(query, (limit,))
        return [dict(r) for r in cur.fetchall()]


def load_cost_breakdown(product_id: str) -> Optional[CostBreakdown]:
    """Latest effective product_costs row → CostBreakdown (COGS + variable costs)."""
    try:
        with db.get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT cogs_per_unit, packaging_cost, fulfillment_cost
                FROM product_costs
                WHERE product_id = %s
                  AND effective_to IS NULL
                ORDER BY effective_from DESC, created_at DESC
                LIMIT 1
                """,
                (str(product_id),),
            )
            row = cur.fetchone()
    except Exception as exc:
        log.error("load_cost_breakdown failed for %s: %s", product_id, exc)
        return None
    if row is None:
        return None
    return CostBreakdown(
        cogs_per_unit=_d(row["cogs_per_unit"]) or Decimal("0"),
        packaging_cost=_d(row["packaging_cost"]) or Decimal("0"),
        fulfillment_cost=_d(row["fulfillment_cost"]) or Decimal("0"),
        min_margin_amount=Decimal("0"),
    )


def load_category(product_id: str) -> Optional[str]:
    try:
        with db.get_cursor(commit=False) as cur:
            cur.execute("SELECT category FROM products WHERE id = %s", (str(product_id),))
            row = cur.fetchone()
            return row.get("category") if row else None
    except Exception:
        return None


def load_pricing_rule(sku: str, category: Optional[str]) -> PricingRule:
    """Resolve best active pricing rule (product > category > global)."""
    try:
        with db.get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT scope_type, scope_value, min_margin_pct, max_premium_pct,
                       vrp_drift_threshold_pct, elasticity_threshold,
                       suppression_enabled, auto_adjust_enabled
                FROM pricing_rules
                WHERE is_active = TRUE
                  AND (
                        (scope_type = 'product'  AND scope_value = %s)
                     OR (scope_type = 'category' AND scope_value = %s)
                     OR  scope_type = 'global'
                  )
                ORDER BY CASE scope_type WHEN 'product' THEN 0
                                         WHEN 'category' THEN 1 ELSE 2 END
                LIMIT 1
                """,
                (sku, category or ""),
            )
            row = cur.fetchone()
    except Exception as exc:
        log.error("load_pricing_rule failed for %s: %s", sku, exc)
        row = None
    if row is None:
        return PricingRule(min_margin_pct=DEFAULT_MIN_MARGIN_PCT)
    return PricingRule(
        min_margin_pct=_d(row["min_margin_pct"]) or DEFAULT_MIN_MARGIN_PCT,
        max_premium_pct=_d(row["max_premium_pct"]),
        vrp_drift_threshold_pct=_d(row["vrp_drift_threshold_pct"]) or DEFAULT_VRP_DRIFT_THRESHOLD_PCT,
        elasticity_threshold=_d(row["elasticity_threshold"]) or DEFAULT_ELASTICITY_THRESHOLD,
        suppression_enabled=bool(row["suppression_enabled"]),
        auto_adjust_enabled=bool(row.get("auto_adjust_enabled", False)),
    )


def evaluate_with_engine(
    *,
    product_id: str,
    sku: str,
    asin: str,
    category: str,
    current_price: Decimal,
    proposed_price: Optional[Decimal],
    vrp_price: Optional[Decimal],
    costs: CostBreakdown,
    rule: PricingRule,
    channel: str,
):
    engine = VRPGuardrailEngine(default_rule=rule)
    inp = PriceEvaluationInput(
        product_id=str(product_id),
        sku=sku or "",
        asin=asin or "",
        category=category or "",
        our_current_price=current_price,
        proposed_price=proposed_price,
        cogs_per_unit=costs.cogs_per_unit,
        packaging_cost=costs.packaging_cost,
        fulfillment_cost=costs.fulfillment_cost,
        shipping_cost=costs.shipping_cost,
        referral_fee=costs.referral_fee,
        fba_fee=costs.fba_fee,
        processing_fee=costs.processing_fee,
        vrp_price=vrp_price,
        min_margin_pct=rule.min_margin_pct,
        max_premium_pct=rule.max_premium_pct,
        vrp_drift_threshold_pct=rule.vrp_drift_threshold_pct,
        elasticity_threshold=rule.elasticity_threshold,
        suppression_enabled=rule.suppression_enabled,
        channel=channel,
    )
    return engine.evaluate(inp)


def compute_recovery(
    row: Dict[str, Any],
    costs: CostBreakdown,
    rule: PricingRule,
) -> Tuple[Optional[Decimal], Optional[Decimal], Optional[str], str, Optional[str]]:
    """Return (recovered_price, floor_price, change_type, action, skip_reason)."""
    reason = row.get("suppression_reason") or ""
    classification = classify(reason)
    our_price = _d(row.get("target_price"))
    vrp_price = _d(row.get("vrp_price"))

    # Floor: base_cost * (1 + MinMarginPct/100) via calculator helpers.
    base_cost = costs.base_cost()
    min_margin_amount = calculate_min_margin_amount(base_cost, rule.min_margin_pct)
    breakdown = calculate_floor_price(
        cogs_per_unit=costs.cogs_per_unit,
        packaging_cost=costs.packaging_cost,
        fulfillment_cost=costs.fulfillment_cost,
        shipping_cost=costs.shipping_cost,
        referral_fee=costs.referral_fee,
        fba_fee=costs.fba_fee,
        processing_fee=costs.processing_fee,
        min_margin_pct=rule.min_margin_pct,
    )
    floor = breakdown.floor_price()  # == base_cost + min_margin_amount == base_cost*(1+pct/100)
    _ = min_margin_amount  # explicitly derived (kept for parity with floor)

    if classification == "manual_override":
        return None, floor, None, "skip", "manual_override rows are never auto-adjusted"
    if classification == "unknown":
        return None, floor, None, "skip", f"unrecognized suppression_reason: {reason!r}"
    if our_price is None or our_price <= 0:
        return None, floor, None, "skip", "missing/invalid target_price"

    if classification == "margin_breach":
        # Minimum compliant price above the floor.
        recovered = _q2(floor)
        if recovered <= our_price:
            return None, floor, None, "skip", "target_price already at/above compliant floor"
        return recovered, floor, "floor_price_update", "adjust", None

    # negative_vrp_drift: move toward VRP to restore deal eligibility,
    # never below the minimum-margin floor.
    if vrp_price is None or vrp_price <= 0:
        return None, floor, None, "skip", "negative VRP drift but no vrp_price on row"
    recovered = _q2(max(vrp_price, floor))
    if recovered >= our_price:
        return None, floor, None, "skip", "VRP at/above current price; lowering cannot restore eligibility"
    return recovered, floor, "vrp_adjustment", "adjust", None


def process_row(row: Dict[str, Any], commit: bool) -> AdjustmentRecord:
    product_id = row["product_id"]
    sku = row.get("sku") or ""
    asin = row.get("asin") or ""
    channel = row.get("channel") or "all"
    reason = row.get("suppression_reason") or ""
    classification = classify(reason)
    our_price = _d(row.get("target_price"))
    vrp_price = _d(row.get("vrp_price"))
    elasticity = _d(row.get("elasticity_score"))

    category = load_category(product_id)
    costs = load_cost_breakdown(product_id)
    rule = load_pricing_rule(sku, category)
    log_channel = _channel_for_log(channel)

    if costs is None:
        return AdjustmentRecord(
            sku=sku, asin=asin, channel=log_channel, reason=reason,
            classification=classification, original_price=our_price,
            recovered_price=None, floor_price=None, vrp_price=vrp_price,
            margin_before=None, margin_after=None, elasticity_score=elasticity,
            change_type=None, action="skip", skip_reason="no effective product_costs row",
            metadata={"suppression_id": str(row.get("id")), "classification": classification},
        )
    base_cost = costs.base_cost()

    recovered, floor, change_type, action, skip_reason = compute_recovery(row, costs, rule)

    margin_before = margin_after = None
    engine_violations_before: List[str] = []
    try:
        if our_price is not None and our_price > 0:
            res_before = evaluate_with_engine(
                product_id=str(product_id), sku=sku, asin=asin, category=category or "",
                current_price=our_price, proposed_price=None,
                vrp_price=vrp_price, costs=costs, rule=rule, channel=log_channel,
            )
            margin_before = res_before.calculated_margin_pct
            engine_violations_before = list(res_before.violations)
        if action == "adjust" and recovered is not None:
            res_after = evaluate_with_engine(
                product_id=str(product_id), sku=sku, asin=asin, category=category or "",
                current_price=our_price or Decimal("0"), proposed_price=recovered,
                vrp_price=vrp_price, costs=costs, rule=rule, channel=log_channel,
            )
            margin_after = res_after.calculated_margin_pct
    except Exception as exc:
        log.warning("engine evaluation failed for %s: %s", sku, exc)
        if base_cost and our_price is not None:
            margin_before = _q2((our_price - base_cost) / base_cost * Decimal("100"))
        if base_cost and recovered is not None:
            margin_after = _q2((recovered - base_cost) / base_cost * Decimal("100"))

    metadata: Dict[str, Any] = {
        "suppression_id": str(row.get("id")),
        "channel": channel,
        "classification": classification,
        "original_reason": reason,
        "severity": row.get("severity"),
        "min_margin_pct": str(rule.min_margin_pct),
        "base_cost": str(base_cost),
        "recovery_formula": ("base_cost*(1+MinMarginPct/100)" if classification == "margin_breach"
                             else "max(vrp_price, floor_price)"),
        "engine_violations_before": engine_violations_before,
        "source": "src.engine.adjuster",
    }

    record = AdjustmentRecord(
        sku=sku, asin=asin, channel=log_channel, reason=reason,
        classification=classification, original_price=our_price,
        recovered_price=recovered, floor_price=floor, vrp_price=vrp_price,
        margin_before=margin_before, margin_after=margin_after,
        elasticity_score=elasticity, change_type=change_type,
        action=action, skip_reason=skip_reason, metadata=metadata,
    )

    if commit and action == "adjust" and recovered is not None:
        try:
            ok = db.log_price_change(
                sku=sku,
                change_type=change_type or "floor_price_update",
                old_price=float(our_price) if our_price is not None else None,
                new_price=float(recovered),
                floor_price=float(floor) if floor is not None else float(recovered),
                vrp_price=float(vrp_price) if vrp_price is not None else None,
                margin_before=float(margin_before) if margin_before is not None else None,
                margin_after=float(margin_after) if margin_after is not None else float(rule.min_margin_pct),
                elasticity_score=float(elasticity) if elasticity is not None else None,
                triggered_by="AUTO_RECOVERY",
                metadata=metadata,
            )
            record.metadata["logged"] = bool(ok)
            if not ok:
                record.action = "skip"
                record.skip_reason = "db.log_price_change returned False"
        except Exception as exc:
            record.metadata["persist_error"] = repr(exc)
            record.action = "skip"
            record.skip_reason = f"persist failed: {exc!r}"
    return record


def record_to_dict(r: AdjustmentRecord) -> Dict[str, Any]:
    def f(v: Optional[Decimal]) -> Optional[float]:
        return float(v) if v is not None else None
    return {
        "sku": r.sku,
        "asin": r.asin,
        "channel": r.channel,
        "reason": r.reason,
        "classification": r.classification,
        "action": r.action,
        "skip_reason": r.skip_reason,
        "change_type": r.change_type,
        "old_price": f(r.original_price),
        "new_price": f(r.recovered_price),
        "floor_price": f(r.floor_price),
        "vrp_price": f(r.vrp_price),
        "margin_before": f(r.margin_before),
        "margin_after": f(r.margin_after),
        "elasticity_score": f(r.elasticity_score),
        "logged": r.metadata.get("logged"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Auto-Adjustment & Price Correction Engine")
    parser.add_argument("--dry-run", action="store_true",
                        help="report only; no DB writes (default)")
    parser.add_argument("--commit", "--live", dest="commit", action="store_true",
                        help="live mode: persist price_change_log rows")
    parser.add_argument("--limit", type=int, default=MAX_ROWS_PER_RUN)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s engine.adjuster %(message)s",
        stream=sys.stderr,
    )

    mode = "commit" if args.commit else "dry-run"

    try:
        db.init_schema()
        db.test_connection()
        log.info("DB access verified (init_schema + test_connection)")
    except Exception as exc:
        log.error("DB access verification failed: %s", exc)
        print(json.dumps({"status": "db_unavailable", "mode": mode, "error": repr(exc)}))
        return 2

    try:
        rows = fetch_pending(args.limit)
    except Exception as exc:
        print(json.dumps({"status": "fetch_failed", "mode": mode, "error": repr(exc)}))
        return 3

    records: List[AdjustmentRecord] = []
    failures: List[Dict[str, Any]] = []
    for row in rows:
        try:
            records.append(process_row(row, commit=args.commit))
        except Exception as exc:
            log.exception("row %s failed: %s", row.get("id"), exc)
            failures.append({"id": str(row.get("id")), "sku": row.get("sku"), "error": repr(exc)})

    adjustments = [r for r in records if r.action == "adjust"]
    skips = [r for r in records if r.action == "skip"]
    report = {
        "status": "ok",
        "mode": mode,
        "pending_rows": len(rows),
        "adjustments": len(adjustments),
        "skipped": len(skips),
        "failed": len(failures),
        "writes": [record_to_dict(r) for r in adjustments],
        "skips": [record_to_dict(r) for r in skips],
        "failures": failures,
    }
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
