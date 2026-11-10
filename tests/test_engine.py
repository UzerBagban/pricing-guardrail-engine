"""
Unit tests for Pricing Guardrail Engine
Tests boundary conditions and margin defenses.
"""

import unittest
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
import json

from src.engine.calculator import (
    CostComponent,
    CostBreakdown,
    PricingRule,
    calculate_floor_price,
    calculate_min_margin_amount,
    quick_floor_price,
)
from src.engine.guardrail import (
    PriceEvaluationInput,
    PriceEvaluationResult,
    VRPGuardrailEngine,
    GuardrailStatus,
    RecommendedAction,
    DriftType,
    quick_evaluate,
    evaluate_price_batch,
)
from src.engine.suppression import (
    SuppressionEvent,
    SuppressionEngine,
    SuppressionRule,
    SuppressionReason,
    SuppressionStatus,
    SuppressionScope,
    evaluate_and_enforce,
    is_price_suppressed,
)


class TestCalculator(unittest.TestCase):
    """Tests for floor price calculator."""

    def setUp(self):
        self.standard_costs = {
            "cogs_per_unit": Decimal("28.50"),
            "packaging_cost": Decimal("1.20"),
            "fulfillment_cost": Decimal("3.50"),
            "shipping_cost": Decimal("4.20"),
            "referral_fee": Decimal("8.00"),
            "fba_fee": Decimal("4.50"),
            "processing_fee": Decimal("2.50"),
            "min_margin_pct": Decimal("20.0"),
        }

    def test_floor_price_calculation(self):
        """Test basic floor price formula: COGS + Shipping + VariableFees + MinMargin"""
        result = calculate_floor_price(**self.standard_costs)

        # Variable costs = 1.20 + 3.50 + 4.20 + 8.00 + 4.50 + 2.50 = 23.90
        expected_variable = Decimal("23.90")
        self.assertEqual(result.total_variable_costs(), expected_variable)

        # Base cost = 28.50 + 23.90 = 52.40
        expected_base = Decimal("52.40")
        self.assertEqual(result.base_cost(), expected_base)

        # Min margin = 52.40 * 20% = 10.48
        expected_margin = Decimal("10.48")
        self.assertEqual(result.min_margin_amount, expected_margin)

        # Floor = 52.40 + 10.48 = 62.88
        expected_floor = Decimal("62.88")
        self.assertEqual(result.floor_price(), expected_floor)

    def test_floor_price_zero_costs(self):
        """Test floor price with zero variable costs."""
        costs = self.standard_costs.copy()
        for k in ["packaging_cost", "fulfillment_cost", "shipping_cost", "referral_fee", "fba_fee", "processing_fee"]:
            costs[k] = Decimal("0")
        costs["min_margin_pct"] = Decimal("0")

        result = calculate_floor_price(**costs)
        self.assertEqual(result.floor_price(), Decimal("28.50"))  # Just COGS

    def test_floor_price_high_margin(self):
        """Test floor price with high margin requirement."""
        costs = self.standard_costs.copy()
        costs["min_margin_pct"] = Decimal("50.0")

        result = calculate_floor_price(**costs)
        # Base 52.40 * 1.5 = 78.60
        self.assertEqual(result.floor_price(), Decimal("78.60"))

    def test_calculate_min_margin_amount(self):
        """Test standalone min margin calculation."""
        margin = calculate_min_margin_amount(Decimal("100"), Decimal("25.0"))
        self.assertEqual(margin, Decimal("25.00"))

        margin = calculate_min_margin_amount(Decimal("52.40"), Decimal("20.0"))
        self.assertEqual(margin, Decimal("10.48"))

    def test_decimal_precision_rounding(self):
        """Test Decimal precision with ROUND_HALF_UP."""
        # Case that would produce more than 2 decimal places
        costs = self.standard_costs.copy()
        costs["cogs_per_unit"] = Decimal("10.00")
        costs["min_margin_pct"] = Decimal("33.33")  # 10 * 1.3333 = 13.333...
        # variable = 23.90, base = 33.90, margin = 33.90 * 0.3333 = 11.299...
        result = calculate_floor_price(**costs)
        # 33.90 + 11.30 = 45.20 (rounded half up)
        self.assertEqual(result.floor_price(), Decimal("45.20"))

    def test_quick_floor_price(self):
        """Test quick floor price helper."""
        floor = quick_floor_price(20.0, 3.0, 10.0, 15.0)
        # COGS=20, shipping=3, fees=10 -> base=33, margin=4.95, floor=37.95
        self.assertAlmostEqual(floor, 37.95, places=2)

    def test_calculate_margin_at_price(self):
        """Test margin calculation at given price using base cost markup formula."""
        result = calculate_floor_price(**self.standard_costs)
        # standard_costs: COGS=28.50, variable=23.90, base=52.40, floor=62.88, min_margin=20%

        margin = result.margin_at_price(Decimal("100"))
        # markup on base cost: (100 - 52.40) / 52.40 * 100 = 90.84%
        self.assertAlmostEqual(float(margin), 90.84, places=2)

        margin = result.margin_at_price(Decimal("0"))
        self.assertEqual(margin, Decimal("0"))

    def test_validate_margin(self):
        """Test margin validation against floor."""
        result = calculate_floor_price(**self.standard_costs)

        # Price at floor = valid (margin >= min_margin_pct)
        self.assertGreaterEqual(result.margin_at_price(Decimal("62.88")), Decimal("20.0"))

        # Price above floor = valid
        self.assertGreaterEqual(result.margin_at_price(Decimal("70.00")), Decimal("20.0"))

        # Price below floor = invalid (margin < min_margin_pct)
        self.assertLess(result.margin_at_price(Decimal("50.00")), Decimal("20.0"))


