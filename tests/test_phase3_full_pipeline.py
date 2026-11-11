"""
Phase 3 Full-Fleet Regression & End-to-End Hardening Tests
==========================================================

Covers the complete Phase 3 lifecycle:

    Telemetry Ingestion -> Guardrail Evaluation -> Suppression Queue
        -> Auto-Adjuster -> Audit Log Entry

Asserts:
    - final_price >= floor_price across 100% of auto-adjusted events
    - zero regressions vs Phase 1 (exactly 39 tests) and Phase 2 ingestion tests

Conventions:
    - unittest.TestCase style, mirroring tests/test_engine.py
    - imports: src.engine.calculator, src.engine.guardrail, src.engine.suppression,
               src.engine.ingestion.db  (per Phase 3 Task 4 spec)
    - .env is NEVER read or written here; DB verified via db.init_schema() / db.test_connection()
      (db.py loads configuration itself — tests never touch the file)
    - synthetic DB rows are tagged PH3-TEST-* and removed in tearDown
      (products cascade to suppression_queue and product_costs)
    - price_change_log is an IMMUTABLE audit table (BEFORE UPDATE/DELETE trigger):
      it is NEVER written by these tests. The audit-log stage is verified by
      (a) the live lookup-miss path (expected False, zero rows inserted) and
      (b) a mocked cursor capturing the exact INSERT the live path would emit.

Live CHECK constraint (suppression_queue.suppression_reason) allows only:
    below_floor_price / vrp_violation / margin_breach / elasticity_breach / manual_override.
'vrp_drift_critical' (the SuppressionReason enum value) would violate it, so
DB-backed inserts use 'margin_breach'; the VRP_DRIFT_CRITICAL recovery path is
covered offline via adjuster.compute_recovery() where reason strings never hit the DB.

Run:
    python -m pytest tests/ -v      (from the project root)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase, main
from unittest.mock import patch

from src.engine import adjuster
from src.engine.calculator import (
    CostBreakdown,
    PricingRule,
    calculate_floor_price,
    calculate_min_margin_amount,
)
from src.engine.guardrail import (
    GuardrailStatus,
    PriceEvaluationInput,
    VRPGuardrailEngine,
)
from src.engine.suppression import (
    SuppressionReason,
    SuppressionRule,
    evaluate_and_enforce,
)
from src.ingestion import db
from src.ingestion import notifier

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Live CHECK-safe reason (see module docstring)
LIVE_REASON = SuppressionReason.MARGIN_BREACH.value  # "margin_breach"


def _unique_sku() -> str:
    return f"PH3-TEST-{uuid.uuid4().hex[:10].upper()}"


def _unique_event_id() -> str:
    return f"ph3-e2e-{uuid.uuid4().hex}"


def _make_costs(cogs: str = "100.00") -> CostBreakdown:
    return CostBreakdown(
        cogs_per_unit=Decimal(cogs),
        packaging_cost=Decimal("0.00"),
        fulfillment_cost=Decimal("0.00"),
        shipping_cost=Decimal("0.00"),
        referral_fee=Decimal("0.00"),
        fba_fee=Decimal("0.00"),
        processing_fee=Decimal("0.00"),
        min_margin_amount=Decimal("0"),
    )


def _make_rule(min_margin_pct: str = "20.0") -> PricingRule:
    return PricingRule(
        min_margin_pct=Decimal(min_margin_pct),
        max_premium_pct=Decimal("30.0"),
        vrp_drift_threshold_pct=Decimal("10.0"),
        elasticity_threshold=Decimal("0.5"),
        suppression_enabled=True,
        auto_adjust_enabled=True,
    )


def _floor_from_costs(costs: CostBreakdown, min_margin_pct: str = "20.0") -> Decimal:
    breakdown = calculate_floor_price(
        cogs_per_unit=costs.cogs_per_unit,
        packaging_cost=costs.packaging_cost,
        fulfillment_cost=costs.fulfillment_cost,
        shipping_cost=costs.shipping_cost,
        referral_fee=costs.referral_fee,
        fba_fee=costs.fba_fee,
        processing_fee=costs.processing_fee,
        min_margin_pct=Decimal(min_margin_pct),
    )
    return breakdown.floor_price()


def _recover_row(reason, target_price, cogs="100.00", min_margin_pct="20.0", vrp_price=None):
    costs = _make_costs(cogs)
    rule = _make_rule(min_margin_pct)
    row = {
        "product_id": "synthetic",
        "sku": "PH3-SYNTH",
        "asin": "B000000000",
        "channel": "amazon",
        "suppression_reason": reason,
        "target_price": float(Decimal(target_price)),
        "vrp_price": float(Decimal(vrp_price)) if vrp_price is not None else None,
    }
    floor = _floor_from_costs(costs, min_margin_pct)
    recovered, floor_out, change_type, action, skip_reason = adjuster.compute_recovery(row, costs, rule)
    return floor, recovered, floor_out, change_type, action, skip_reason


class DbBackedCase(TestCase):
    """DB-backed tests share a lifecycle inserting synthetic PH3-TEST-* rows
    and removing them in tearDown (products cascade)."""

    def setUp(self):
        db.init_schema()
        self._product_ids: list[str] = []
        self._rule_names: list[str] = []
        self._event_ids: list[str] = []

    def tearDown(self):
        try:
            if self._rule_names:
                with db.get_cursor() as cur:
                    cur.execute("DELETE FROM pricing_rules WHERE name = ANY(%s)", (self._rule_names,))
        except Exception as exc:
            print(f"[warn] pricing_rules cleanup failed: {exc}")
        try:
            if self._event_ids:
                with db.get_cursor() as cur:
                    cur.execute("DELETE FROM telemetry_raw WHERE event_id = ANY(%s)", (self._event_ids,))
        except Exception as exc:
            print(f"[warn] telemetry_raw cleanup failed: {exc}")
        try:
            if self._product_ids:
                with db.get_cursor() as cur:
                    # products cascade to suppression_queue and product_costs; delete by
                    # the unique PH3-TEST-% prefix (uuid=text ANY binding failed on this instance).
                    cur.execute("DELETE FROM products WHERE sku LIKE 'PH3-TEST-%'")
        except Exception as exc:
            print(f"[warn] products cleanup failed (rows may remain, tagged PH3-TEST-*): {exc}")

    def _insert_product(self, sku: str | None = None) -> tuple[str, str]:
        sku = sku or _unique_sku()
        asin = f"B{uuid.uuid4().hex[:9].upper()}"[:10]
        pid = str(uuid.uuid4())
        with db.get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO products (id, sku, asin, name, category, brand, is_active, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, true, NOW())
                """,
                (pid, sku, asin, f"Test Product {sku}", "Electronics", "Generic"),
            )
            cur.execute(
                """
                INSERT INTO product_costs (product_id, cogs_per_unit, packaging_cost, fulfillment_cost)
                VALUES (%s, %s, %s, %s)
                """,
                (pid, "100.00", "0.00", "0.00"),
            )
        rule_name = f"PH3-TEST-{sku}"
        with db.get_cursor() as cur:
            # Product-scope pricing rule -> deterministic min_margin_pct=20.0,
            # independent of any global/category rule already in the live DB.
            cur.execute(
                """
                INSERT INTO pricing_rules
                    (name, scope_type, scope_value, min_margin_pct, max_premium_pct,
                     vrp_drift_threshold_pct, elasticity_threshold,
                     suppression_enabled, auto_adjust_enabled, priority, is_active)
                VALUES (%s, 'product', %s, 20.0, 30.0, 10.0, 0.5, true, true, 0, true)
                """,
                (rule_name, sku),
            )
        self._product_ids.append(pid)
        self._rule_names.append(rule_name)
        return sku, pid

    def _add_suppression(self, sku, asin=None, reason=LIVE_REASON,
                         target_price=110.0, floor_price=120.0):
        return db.add_suppression(
            sku=sku,
            asin=asin or "B000000000",
            channel="amazon",
            reason=reason,
            target_price=target_price,
            floor_price=floor_price,
            vrp_price=None,
            margin_pct=10.0,
            elasticity_score=None,
            severity="high",
        )

    def _find_our_pending(self, sku) -> dict | None:
        rows = adjuster.fetch_pending(limit=200)
        return next((r for r in rows if r.get("sku") == sku), None)


