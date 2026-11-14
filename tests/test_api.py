"""Automated Unit & Integration Tests for FastAPI Web Service and REST Endpoints."""

from unittest import TestCase, main
from fastapi.testclient import TestClient
from src.api.app import app


class TestPricingGuardrailAPI(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_health_endpoint(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("status", data)
        self.assertEqual(data["version"], "1.3.0")
        self.assertIn("database_connected", data)
        self.assertIn("timestamp", data)

    def test_kpis_endpoint(self):
        res = self.client.get("/api/v1/kpis")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("kpis", data)
        self.assertIn("status_distribution", data)
        kpis = data["kpis"]
        self.assertGreater(kpis.get("total_events", 0), 0)
        self.assertGreater(kpis.get("margin_dollars_protected", 0.0), 0.0)

    def test_complete_dataset_endpoint(self):
        res = self.client.get("/api/v1/dataset")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        for key in ("kpis", "status_distribution", "categories", "sku_rows", "diagnostic_memos"):
            self.assertIn(key, data)

    def test_events_filtering_and_pagination(self):
        # 1. Unfiltered
        res = self.client.get("/api/v1/events?limit=10&offset=0")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("items", data)
        self.assertLessEqual(len(data["items"]), 10)
        self.assertGreater(data["total"], 0)

        # 2. Status filter
        res_breach = self.client.get("/api/v1/events?status=breach")
        self.assertEqual(res_breach.status_code, 200)
        data_breach = res_breach.json()
        for item in data_breach["items"]:
            self.assertEqual(item["guardrail_status"].lower(), "breach")

        # 3. Category filter
        res_cat = self.client.get("/api/v1/events?category=Electronics")
        self.assertEqual(res_cat.status_code, 200)
        for item in res_cat.json()["items"]:
            self.assertEqual(item["category"], "Electronics")

    def test_diagnostic_memos_endpoint(self):
        res = self.client.get("/api/v1/memos")
        self.assertEqual(res.status_code, 200)
        memos = res.json()
        self.assertIsInstance(memos, list)
        self.assertGreaterEqual(len(memos), 3)
        for memo in memos:
            self.assertIn("title", memo)
            self.assertIn("severity", memo)
            self.assertIn("summary", memo)

    def test_breach_forecast_endpoint(self):
        res = self.client.get("/api/v1/forecast")
        self.assertEqual(res.status_code, 200)
        forecasts = res.json()
        self.assertIsInstance(forecasts, list)
        if len(forecasts) > 0:
            top = forecasts[0]
            self.assertIn("sku", top)
            self.assertIn("breach_probability_7d", top)
            self.assertIn("risk_tier", top)
            self.assertIn("preventative_action", top)

    def test_simulation_approved(self):
        payload = {
            "sku": "SIM-HEALTHY",
            "category": "Electronics",
            "cogs": 12.0,
            "shipping_cost": 3.0,
            "referral_rate_pct": 15.0,
            "min_margin_amount": 4.0,
            "proposed_price": 35.0,
        }
        res = self.client.post("/api/v1/simulate", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["is_safe"])
        self.assertEqual(data["verdict"], "APPROVED")
        self.assertGreater(data["floor_buffer"], 0.0)
        self.assertGreater(data["net_margin"], 0.0)

    def test_simulation_floor_breach_blocked(self):
        # Selling price below calculated floor
        payload = {
            "sku": "SIM-BREACH",
            "category": "Electronics",
            "cogs": 20.0,
            "shipping_cost": 5.0,
            "referral_rate_pct": 15.0,
            "min_margin_amount": 6.0,
            "proposed_price": 18.0,  # Below floor (~$36.47)
        }
        res = self.client.post("/api/v1/simulate", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["is_safe"])
        self.assertIn("BLOCKED", data["verdict"])
        self.assertEqual(data["action"], "HALT_REPRICING")
        self.assertLess(data["floor_buffer"], 0.0)

    def test_simulation_tight_buffer_warning(self):
        # Floor is (10 + 2 + 3) / (1 - 0.15) = 15 / 0.85 = 17.65
        # Selling price at 18.50 is safe but buffer < 2.0
        payload = {
            "sku": "SIM-WARNING",
            "category": "Home & Kitchen",
            "cogs": 10.0,
            "shipping_cost": 2.0,
            "referral_rate_pct": 15.0,
            "min_margin_amount": 3.0,
            "proposed_price": 18.50,
        }
        res = self.client.post("/api/v1/simulate", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["is_safe"])
        self.assertIn("WARNING", data["verdict"])

    def test_security_audit_endpoint(self):
        res = self.client.get("/api/v1/security-audit")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("overall_status", data)
        self.assertIn("checks", data)
        self.assertIn("passed_checks", data)

    def test_root_index_html_rendered(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("content-type", ""))
        self.assertIn("Pricing Guardrail", res.text)


if __name__ == "__main__":
    main(verbosity=2)