class TestGuardrailEngine(unittest.TestCase):
    """Tests for VRP Guardrail Engine."""

    def setUp(self):
        self.engine = VRPGuardrailEngine()

    def create_basic_input(self, **overrides) -> PriceEvaluationInput:
        """Create a basic evaluation input with defaults."""
        defaults = {
            "product_id": "prod-001",
            "sku": "TEST-001",
            "asin": "B000000001",
            "category": "Electronics",
            "our_current_price": Decimal("79.99"),
            "cogs_per_unit": Decimal("28.50"),
            "packaging_cost": Decimal("1.20"),
            "fulfillment_cost": Decimal("3.50"),
            "shipping_cost": Decimal("4.20"),
            "referral_fee": Decimal("8.00"),
            "fba_fee": Decimal("4.50"),
            "processing_fee": Decimal("2.50"),
            "min_margin_pct": Decimal("20.0"),
            "vrp_drift_threshold_pct": Decimal("5.0"),
        }
        defaults.update(overrides)
        return PriceEvaluationInput(**defaults)

    def test_healthy_price_at_vrp(self):
        """Test price at VRP with healthy margin."""
        input_data = self.create_basic_input(
            vrp_price=Decimal("79.99"),
            buybox_price=Decimal("85.00"),
            lowest_competitor_price=Decimal("85.00"),
        )

        result = self.engine.evaluate(input_data)

        self.assertEqual(result.status, GuardrailStatus.HEALTHY)
        self.assertEqual(result.recommended_action, RecommendedAction.HOLD)
        self.assertFalse(result.suppression_triggered)
        # Floor should be ~62.88
        self.assertAlmostEqual(float(result.floor_price), 62.88, places=1)

    def test_below_floor_price_triggers_breach(self):
        """Test price below floor triggers BREACH and suppression."""
        input_data = self.create_basic_input(
            our_current_price=Decimal("40.00"),
            vrp_price=Decimal("79.99"),
        )

        result = self.engine.evaluate(input_data)

        self.assertEqual(result.status, GuardrailStatus.BREACH)
        self.assertIn("below_floor_price", result.violations)
        self.assertTrue(result.suppression_triggered)
        self.assertEqual(result.recommended_action, RecommendedAction.SUPPRESS)
        self.assertEqual(result.recommended_price, result.floor_price)

    def test_margin_below_minimum_triggers_breach(self):
        """Test price at floor has margin equal to min_margin (edge case)."""
        # With multiplicative formula, floor = base * (1 + min_margin/100)
        # At floor price, margin on base cost = min_margin_pct exactly
        input_data = self.create_basic_input(
            our_current_price=Decimal("62.88"),  # Exactly at floor
            min_margin_pct=Decimal("20.0"),
        )

        result = self.engine.evaluate(input_data)

        # At floor price, calculated margin = 20% exactly (not < 20%)
        # So status should be HEALTHY (not BREACH)
        # Below floor check: 62.88 >= 62.88 (not below floor)
        self.assertEqual(result.status, GuardrailStatus.HEALTHY)
        self.assertNotIn("margin_below_minimum", result.violations)
        self.assertFalse(result.suppression_triggered)

        # Price slightly below floor triggers BREACH
        input_data2 = self.create_basic_input(
            our_current_price=Decimal("62.87"),  # Just below floor
            vrp_price=Decimal("79.99"),
            min_margin_pct=Decimal("20.0"),
        )

        result2 = self.engine.evaluate(input_data2)
        self.assertEqual(result2.status, GuardrailStatus.BREACH)
        self.assertIn("below_floor_price", result2.violations)
        self.assertTrue(result2.suppression_triggered)

    def test_vrp_drop_above_threshold_warning(self):
        """Test VRP drop >5% triggers WARNING."""
        input_data = self.create_basic_input(
            our_current_price=Decimal("79.99"),
            vrp_price=Decimal("72.00"),  # ~10% drop
            vrp_drift_threshold_pct=Decimal("5.0"),
        )

        result = self.engine.evaluate(input_data)

        self.assertTrue(result.vrp_drift_detected)
        self.assertIsNotNone(result.vrp_drift_pct)
        self.assertLess(result.vrp_drift_pct, Decimal("-5.0"))
        self.assertEqual(result.status, GuardrailStatus.WARNING)
        self.assertEqual(result.recommended_action, RecommendedAction.DECREASE_PRICE)
        self.assertIsNotNone(result.recommended_price)

    def test_vrp_drop_below_threshold_healthy(self):
        """Test VRP drop <5% stays HEALTHY."""
        input_data = self.create_basic_input(
            our_current_price=Decimal("79.99"),
            vrp_price=Decimal("77.00"),  # ~3.7% drop
            vrp_drift_threshold_pct=Decimal("5.0"),
        )

        result = self.engine.evaluate(input_data)

        self.assertFalse(result.vrp_drift_detected)
        self.assertEqual(result.status, GuardrailStatus.HEALTHY)

    def test_vrp_spike_above_threshold_warning(self):
        """Test VRP spike >5% triggers WARNING."""
        input_data = self.create_basic_input(
            our_current_price=Decimal("79.99"),
            vrp_price=Decimal("88.00"),  # ~10% spike
            vrp_drift_threshold_pct=Decimal("5.0"),
        )

        result = self.engine.evaluate(input_data)

        self.assertTrue(result.vrp_drift_detected)
        self.assertGreater(result.vrp_drift_pct, Decimal("5.0"))
        self.assertEqual(result.status, GuardrailStatus.WARNING)
        self.assertEqual(result.recommended_action, RecommendedAction.INCREASE_PRICE)

    def test_competitor_undercut_warning(self):
        """Test competitor undercut triggers warning."""
        input_data = self.create_basic_input(
            our_current_price=Decimal("85.00"),
            vrp_price=Decimal("79.99"),
            lowest_competitor_price=Decimal("75.00"),
            buybox_price=Decimal("75.00"),
        )

        result = self.engine.evaluate(input_data)

        self.assertIn("buybox_lost", result.violations) or any("competitor" in w.lower() for w in result.warnings)
        self.assertEqual(result.status, GuardrailStatus.WARNING)

    def test_elasticity_breach_warning(self):
        """Test elasticity breach triggers WARNING."""
        input_data = self.create_basic_input(
            our_current_price=Decimal("79.99"),
            vrp_price=Decimal("79.99"),
            base_price=Decimal("79.99"),
            base_velocity=Decimal("100"),
            current_velocity=Decimal("50"),  # 50% drop
            elasticity_threshold=Decimal("2.5"),
        )

        result = self.engine.evaluate(input_data)

        # Price change = 0%, velocity change = -50%
        # Elasticity would be very high (division by near-zero)
        self.assertTrue(result.elasticity_breach or "elasticity_breach" in result.violations)

    def test_proposed_price_evaluation(self):
        """Test evaluating a proposed price change."""
        input_data = self.create_basic_input(
            our_current_price=Decimal("79.99"),
            proposed_price=Decimal("50.00"),  # Proposed drop
            vrp_price=Decimal("79.99"),
        )

        result = self.engine.evaluate(input_data)

        # Should evaluate against proposed_price
        self.assertEqual(result.proposed_price, Decimal("50.00"))
        self.assertEqual(result.status, GuardrailStatus.BREACH)
        self.assertIn("below_floor_price", result.violations)

    def test_suppression_disabled_downgrades_to_warning(self):
        """Test suppression_enabled=False downgrades BREACH to WARNING."""
        input_data = self.create_basic_input(
            our_current_price=Decimal("40.00"),
            vrp_price=Decimal("79.99"),
            suppression_enabled=False,
        )

        result = self.engine.evaluate(input_data)

        self.assertEqual(result.status, GuardrailStatus.WARNING)
        self.assertFalse(result.suppression_triggered)
        self.assertIsNone(result.suppression_reason)

    def test_quick_evaluate_helper(self):
        """Test quick_evaluate helper function."""
        # Healthy
        result = quick_evaluate(50.0, 45.0, vrp_price=48.0, vrp_threshold=5.0)
        self.assertEqual(result["status"], "healthy")

        # Below floor
        result = quick_evaluate(40.0, 45.0)
        self.assertEqual(result["status"], "breach")
        self.assertTrue(result["suppression_triggered"])

        # VRP drop
        result = quick_evaluate(50.0, 45.0, vrp_price=44.0, vrp_threshold=5.0)  # 12% drop
        self.assertEqual(result["status"], "warning")

    def test_evaluate_price_batch(self):
        """Test batch evaluation."""
        inputs = [
            self.create_basic_input(product_id=f"prod-{i}", sku=f"SKU-{i}", our_current_price=Decimal("79.99"))
            for i in range(3)
        ]
        inputs[1].our_current_price = Decimal("40.00")  # Below floor
        inputs[2].our_current_price = Decimal("79.99")
        inputs[2].vrp_price = Decimal("72.00")  # VRP drop

        results = evaluate_price_batch(inputs)

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].status, GuardrailStatus.HEALTHY)
        self.assertEqual(results[1].status, GuardrailStatus.BREACH)
        self.assertEqual(results[2].status, GuardrailStatus.WARNING)

    def test_result_to_dict_serialization(self):
        """Test result serialization to dict."""
        input_data = self.create_basic_input()
        result = self.engine.evaluate(input_data)

        d = result.to_dict()

        self.assertIn("evaluation_id", d)
        self.assertIn("floor_price", d)
        self.assertIsInstance(d["floor_price"], float)
        self.assertIn("status", d)
        self.assertEqual(d["status"], "healthy")
        self.assertIn("violations", d)
        self.assertIsInstance(d["violations"], list)