# ===========================================================================
# 1. DB connection & schema (no writes)
# ===========================================================================

class TestDatabaseConnectionAndSchema(TestCase):
    def test_init_schema_verifies_tables(self):
        db.init_schema()

    def test_test_connection_returns_true(self):
        self.assertIs(db.test_connection(), True)


# ===========================================================================
# 2. Mathematical invariant: final_price >= floor_price (synthetic)
# ===========================================================================

class TestRecoveryInvariant(TestCase):
    """100% of auto-adjusted events must satisfy final_price >= floor_price."""

    # (reason, our_price, vrp)
    SCENARIOS = [
        ("margin_breach", "110.00", None),
        ("margin_breach", "50.00", None),
        ("margin_breach", "119.99", None),
        ("margin_breach", "120.01", None),   # skip: already at compliant floor
        ("below_floor_price", "115.00", None),
        ("below_floor_price", "80.00", None),
        ("elasticity_breach", "105.00", None),
        ("vrp_drift_critical", "150.00", "125.00"),
        ("vrp_drift_critical", "140.00", "121.00"),
        ("vrp_drift_critical", "200.00", "300.00"),  # skip: vrp >= current price
        ("vrp_drift_critical", "150.00", "100.00"),  # vrp < floor -> recovered == floor
        ("vrp_violation", "160.00", "130.00"),       # live DB enum value
        ("manual_override", "10.00", None),          # never auto-adjusted
        ("bogus_reason", "10.00", None),             # unknown -> skip
    ]

    def test_invariant_holds_for_all_scenarios(self):
        adjusted = skipped = 0
        for reason, our_price, vrp in self.SCENARIOS:
            floor, recovered, floor_out, change_type, action, skip_reason = _recover_row(
                reason, our_price, vrp_price=vrp,
            )
            self.assertEqual(floor_out, floor, "floor_price returned inconsistently")
            if action == "adjust":
                adjusted += 1
                self.assertIsNotNone(recovered)
                self.assertGreaterEqual(
                    recovered, floor_out,
                    f"INVARIANT VIOLATED reason={reason}: recovered {recovered} < floor {floor_out}",
                )
            else:
                skipped += 1
                self.assertEqual(action, "skip")
        self.assertGreater(adjusted, 0, "no adjustments exercised the invariant")
        self.assertGreater(skipped, 0, "no skip paths exercised")

    def test_floor_price_basic_invariants(self):
        breakdown = calculate_floor_price(
            cogs_per_unit=Decimal("100.00"), packaging_cost=Decimal("0.00"),
            fulfillment_cost=Decimal("0.00"), shipping_cost=Decimal("0.00"),
            referral_fee=Decimal("0.00"), fba_fee=Decimal("0.00"),
            processing_fee=Decimal("0.00"), min_margin_pct=Decimal("20.0"),
        )
        self.assertEqual(breakdown.base_cost(), Decimal("100.00"))
        self.assertEqual(breakdown.min_margin_amount, Decimal("20.00"))
        self.assertEqual(breakdown.floor_price(), Decimal("120.00"))
        self.assertEqual(calculate_min_margin_amount(Decimal("52.40"), Decimal("20.0")), Decimal("10.48"))

    def test_margin_breach_recovers_exactly_to_floor(self):
        floor, recovered, floor_out, change_type, action, _ = _recover_row("margin_breach", "110.00")
        self.assertEqual(action, "adjust")
        self.assertEqual(floor_out, floor)
        self.assertEqual(recovered, floor)
        self.assertEqual(change_type, "floor_price_update")

    def test_vrp_drift_never_recovers_below_floor(self):
        floor, recovered, floor_out, change_type, action, _ = _recover_row(
            "vrp_drift_critical", "150.00", vrp_price="125.00")
        self.assertEqual(action, "adjust")
        self.assertGreaterEqual(recovered, floor_out)
        self.assertEqual(change_type, "vrp_adjustment")

        floor, recovered, floor_out, change_type, action, _ = _recover_row(
            "vrp_drift_critical", "150.00", vrp_price="100.00")
        self.assertEqual(action, "adjust")
        self.assertEqual(recovered, floor_out)


