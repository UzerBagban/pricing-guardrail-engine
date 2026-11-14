"""Unit and Integration Tests for Zero-ETL Federated Company Connectors."""

import os
import sqlite3
import tempfile
from decimal import Decimal
from unittest import TestCase, main

from fastapi.testclient import TestClient

from src.api.app import app
from src.connectors.base import NormalizedTelemetry, SchemaMapping
from src.connectors.manager import ConnectorRegistry, default_registry
from src.connectors.sql_connector import DirectSQLConnector


class TestFederatedConnectors(TestCase):
    def setUp(self):
        # Create a temporary mock external client SQLite database
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.db_url = f"sqlite:///{self.db_path.replace(os.sep, '/')}"

        # Initialize mock client schema with non-standard column names
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE client_pricing_feed (
                item_code TEXT PRIMARY KEY,
                current_retail_price REAL,
                supplier_unit_cost REAL,
                carrier_freight_fee REAL,
                marketplace_cut_pct REAL,
                min_dollar_profit REAL,
                amazon_reference_price REAL,
                competitor_min_price REAL
            )
        """)
        # Insert 3 client records (1 healthy, 1 warning, 1 below-cost breach)
        cur.executemany("""
            INSERT INTO client_pricing_feed VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            ("CLIENT-SKU-101", 89.99, 35.00, 5.50, 15.0, 10.00, 89.99, 87.50),   # Healthy
            ("CLIENT-SKU-102", 52.00, 30.00, 4.00, 15.0, 8.00, 50.00, 51.00),    # Warning
            ("CLIENT-SKU-103", 22.00, 20.00, 3.50, 15.0, 5.00, 21.00, 12.50),    # Floor breach (cost=$28.5, price=$22)
        ])
        conn.commit()
        conn.close()

        # Custom schema mapping
        self.mapping = SchemaMapping(field_map={
            "sku": "item_code",
            "our_price": "current_retail_price",
            "cogs": "supplier_unit_cost",
            "shipping_cost": "carrier_freight_fee",
            "referral_rate_pct": "marketplace_cut_pct",
            "min_margin_amount": "min_dollar_profit",
            "vrp_price": "amazon_reference_price",
            "lowest_competitor_price": "competitor_min_price",
        })

        self.client = TestClient(app)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_schema_mapping_normalization(self):
        raw = {
            "item_code": "CUSTOM-001",
            "current_retail_price": 49.99,
            "supplier_unit_cost": 18.50,
            "carrier_freight_fee": 3.50,
        }
        normalized = self.mapping.normalize(raw)
        self.assertEqual(normalized.sku, "CUSTOM-001")
        self.assertEqual(normalized.our_price, Decimal("49.99"))
        self.assertEqual(normalized.cogs, Decimal("18.50"))
        self.assertEqual(normalized.shipping_cost, Decimal("3.50"))

    def test_direct_sql_connector_stream(self):
        connector = DirectSQLConnector(
            tenant_id="tenant_acme",
            name="Acme Corp External DB",
            connection_url=self.db_url,
            telemetry_table="client_pricing_feed",
            schema_mapping=self.mapping,
        )

        ok, msg = connector.test_connection()
        self.assertTrue(ok)
        self.assertIn("Successfully connected", msg)

        events = list(connector.stream_live_telemetry(limit=10))
        self.assertEqual(len(events), 3)
        skus = [e.sku for e in events]
        self.assertIn("CLIENT-SKU-101", skus)
        self.assertIn("CLIENT-SKU-103", skus)

    def test_connector_registry_live_evaluation(self):
        registry = ConnectorRegistry()
        connector = DirectSQLConnector(
            tenant_id="tenant_enterprise_beta",
            name="Beta Enterprise Live Stream",
            connection_url=self.db_url,
            telemetry_table="client_pricing_feed",
            schema_mapping=self.mapping,
        )
        registry.register_connector(connector)

        result = registry.evaluate_live_feed("tenant_enterprise_beta", limit=10)
        self.assertEqual(result["tenant_id"], "tenant_enterprise_beta")
        self.assertEqual(result["total_items_evaluated"], 3)
        self.assertEqual(result["blocked_breaches"], 1)  # CLIENT-SKU-103 breached cost floor
        self.assertGreater(result["gross_profit_defended_dollars"], 0.0)
        self.assertLess(result["avg_item_latency_ms"], 5.0)  # Sub-millisecond evaluation SLA

        # Verify safe pricing enforcement
        items = {item["sku"]: item for item in result["items"]}
        self.assertEqual(items["CLIENT-SKU-101"]["verdict"], "APPROVED")
        self.assertEqual(items["CLIENT-SKU-103"]["verdict"], "BLOCKED (FLOOR BREACH)")
        self.assertGreaterEqual(items["CLIENT-SKU-103"]["safe_executable_price"], items["CLIENT-SKU-103"]["floor_price"])

    def test_fastapi_connector_endpoints(self):
        # 1. Register connector via API
        reg_payload = {
            "tenant_id": "tenant_api_client",
            "name": "API Client External Store",
            "connector_type": "sql",
            "connection_url": self.db_url,
            "telemetry_table": "client_pricing_feed",
            "field_mappings": self.mapping.field_map,
        }
        res_reg = self.client.post("/api/v1/connectors/register", json=reg_payload)
        self.assertEqual(res_reg.status_code, 200)
        self.assertEqual(res_reg.json()["status"], "registered")

        # 2. List connectors
        res_list = self.client.get("/api/v1/connectors")
        self.assertEqual(res_list.status_code, 200)
        tids = [c["tenant_id"] for c in res_list.json()]
        self.assertIn("tenant_api_client", tids)

        # 3. Test connector
        res_test = self.client.post("/api/v1/connectors/test?tenant_id=tenant_api_client")
        self.assertEqual(res_test.status_code, 200)
        self.assertTrue(res_test.json()["connected"])

        # 4. Live Zero-ETL Evaluation via API
        eval_payload = {
            "tenant_id": "tenant_api_client",
            "limit": 10,
            "push_decisions": False,
        }
        res_eval = self.client.post("/api/v1/connectors/evaluate-live", json=eval_payload)
        self.assertEqual(res_eval.status_code, 200)
        eval_data = res_eval.json()
        self.assertEqual(eval_data["total_items_evaluated"], 3)
        self.assertEqual(eval_data["blocked_breaches"], 1)
        self.assertGreater(eval_data["gross_profit_defended_dollars"], 0.0)


if __name__ == "__main__":
    main(verbosity=2)