class TestSuppressionEngine(unittest.TestCase):
    """Tests for Suppression Engine."""

    def setUp(self):
        self.rule = SuppressionRule(
            suppress_on_below_floor=True,
            suppress_on_margin_breach=True,
            suppress_on_vrp_drift_critical=True,
            vrp_drift_critical_pct=Decimal("10.0"),
            default_duration_hours=24,
        )
        self.engine = SuppressionEngine(self.rule)

    def create_suppression_input(self, **overrides) -> PriceEvaluationInput:
        """Create input for suppression testing."""
        defaults = {
            "product_id": "prod-001",
            "sku": "TEST-001",
            "asin": "B000000001",
            "category": "Electronics",
            "our_current_price": Decimal("79.99"),
            "cogs_per_unit": Decimal("28.50"),
            "packaging_cost": Decimal("1.20"),
            "fulfillment_cost": Decimal("3.50"),
            "shipping_cost": Decimal("4.20"),
            "referral_fee": Decimal("8.00"),
            "fba_fee": Decimal("4.50"),
            "processing_fee": Decimal("2.50"),
            "min_margin_pct": Decimal("20.0"),
            "vrp_drift_threshold_pct": Decimal("5.0"),
        }
        defaults.update(overrides)
        return PriceEvaluationInput(**defaults)

    def test_suppression_on_below_floor(self):
        """Test suppression created for below-floor price."""
        input_data = self.create_suppression_input(
            our_current_price=Decimal("40.00"),
            vrp_price=Decimal("79.99"),
        )

        result, suppressions, engine = evaluate_and_enforce(input_data, self.rule)

        # Both below_floor and margin_breach triggers fire (below floor = margin < min)
        self.assertEqual(len(suppressions), 2)
        reasons = {s.reason for s in suppressions}
        self.assertIn(SuppressionReason.BELOW_FLOOR_PRICE, reasons)
        self.assertIn(SuppressionReason.MARGIN_BREACH, reasons)
        for s in suppressions:
            self.assertEqual(s.status, SuppressionStatus.ACTIVE)
            self.assertTrue(s.is_active())

    def test_suppression_on_vrp_drift_critical(self):
        """Test suppression for critical VRP drift (>10%)."""
        input_data = self.create_suppression_input(
            our_current_price=Decimal("79.99"),
            vrp_price=Decimal("68.00"),  # ~15% drop
        )

        result, suppressions, engine = evaluate_and_enforce(input_data, self.rule)

        # Should have both below-floor? No, price is above floor.
        # But VRP drift > 10% should trigger
        vrp_suppressions = [s for s in suppressions if s.reason == SuppressionReason.VRP_DRIFT_CRITICAL]
        self.assertEqual(len(vrp_suppressions), 1)

    def test_suppression_activation_and_expiry(self):
        """Test suppression lifecycle: activate, check_expiry, release."""
        event = SuppressionEvent(
            product_id="prod-001",
            sku="TEST-001",
            reason=SuppressionReason.BELOW_FLOOR_PRICE,
            trigger_price=Decimal("40.00"),
            floor_price=Decimal("62.88"),
        )

        # Initially pending
        self.assertEqual(event.status, SuppressionStatus.PENDING)

        # Activate
        event.activate()
        self.assertEqual(event.status, SuppressionStatus.ACTIVE)
        self.assertIsNotNone(event.activated_at)
        self.assertIsNotNone(event.expires_at)

        # Check is_active
        self.assertTrue(event.is_active())

        # Release
        event.release("admin", "manual_override")
        self.assertEqual(event.status, SuppressionStatus.RELEASED)
        self.assertFalse(event.is_active())

    def test_suppression_override(self):
        """Test suppression override with expiry."""
        event = SuppressionEvent(
            product_id="prod-001",
            sku="TEST-001",
            reason=SuppressionReason.BELOW_FLOOR_PRICE,
            trigger_price=Decimal("40.00"),
            floor_price=Decimal("62.88"),
        )
        event.activate()

        # Override
        event.override("admin_user", "Promotional event", expires_hours=4)
        self.assertEqual(event.status, SuppressionStatus.OVERRIDDEN)
        self.assertIsNotNone(event.override_expires_at)

        # Override means deal is NOT blocked during override period
        # is_active() returns False for OVERRIDDEN (deal can proceed)
        self.assertFalse(event.is_active())

    def test_get_active_suppressions(self):
        """Test filtering active suppressions."""
        # Create multiple suppressions
        for i in range(3):
            input_data = self.create_suppression_input(
                product_id=f"prod-{i}",
                sku=f"SKU-{i}",
                our_current_price=Decimal("40.00"),
            )
            self.engine.evaluate_and_suppress(input_data)

        active = self.engine.get_active_suppressions()
        # Each below_floor triggers both BELOW_FLOOR_PRICE and MARGIN_BREACH
        self.assertEqual(len(active), 6)

        # Filter by product
        active_prod0 = self.engine.get_active_suppressions(product_id="prod-0")
        self.assertEqual(len(active_prod0), 2)

    def test_suppression_summary(self):
        """Test suppression summary statistics."""
        for i in range(3):
            input_data = self.create_suppression_input(
                product_id=f"prod-{i}",
                sku=f"SKU-{i}",
                our_current_price=Decimal("40.00"),
            )
            self.engine.evaluate_and_suppress(input_data)

        summary = self.engine.get_suppression_summary()

        self.assertEqual(summary["total"], 6)
        self.assertEqual(summary["active"], 6)
        self.assertEqual(summary["active_products"], 3)
        self.assertIn("below_floor_price", summary["by_reason"])
        self.assertIn("margin_breach", summary["by_reason"])

    def test_is_price_suppressed_helper(self):
        """Test is_price_suppressed helper function."""
        input_data = self.create_suppression_input(
            product_id="prod-001",
            sku="TEST-001",
            our_current_price=Decimal("40.00"),
        )
        self.engine.evaluate_and_suppress(input_data)

        # Check suppression exists
        suppressed, event = is_price_suppressed("prod-001", "TEST-001", self.engine)
        self.assertTrue(suppressed)
        self.assertIsNotNone(event)

        # Different SKU not suppressed
        suppressed, event = is_price_suppressed("prod-001", "OTHER-SKU", self.engine)
        self.assertFalse(suppressed)
        self.assertIsNone(event)

    def test_suppression_event_serialization(self):
        """Test SuppressionEvent to_dict serialization."""
        event = SuppressionEvent(
            product_id="prod-001",
            sku="TEST-001",
            reason=SuppressionReason.BELOW_FLOOR_PRICE,
            trigger_price=Decimal("40.00"),
            floor_price=Decimal("62.88"),
        )
        event.activate()

        d = event.to_dict()

        self.assertIn("id", d)
        self.assertIn("reason", d)
        self.assertEqual(d["reason"], "below_floor_price")
        self.assertIn("status", d)
        self.assertEqual(d["status"], "active")
        self.assertIsInstance(d["trigger_price"], float)
        self.assertIsInstance(d["created_at"], str)  # ISO format