# ===========================================================================
# 3. Guardrail -> Suppression chain (offline)
# ===========================================================================

class TestGuardrailToSuppressionChain(TestCase):
    def _input(self, **overrides) -> PriceEvaluationInput:
        defaults = dict(
            product_id="PH3-CHAIN",
            sku="PH3-CHAIN",
            asin="B000000000",
            category="Electronics",
            our_current_price=Decimal("40.00"),  # below floor (62.88)
            cogs_per_unit=Decimal("28.50"),
            packaging_cost=Decimal("1.20"),
            fulfillment_cost=Decimal("3.50"),
            shipping_cost=Decimal("4.20"),
            referral_fee=Decimal("8.00"),
            fba_fee=Decimal("4.50"),
            processing_fee=Decimal("2.50"),
            min_margin_pct=Decimal("20.0"),
            vrp_drift_threshold_pct=Decimal("5.0"),
        )
        defaults.update(overrides)
        return PriceEvaluationInput(**defaults)

    def test_below_floor_evaluation_triggers_suppression(self):
        rule = SuppressionRule(
            suppress_on_below_floor=True,
            suppress_on_margin_breach=True,
            suppress_on_vrp_drift_critical=True,
            vrp_drift_critical_pct=Decimal("10.0"),
        )
        result, suppressions, _ = evaluate_and_enforce(self._input(), rule)
        self.assertEqual(result.status, GuardrailStatus.BREACH)
        self.assertTrue(result.suppression_triggered)
        self.assertIn(SuppressionReason.BELOW_FLOOR_PRICE, {s.reason for s in suppressions})
        for s in suppressions:
            self.assertTrue(s.is_active())

    def test_recovery_of_suppressed_below_floor_price_stays_above_floor(self):
        inp = self._input()
        result = VRPGuardrailEngine().evaluate(inp)
        self.assertEqual(result.status, GuardrailStatus.BREACH)
        row = {
            "product_id": "PH3-CHAIN",
            "sku": "PH3-CHAIN",
            "asin": "B000000000",
            "channel": "amazon",
            "suppression_reason": SuppressionReason.BELOW_FLOOR_PRICE.value,
            "target_price": float(inp.our_current_price),
            "vrp_price": None,
        }
        costs = CostBreakdown(
            cogs_per_unit=Decimal("28.50"),
            packaging_cost=Decimal("1.20"),
            fulfillment_cost=Decimal("3.50"),
            shipping_cost=Decimal("4.20"),
            referral_fee=Decimal("8.00"),
            fba_fee=Decimal("4.50"),
            processing_fee=Decimal("2.50"),
        )
        recovered, floor_out, change_type, action, _ = adjuster.compute_recovery(row, costs, _make_rule("20.0"))
        self.assertEqual(action, "adjust")
        self.assertGreaterEqual(recovered, floor_out)
        self.assertEqual(floor_out, Decimal("62.88"))

    def test_healthy_price_does_not_suppress(self):
        result = VRPGuardrailEngine().evaluate(self._input(
            our_current_price=Decimal("79.99"), vrp_price=Decimal("79.99"),
        ))
        self.assertEqual(result.status, GuardrailStatus.HEALTHY)
        self.assertFalse(result.suppression_triggered)


