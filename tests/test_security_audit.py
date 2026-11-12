"""Unit & Integration tests for Phase 5 Security, Immutability & Compliance Audit."""

from unittest import TestCase, main
import psycopg2
from src.analytics.security_audit import (
    audit_immutability_triggers,
    audit_invariant_compliance,
    audit_channel_enum_constraints,
    audit_input_sanitization_defense,
    audit_override_governance,
    run_security_compliance_audit,
)
from src.ingestion.db import get_cursor, test_connection as db_test_connection
from src.ingestion.validator import validate_telemetry_payload


class TestSecurityAndComplianceAudit(TestCase):
    def test_immutability_trigger_installed(self):
        res = audit_immutability_triggers()
        self.assertEqual(res.status, "PASSED")
        self.assertIn("prevent_audit_modification", res.details)

    def test_channel_enum_constraint_rejects_unauthorized(self):
        self.assertTrue(db_test_connection())
        # Attempt to insert an unauthorized marketplace channel
        with self.assertRaises(psycopg2.errors.CheckViolation):
            with get_cursor(commit=True) as cur:
                cur.execute("SELECT id, asin FROM products LIMIT 1")
                prod = cur.fetchone()
                cur.execute("""
                    INSERT INTO price_change_log (
                        product_id, asin, channel, change_type, new_price, floor_price
                    ) VALUES (%s, %s, 'unauthorized_dark_market', 'floor_price_update', 50.0, 40.0)
                """, (prod["id"], prod["asin"]))

    def test_validator_blocks_sql_injection(self):
        payload = {
            "sku": "ELEC-001'; DROP TABLE products; --",
            "our_price": 49.99,
            "channel": "amazon",
        }
        res = validate_telemetry_payload(payload)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("SQL injection" in t for t in res.threat_indicators))

    def test_validator_blocks_xss_script(self):
        payload = {
            "sku": "ELEC-002<script>alert('pwned')</script>",
            "our_price": 24.99,
            "channel": "amazon",
        }
        res = validate_telemetry_payload(payload)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("XSS" in t for t in res.threat_indicators))

    def test_validator_blocks_negative_pricing(self):
        payload = {
            "sku": "BEAU-001",
            "our_price": -15.00,
            "floor_price": -10.00,
            "channel": "amazon",
        }
        res = validate_telemetry_payload(payload)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("must be at least" in e or "cannot be negative" in e for e in res.errors))

    def test_validator_blocks_unauthorized_channel(self):
        payload = {
            "sku": "HOME-001",
            "our_price": 29.99,
            "channel": "untrusted_marketplace_xyz",
        }
        res = validate_telemetry_payload(payload)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("Invalid marketplace channel" in e for e in res.errors))

    def test_validator_accepts_clean_payload(self):
        payload = {
            "sku": "SPRT-001",
            "name": "Resistance Bands Set 5-Level",
            "category": "Sports & Outdoors",
            "channel": "amazon",
            "our_price": 23.45,
            "floor_price": 12.80,
            "cogs": 6.20,
            "shipping": 1.50,
            "fees": 2.80,
            "lowest_competitor_price": 23.19,
        }
        res = validate_telemetry_payload(payload)
        self.assertTrue(res.is_valid)
        self.assertEqual(len(res.errors), 0)
        self.assertEqual(res.sanitized_data["sku"], "SPRT-001")
        self.assertEqual(res.sanitized_data["channel"], "amazon")

    def test_full_security_audit_suite(self):
        report = run_security_compliance_audit()
        self.assertEqual(report.overall_status, "PASSED")
        self.assertEqual(report.failed_checks, 0)
        self.assertEqual(report.passed_checks, report.total_checks)


if __name__ == "__main__":
    main(verbosity=2)