class TestBoundaryConditions(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def setUp(self):
        self.engine = VRPGuardrailEngine()

    def test_zero_price_handling(self):
        """Test handling of zero/negative prices."""
        input_data = PriceEvaluationInput(
            product_id="prod-001",
            sku="TEST",
            asin="",
            category="",
            our_current_price=Decimal("0"),
            cogs_per_unit=Decimal("10.00"),
            min_margin_pct=Decimal("20.0"),
        )

        result = self.engine.evaluate(input_data)

        self.assertEqual(result.status, GuardrailStatus.BREACH)
        self.assertIn("invalid_price", result.violations)

    def test_negative_vrp_drift(self):
        """Test negative VRP drift (VRP below our price)."""
        input_data = PriceEvaluationInput(
            product_id="prod-001",
            sku="TEST",
            asin="",
            category="",
            our_current_price=Decimal("100.00"),
            cogs_per_unit=Decimal("50.00"),
            min_margin_pct=Decimal("20.0"),
            vrp_price=Decimal("80.00"),  # 20% below
            vrp_drift_threshold_pct=Decimal("5.0"),
        )

        result = self.engine.evaluate(input_data)

        self.assertTrue(result.vrp_drift_detected)
        self.assertLess(result.vrp_drift_pct, Decimal("0"))
        self.assertIn("vrp_drop_below_threshold", result.violations)

    def test_very_high_competitor_count(self):
        """Test handling of many competitors."""
        input_data = PriceEvaluationInput(
            product_id="prod-001",
            sku="TEST",
            asin="",
            category="",
            our_current_price=Decimal("79.99"),
            cogs_per_unit=Decimal("28.50"),
            packaging_cost=Decimal("1.20"),
            fulfillment_cost=Decimal("3.50"),
            shipping_cost=Decimal("4.20"),
            referral_fee=Decimal("8.00"),
            fba_fee=Decimal("4.50"),
            processing_fee=Decimal("2.50"),
            min_margin_pct=Decimal("20.0"),
            vrp_price=Decimal("79.99"),
            competitor_count=500,
            lowest_competitor_price=Decimal("75.00"),
        )

        result = self.engine.evaluate(input_data)

        # Should handle gracefully
        self.assertIsNotNone(result)

    def test_missing_optional_fields(self):
        """Test evaluation with minimal optional fields."""
        input_data = PriceEvaluationInput(
            product_id="prod-001",
            sku="TEST",
            asin="",
            category="",
            our_current_price=Decimal("79.99"),
            cogs_per_unit=Decimal("28.50"),
        )

        result = self.engine.evaluate(input_data)

        # Should not crash, use defaults
        self.assertIsNotNone(result.floor_price)
        self.assertEqual(result.status, GuardrailStatus.HEALTHY)

    def test_extreme_margin_requirements(self):
        """Test with extreme margin requirements."""
        input_data = PriceEvaluationInput(
            product_id="prod-001",
            sku="TEST",
            asin="",
            category="",
            our_current_price=Decimal("200.00"),
            cogs_per_unit=Decimal("50.00"),
            min_margin_pct=Decimal("90.0"),  # 90% margin required
            vrp_price=Decimal("200.00"),
        )

        result = self.engine.evaluate(input_data)

        # Floor = (50 + vars) * 1.9
        # Price 200 might still be below floor
        self.assertIn(result.status, [GuardrailStatus.HEALTHY, GuardrailStatus.BREACH])

    def test_rapid_price_changes(self):
        """Test evaluation of rapid successive price changes."""
        base_input = PriceEvaluationInput(
            product_id="prod-001",
            sku="TEST",
            asin="",
            category="",
            our_current_price=Decimal("79.99"),
            cogs_per_unit=Decimal("28.50"),
            packaging_cost=Decimal("1.20"),
            fulfillment_cost=Decimal("3.50"),
            shipping_cost=Decimal("4.20"),
            referral_fee=Decimal("8.00"),
            fba_fee=Decimal("4.50"),
            processing_fee=Decimal("2.50"),
            min_margin_pct=Decimal("20.0"),
            vrp_price=Decimal("79.99"),
        )

        # Simulate rapid repricing
        prices = [Decimal("79.99"), Decimal("75.00"), Decimal("70.00"), Decimal("65.00"), Decimal("60.00")]
        results = []

        for price in prices:
            inp = base_input
            inp.proposed_price = price
            results.append(self.engine.evaluate(inp))

        # First few healthy, then breach
        self.assertEqual(results[0].status, GuardrailStatus.HEALTHY)
        self.assertEqual(results[-1].status, GuardrailStatus.BREACH)

    def test_cost_component_precision(self):
        """Test precision with many cost components."""
        from src.engine.calculator import calculate_floor_price

        result = calculate_floor_price(
            cogs_per_unit=Decimal("10.0001"),
            packaging_cost=Decimal("0.0001"),
            fulfillment_cost=Decimal("0.0001"),
            shipping_cost=Decimal("0.0001"),
            referral_fee=Decimal("0.0001"),
            fba_fee=Decimal("0.0001"),
            processing_fee=Decimal("0.0001"),
            min_margin_pct=Decimal("10.0001"),
        )

        # Should handle high precision without error
        self.assertIsNotNone(result.floor_price())
        # 2 decimal places in output
        self.assertEqual(result.floor_price().as_tuple().exponent, -2)


class TestIntegrationScenarios(unittest.TestCase):
    """Integration tests for realistic scenarios."""

    def setUp(self):
        self.engine = VRPGuardrailEngine()
        self.rule = SuppressionRule()

    def test_competitor_price_war_scenario(self):
        """Test scenario: competitor starts price war."""
        # Day 1: Normal pricing
        day1 = PriceEvaluationInput(
            product_id="prod-001",
            sku="ELEC-001",
            asin="B08N5WRWNW",
            category="Electronics",
            our_current_price=Decimal("79.99"),
            cogs_per_unit=Decimal("28.50"),
            packaging_cost=Decimal("1.20"),
            fulfillment_cost=Decimal("3.50"),
            shipping_cost=Decimal("4.20"),
            referral_fee=Decimal("8.00"),
            fba_fee=Decimal("4.50"),
            processing_fee=Decimal("2.50"),
            min_margin_pct=Decimal("20.0"),
            vrp_price=Decimal("79.99"),
            lowest_competitor_price=Decimal("78.00"),
            competitor_count=10,
        )

        r1 = self.engine.evaluate(day1)
        self.assertEqual(r1.status, GuardrailStatus.HEALTHY)

        # Day 3: Competitor drops price 15%
        day3 = PriceEvaluationInput(
            **{k: v for k, v in day1.__dict__.items() if k not in ("our_current_price", "vrp_price", "lowest_competitor_price", "buybox_price")},
            our_current_price=Decimal("79.99"),
            vrp_price=Decimal("79.99"),
            lowest_competitor_price=Decimal("66.30"),  # 15% drop
            buybox_price=Decimal("66.30"),
        )

        r3 = self.engine.evaluate(day3)
        # We lose buybox but still above floor
        self.assertIn("buybox_lost", r3.violations)
        self.assertEqual(r3.recommended_action, RecommendedAction.MATCH_COMPETITOR)

        # Day 5: We match competitor, but it's below floor
        day5 = PriceEvaluationInput(
            **{k: v for k, v in day1.__dict__.items() if k not in ("our_current_price", "vrp_price", "lowest_competitor_price")},
            our_current_price=Decimal("60.00"),  # Below floor (62.88)
            vrp_price=Decimal("60.00"),
            lowest_competitor_price=Decimal("60.00"),
        )

        r5 = self.engine.evaluate(day5)
        self.assertEqual(r5.status, GuardrailStatus.BREACH)
        self.assertTrue(r5.suppression_triggered)

    def test_vrp_monitoring_scenario(self):
        """Test scenario: VRP monitoring with drift detection."""
        base = PriceEvaluationInput(
            product_id="prod-001",
            sku="ELEC-001",
            asin="B08N5WRWNW",
            category="Electronics",
            our_current_price=Decimal("89.99"),
            cogs_per_unit=Decimal("35.00"),
            packaging_cost=Decimal("1.50"),
            fulfillment_cost=Decimal("4.00"),
            shipping_cost=Decimal("5.00"),
            referral_fee=Decimal("9.00"),
            fba_fee=Decimal("5.00"),
            processing_fee=Decimal("3.00"),
            min_margin_pct=Decimal("18.0"),
        )

        # Normal VRP
        vrp_normal = PriceEvaluationInput(**{**base.__dict__, "vrp_price": Decimal("89.99")})
        r1 = self.engine.evaluate(vrp_normal)
        self.assertEqual(r1.status, GuardrailStatus.HEALTHY)

        # VRP drops 3% (within threshold)
        vrp_warning = PriceEvaluationInput(**{**base.__dict__, "vrp_price": Decimal("87.00")})
        r2 = self.engine.evaluate(vrp_warning)
        self.assertEqual(r2.status, GuardrailStatus.HEALTHY)  # 3% < 5%

        # VRP drops 7% (exceeds threshold)
        vrp_breach = PriceEvaluationInput(**{**base.__dict__, "vrp_price": Decimal("83.50")})
        r3 = self.engine.evaluate(vrp_breach)
        self.assertEqual(r3.status, GuardrailStatus.WARNING)
        self.assertTrue(r3.vrp_drift_detected)
        self.assertEqual(r3.recommended_action, RecommendedAction.DECREASE_PRICE)

    def test_promotional_spike_scenario(self):
        """Test scenario: promotional spike then normalization."""
        base = PriceEvaluationInput(
            product_id="prod-001",
            sku="HOME-001",
            asin="B07XYZ123",
            category="Home",
            our_current_price=Decimal("49.99"),
            cogs_per_unit=Decimal("15.00"),
            packaging_cost=Decimal("0.80"),
            fulfillment_cost=Decimal("2.00"),
            shipping_cost=Decimal("3.00"),
            referral_fee=Decimal("5.00"),
            fba_fee=Decimal("3.00"),
            processing_fee=Decimal("1.50"),
            min_margin_pct=Decimal("22.0"),
        )

        # Normal period
        normal = PriceEvaluationInput(**{**base.__dict__, "vrp_price": Decimal("49.99")})
        r1 = self.engine.evaluate(normal)
        self.assertEqual(r1.status, GuardrailStatus.HEALTHY)

        # Promo: price raised to $59.99, VRP follows
        promo = PriceEvaluationInput(**{**base.__dict__, "our_current_price": Decimal("59.99"), "vrp_price": Decimal("59.99")})
        r2 = self.engine.evaluate(promo)
        self.assertEqual(r2.status, GuardrailStatus.HEALTHY)
        self.assertGreater(r2.calculated_margin_pct, Decimal("22.0"))

        # Post-promo: VRP drops to $45, our price still $59.99
        post = PriceEvaluationInput(**{**base.__dict__, "our_current_price": Decimal("59.99"), "vrp_price": Decimal("45.00")})
        r3 = self.engine.evaluate(post)
        self.assertEqual(r3.status, GuardrailStatus.WARNING)
        self.assertTrue(r3.vrp_drift_detected)
        self.assertEqual(r3.recommended_action, RecommendedAction.DECREASE_PRICE)


if __name__ == "__main__":
    unittest.main(verbosity=2)