# ===========================================================================
# 4. Adjuster classification (offline)
# ===========================================================================

class TestAdjusterClassification(TestCase):
    def test_classify_margin_breach(self):
        self.assertEqual(adjuster.classify(SuppressionReason.MARGIN_BREACH.value), "margin_breach")

    def test_classify_below_floor_price(self):
        self.assertEqual(adjuster.classify(SuppressionReason.BELOW_FLOOR_PRICE.value), "margin_breach")

    def test_classify_elasticity_breach(self):
        self.assertEqual(adjuster.classify(SuppressionReason.ELASTICITY_BREACH.value), "margin_breach")

    def test_classify_vrp_drift_critical_and_live_value(self):
        self.assertEqual(adjuster.classify(SuppressionReason.VRP_DRIFT_CRITICAL.value), "negative_vrp_drift")
        self.assertEqual(adjuster.classify("vrp_violation"), "negative_vrp_drift")

    def test_classify_manual_override_and_unknown(self):
        self.assertEqual(adjuster.classify(SuppressionReason.MANUAL_OVERRIDE.value), "manual_override")
        self.assertEqual(adjuster.classify("bogus_reason"), "unknown")


# ===========================================================================
# 5. Notifier alert path (offline)
# ===========================================================================

class TestNotifierAlertPath(TestCase):
    def test_build_alert_payload_has_all_fields(self):
        payload = notifier.build_alert_payload(
            sku="PH3-TEST-001", product_name="Test Widget",
            breach_type="MARGIN_BREACH", attempted_price=110.00,
            floor_price=120.00, projected_margin_loss=10.00,
            timestamp="2026-11-11T00:00:00Z",
        )
        self.assertEqual(payload["sku"], "PH3-TEST-001")
        self.assertEqual(payload["product_name"], "Test Widget")
        self.assertEqual(payload["breach_type"], "MARGIN_BREACH")
        self.assertEqual(payload["attempted_price"], 110.00)
        self.assertEqual(payload["floor_price"], 120.00)
        self.assertEqual(payload["projected_margin_loss"], 10.00)
        self.assertEqual(payload["timestamp"], "2026-11-11T00:00:00Z")

    def test_build_alert_payload_default_timestamp_ends_with_z(self):
        payload = notifier.build_alert_payload(
            sku="PH3-TEST-001", product_name="Test Widget",
            breach_type="VRP_DRIFT_CRITICAL", attempted_price=150.00,
            floor_price=120.00, projected_margin_loss=30.00,
        )
        self.assertTrue(payload["timestamp"].endswith("Z"))

    def test_dispatch_webhook_without_url_is_noop_true(self):
        payload = notifier.build_alert_payload(
            sku="PH3-TEST-001", product_name="Test Widget",
            breach_type="MARGIN_BREACH", attempted_price=110.00,
            floor_price=120.00, projected_margin_loss=10.00,
        )
        env = {k: v for k, v in os.environ.items()
               if k not in ("NOTIFICATION_WEBHOOK_URL", "SLACK_WEBHOOK_URL")}
        with patch.dict(os.environ, env, clear=True):
            self.assertIs(notifier.dispatch_webhook(payload), True)

    def test_resolve_alert_without_db_id_returns_true(self):
        alert = {"sku": "PH3-TEST-001", "db_status": "pending", "db_id": None}
        self.assertIs(notifier.resolve_alert(db=None, alert=alert, notification_status="NOTIFIED"), True)


