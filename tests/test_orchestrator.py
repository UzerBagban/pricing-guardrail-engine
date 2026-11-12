"""Unit & Integration tests for Phase 5 Autonomous Pipeline Orchestrator."""

import json
from unittest import TestCase, main
from src.engine.orchestrator import CycleReport, PipelineOrchestrator


class TestPipelineOrchestrator(TestCase):
    def setUp(self):
        self.orchestrator = PipelineOrchestrator(
            commit=False,  # Dry run for tests
            mock_alerts=True,
            batch_size=10,
        )

    def test_verify_database(self):
        self.assertTrue(self.orchestrator.verify_database())

    def test_run_cycle_dry_run_with_simulation(self):
        report = self.orchestrator.run_cycle(simulate_count=5)
        self.assertIsInstance(report, CycleReport)
        self.assertTrue(report.cycle_id.startswith("CYC-"))
        self.assertEqual(report.events_ingested, 5)
        self.assertTrue(report.invariant_passed)
        self.assertGreater(report.duration_ms, 0)
        self.assertEqual(len(report.errors), 0)

    def test_run_cycle_with_explicit_breach_event(self):
        # Explicit predatory undercut below cost floor
        test_event = {
            "event_id": "test-breach-orch-001",
            "sku": "ELEC-002",
            "category": "Electronics",
            "channel": "amazon",
            "our_price": 24.99,
            "floor_price": 18.35,
            "lowest_competitor_price": 12.50,  # Below floor
            "guardrail_status": "breach",
            "scenario": "aggressive_competitor_undercut",
            "cogs": 12.75,
            "shipping": 2.10,
            "fees": 3.00,
            "calculated_margin_pct": 28.57,
            "vrp_drift_pct": 0.0,
            "elasticity_score": 1.0,
        }
        report = self.orchestrator.run_cycle(input_records=[test_event])
        self.assertEqual(report.events_ingested, 1)
        self.assertEqual(report.breaches_detected, 1)
        self.assertTrue(report.invariant_passed)

    def test_daemon_max_cycles(self):
        # Run daemon with max_cycles=2 and interval=0
        reports = self.orchestrator.run_daemon(
            interval_seconds=0,
            max_cycles=2,
            simulate_per_cycle=3,
        )
        self.assertEqual(len(reports), 2)
        for r in reports:
            self.assertEqual(r.events_ingested, 3)
            self.assertTrue(r.invariant_passed)

    def test_cycle_report_to_dict(self):
        report = self.orchestrator.run_cycle(simulate_count=2)
        d = report.to_dict()
        self.assertIn("cycle_id", d)
        self.assertIn("duration_ms", d)
        self.assertIn("events_ingested", d)
        # Verify JSON serializability
        encoded = json.dumps(d)
        self.assertIn("CYC-", encoded)


if __name__ == "__main__":
    main(verbosity=2)
