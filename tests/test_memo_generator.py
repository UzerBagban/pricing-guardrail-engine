"""Unit tests for Phase 4 Executive Diagnostic Memo Generator & Root-Cause Explainer."""

import json
from unittest import TestCase, main
from src.analytics.memo_generator import (
    diagnose_sku,
    explain_sku,
    generate_executive_memo_data,
    format_markdown_memo,
    format_html_memo,
)


class TestMemoGenerator(TestCase):
    def test_diagnose_predatory_undercut(self):
        sku_data = {
            "sku": "TEST-UNDERCUT-001",
            "name": "Gaming Mouse RGB",
            "category": "Electronics",
            "channel": "amazon",
            "our_price": 50.0,
            "floor_price": 35.0,
            "lowest_competitor_price": 25.0,  # below floor
            "calculated_margin_pct": 42.8,
            "min_margin": 15.0,
            "vrp_drift_pct": 0.0,
            "guardrail_status": "breach",
            "scenario": "aggressive_competitor_undercut",
            "cogs": 20.0,
            "shipping": 5.0,
            "fees": 5.0,
        }
        res = diagnose_sku(sku_data)
        self.assertEqual(res["threat_type"], "PREDATORY_UNDERCUT")
        self.assertEqual(res["severity"], 5)
        self.assertEqual(res["recommendation"], "HOLD_FLOOR")
        self.assertIn("BELOW our cost floor", res["explanation"])
        self.assertIn("$35.00", res["explanation"])

    def test_diagnose_vrp_drift_shock(self):
        sku_data = {
            "sku": "TEST-VRP-001",
            "name": "Coffee Maker",
            "category": "Home & Kitchen",
            "channel": "amazon",
            "our_price": 80.0,
            "floor_price": 50.0,
            "vrp_price": 65.0,
            "lowest_competitor_price": 75.0,
            "calculated_margin_pct": 60.0,
            "min_margin": 20.0,
            "vrp_drift_pct": -15.4,  # shock <= -10%
            "guardrail_status": "warning",
            "scenario": "vrp_drift_shock",
            "cogs": 30.0,
            "shipping": 10.0,
            "fees": 10.0,
        }
        res = diagnose_sku(sku_data)
        self.assertEqual(res["threat_type"], "VRP_DRIFT_SHOCK")
        self.assertEqual(res["severity"], 4)
        self.assertEqual(res["recommendation"], "MONITOR_ELASTICITY")
        self.assertIn("plummeted by 15.4%", res["explanation"])

    def test_diagnose_cost_inflation(self):
        sku_data = {
            "sku": "TEST-COST-001",
            "name": "Camping Tent",
            "category": "Sports & Outdoors",
            "channel": "amazon",
            "our_price": 120.0,
            "floor_price": 85.0,
            "vrp_price": 120.0,
            "lowest_competitor_price": 118.0,
            "calculated_margin_pct": 41.2,
            "min_margin": 15.0,
            "vrp_drift_pct": 0.0,
            "guardrail_status": "healthy",
            "scenario": "cost_inflation_breach",
            "cogs": 50.0,
            "shipping": 20.0,
            "fees": 15.0,
        }
        res = diagnose_sku(sku_data)
        self.assertEqual(res["threat_type"], "LOGISTICS_COST_INFLATION")
        self.assertEqual(res["severity"], 3)
        self.assertEqual(res["recommendation"], "UPDATE_FLOOR")

    def test_diagnose_healthy(self):
        sku_data = {
            "sku": "TEST-HEALTHY-001",
            "name": "Desk Lamp",
            "category": "Home",
            "channel": "amazon",
            "our_price": 45.0,
            "floor_price": 25.0,
            "vrp_price": 45.0,
            "lowest_competitor_price": 44.0,
            "calculated_margin_pct": 80.0,
            "min_margin": 15.0,
            "vrp_drift_pct": 0.0,
            "guardrail_status": "healthy",
            "scenario": "normal",
            "cogs": 15.0,
            "shipping": 5.0,
            "fees": 5.0,
        }
        res = diagnose_sku(sku_data)
        self.assertEqual(res["threat_type"], "HEALTHY_EQUILIBRIUM")
        self.assertEqual(res["severity"], 1)
        self.assertEqual(res["recommendation"], "MAINTAIN")

    def test_generate_executive_memo_data(self):
        data = generate_executive_memo_data()
        self.assertIn("title", data)
        self.assertIn("kpis", data)
        self.assertIn("threat_breakdown", data)
        self.assertIn("critical_incidents", data)
        self.assertGreater(data["kpis"]["total_events"], 0)

    def test_format_markdown_memo(self):
        data = generate_executive_memo_data()
        md = format_markdown_memo(data)
        self.assertIn("# Executive Pricing Guardrail & Margin Defense Audit Memo", md)
        self.assertIn("## 1. Executive Summary & Value Protected", md)
        self.assertIn("## 2. High-Risk SKU Incident Autopsy", md)
        self.assertIn("## 3. Strategic Recommendations", md)

    def test_format_html_memo(self):
        data = generate_executive_memo_data()
        html = format_html_memo(data)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Audited Events", html)
        self.assertIn("Profit Defended", html)

    def test_explain_sku_live(self):
        res = explain_sku("ELEC-002")
        self.assertTrue(res["found"])
        self.assertEqual(res["sku"], "ELEC-002")
        self.assertIn("recommendation", res)
        self.assertIn("explanation", res)

    def test_explain_missing_sku(self):
        res = explain_sku("NON-EXISTENT-SKU-999")
        self.assertFalse(res["found"])
        self.assertIn("not found", res["explanation"])


if __name__ == "__main__":
    main(verbosity=2)