# ===========================================================================
# 6. Audit log stage (IMMUTABLE table: verified without writes)
# ===========================================================================

class TestAuditLogStage(TestCase):
    def test_unknown_sku_returns_false_and_writes_nothing(self):
        result = db.log_price_change(
            sku="PH3-NO-SUCH-SKU-DOES-NOT-EXIST",
            change_type="floor_price_update",
            old_price=None,
            new_price=1.0,
            floor_price=1.0,
            vrp_price=None,
            margin_before=None,
            margin_after=1.0,
            elasticity_score=None,
            triggered_by="AUTO_RECOVERY",
            metadata={"source": "phase3_test"},
        )
        self.assertFalse(result)

    def test_log_price_change_emits_expected_insert(self):
        """Capture the exact INSERT the live path would issue (mocked cursor)."""
        captured: list = []

        class _Cursor:
            def execute(self, sql, params=None):
                captured.append((sql, params))

        class _Ctx:
            def __enter__(self):
                return _Cursor()

            def __exit__(self, *exc):
                return False

        def fake_get_cursor(commit: bool = True):
            return _Ctx()

        with patch.object(db, "get_product_id_by_sku",
                          return_value="11111111-1111-1111-1111-111111111111"), \
             patch.object(db, "get_cursor", fake_get_cursor):
            ok = db.log_price_change(
                sku="ELEC-001",
                change_type="floor_price_update",
                old_price=110.0,
                new_price=120.0,
                floor_price=120.0,
                vrp_price=None,
                margin_before=10.0,
                margin_after=20.0,
                elasticity_score=None,
                triggered_by="AUTO_RECOVERY",
                metadata={"source": "phase3_test"},
            )

        self.assertIs(ok, True)
        self.assertEqual(len(captured), 1, "exactly one INSERT must be issued")
        sql, params = captured[0]
        self.assertIn("INSERT INTO price_change_log", sql)
        self.assertEqual(params[1], "floor_price_update")
        self.assertEqual(params[3], 120.0)   # new_price
        self.assertEqual(params[4], 120.0)   # floor_price
        self.assertEqual(params[9], "AUTO_RECOVERY")
        self.assertEqual(json.loads(params[10])["source"], "phase3_test")


# ===========================================================================
# 7. DB-backed: telemetry -> suppression queue
# ===========================================================================

class TestIngestionToSuppression(DbBackedCase):
    def test_add_suppression_unknown_product_returns_false(self):
        result = self._add_suppression(f"PH3-NOEXIST-{uuid.uuid4().hex[:8].upper()}")
        self.assertFalse(result)

    def test_add_suppression_with_product_returns_true_and_queues(self):
        sku, _ = self._insert_product()
        result = self._add_suppression(sku, asin="B000000001")
        self.assertTrue(result)
        self.assertIn(sku, db.get_suppressed_skus())

    def test_get_suppressed_skus_is_list(self):
        self.assertIsInstance(db.get_suppressed_skus(), list)


# ===========================================================================
# 8. DB-backed: suppression queue -> auto-adjuster recovery
# ===========================================================================

class TestAdjusterDbIntegration(DbBackedCase):
    def test_load_cost_breakdown_returns_costs(self):
        sku, pid = self._insert_product()
        costs = adjuster.load_cost_breakdown(pid)
        self.assertIsNotNone(costs)
        self.assertEqual(costs.cogs_per_unit, Decimal("100.00"))
        self.assertEqual(costs.base_cost(), Decimal("100.00"))

    def test_load_pricing_rule_prefers_product_scope(self):
        sku, _ = self._insert_product()
        rule = adjuster.load_pricing_rule(sku, "Electronics")
        self.assertEqual(rule.min_margin_pct, Decimal("20.0"))
        self.assertTrue(rule.auto_adjust_enabled)

    def test_fetch_pending_finds_our_row(self):
        sku, _ = self._insert_product()
        self.assertTrue(self._add_suppression(sku))
        our = self._find_our_pending(sku)
        self.assertIsNotNone(our, f"{sku} not found among pending rows")
        self.assertEqual(our["sku"], sku)
        self.assertEqual(our["suppression_reason"], LIVE_REASON)

    def test_db_backed_recovery_invariant(self):
        sku, _ = self._insert_product()
        self.assertTrue(self._add_suppression(sku, target_price=110.0, floor_price=120.0))
        our = self._find_our_pending(sku)
        self.assertIsNotNone(our)
        costs = adjuster.load_cost_breakdown(our["product_id"])
        rule = adjuster.load_pricing_rule(sku, "Electronics")
        recovered, floor_out, change_type, action, skip_reason = adjuster.compute_recovery(our, costs, rule)
        self.assertEqual(action, "adjust", f"expected adjust, got {action} ({skip_reason})")
        self.assertGreaterEqual(recovered, floor_out,
                                f"INVARIANT VIOLATED: {recovered} < {floor_out}")
        self.assertEqual(floor_out, Decimal("120.00"))
        self.assertEqual(change_type, "floor_price_update")

    def test_process_row_dry_run_respects_invariant_and_writes_nothing(self):
        sku, _ = self._insert_product()
        self.assertTrue(self._add_suppression(sku, target_price=110.0, floor_price=120.0))
        our = self._find_our_pending(sku)
        self.assertIsNotNone(our)
        record = adjuster.process_row(our, commit=False)
        self.assertIn(record.action, ("adjust", "skip"))
        if record.action == "adjust":
            self.assertIsNotNone(record.recovered_price)
            self.assertGreaterEqual(record.recovered_price, record.floor_price)
        # dry-run must NOT flip the suppression row out of 'pending'
        with db.get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT sq.status FROM suppression_queue sq
                JOIN products p ON p.id = sq.product_id
                WHERE p.sku = %s
                """,
                (sku,),
            )
            row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "pending")


# ===========================================================================
# 9. DB-backed: full lifecycle E2E
# ===========================================================================

class TestFullPipelineE2E(DbBackedCase):
    """Telemetry -> Guardrail -> Suppression Queue -> Auto-Adjuster -> Audit Log."""

    def test_full_pipeline_invariant(self):
        sku, pid = self._insert_product()

        # Stage 1: Telemetry ingestion
        event_id = _unique_event_id()
        event = db.TelemetryEvent(
            event_id=event_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            product_id=sku,
            sku=sku,
            asin="B000000000",
            category="Electronics",
            channel="amazon",
            event_type="price_check",
            scenario="competitor_undercut",
            our_price=110.0,
            floor_price=120.0,
            vrp_price=None,
            lowest_competitor_price=105.0,
            cogs=100.0,
            min_margin=20.0,
            guardrail_status="suppressed",
            suppression_triggered=True,
            suppression_reason=LIVE_REASON,
        )
        self._event_ids.append(event_id)
        self.assertIs(db.persist_event(event), True,
                      "telemetry_raw rejected the event insert")

        # Stage 2: Guardrail evaluation (offline re-evaluation of the same input)
        evaluation = VRPGuardrailEngine().evaluate(PriceEvaluationInput(
            product_id=str(pid),
            sku=sku,
            asin="B000000000",
            category="Electronics",
            our_current_price=Decimal("110.00"),
            cogs_per_unit=Decimal("100.00"),
            min_margin_pct=Decimal("20.0"),
        ))
        self.assertEqual(evaluation.status, GuardrailStatus.BREACH)
        self.assertTrue(evaluation.suppression_triggered)

        # Stage 3: Suppression queue
        self.assertTrue(self._add_suppression(sku, asin="B000000000",
                                               target_price=110.0, floor_price=120.0))
        with db.get_cursor(commit=False) as cur:
            cur.execute(
                """
                SELECT sq.status, sq.suppression_reason, sq.severity
                FROM suppression_queue sq JOIN products p ON p.id = sq.product_id
                WHERE p.sku = %s
                """,
                (sku,),
            )
            row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["suppression_reason"], LIVE_REASON)
        self.assertEqual(row["severity"], "high")

        # Stage 4: Auto-adjuster recovery
        our = self._find_our_pending(sku)
        self.assertIsNotNone(our, "queued row not visible to adjuster.fetch_pending")
        costs = adjuster.load_cost_breakdown(our["product_id"])
        self.assertIsNotNone(costs)
        rule = adjuster.load_pricing_rule(sku, "Electronics")
        recovered, floor_out, change_type, action, skip_reason = adjuster.compute_recovery(our, costs, rule)
        self.assertEqual(action, "adjust", f"expected adjust, got {action} ({skip_reason})")
        self.assertGreaterEqual(recovered, floor_out,
                                f"E2E INVARIANT VIOLATED: {recovered} < {floor_out}")
        self.assertEqual(floor_out, Decimal("120.00"))
        record = adjuster.process_row(our, commit=False)
        if record.action == "adjust":
            self.assertGreaterEqual(record.recovered_price, record.floor_price)

        # Stage 5: Audit log entry — verified WITHOUT writing (immutable table).
        # Live lookup-miss must return False and leave the table untouched.
        self.assertFalse(db.log_price_change(
            sku="PH3-NO-SUCH-SKU-DOES-NOT-EXIST",
            change_type=change_type or "floor_price_update",
            old_price=110.0,
            new_price=float(recovered),
            floor_price=float(floor_out),
            vrp_price=None,
            margin_before=10.0,
            margin_after=20.0,
            elasticity_score=None,
            triggered_by="AUTO_RECOVERY",
            metadata={"source": "phase3_e2e_test"},
        ))
        self.assertTrue(callable(db.log_price_change))


# ===========================================================================
# 10. Regression gates
# ===========================================================================

class TestPhaseOneSuiteIntegrity(TestCase):
    def test_phase1_suite_still_has_39_tests(self):
        with open(os.path.join(PROJECT_ROOT, "tests", "test_engine.py"), encoding="utf-8") as fh:
            content = fh.read()
        count = len(re.findall(r"def test_", content))
        self.assertEqual(count, 39, f"Phase 1 suite drifted: expected 39 tests, found {count}")


class TestNoRegressions(TestCase):
    def test_full_suite_excluding_this_file_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q",
             "--ignore=tests/test_phase3_full_pipeline.py"],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
            timeout=600,
        )
        print(result.stdout)
        if result.returncode != 0:
            print(result.stderr)
        self.assertEqual(result.returncode, 0,
                         "Phase 1 + Phase 2 regression gate failed:\n" + result.stdout + result.stderr)


if __name__ == "__main__":
    main(verbosity=2)
