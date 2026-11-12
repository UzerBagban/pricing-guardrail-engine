"""Unit tests for Phase 4 Predictive 7-Day Floor Breach Forecaster."""

import os
from unittest import TestCase, main
from src.engine.predictive import (
    FEATURE_NAMES,
    extract_features,
    train_model,
    predict_sku_risk,
    predict_catalog_risks,
    load_or_train_model,
)


class TestPredictiveEngine(TestCase):
    def test_extract_features(self):
        row = {
            "our_price": 50.0,
            "floor_price": 40.0,
            "lowest_competitor_price": 35.0,
            "vrp_drift_pct": -5.0,
            "elasticity_score": -1.5,
            "competitor_count": 8,
            "calculated_margin_pct": 25.0,
        }
        feats = extract_features(row)
        self.assertEqual(len(feats), len(FEATURE_NAMES))
        # floor buffer = (50 - 40) / 40 * 100 = 25%
        self.assertAlmostEqual(feats[0], 25.0, places=1)
        # competitor gap = (50 - 35) / 50 * 100 = 30%
        self.assertAlmostEqual(feats[1], 30.0, places=1)

    def test_train_model_performance(self):
        test_model_path = "models/test_predictor.joblib"
        metrics = train_model(save_path=test_model_path)
        self.assertEqual(metrics["status"], "trained")
        self.assertGreaterEqual(metrics["test_accuracy"], 0.85)
        self.assertIn("feature_importances", metrics)
        self.assertTrue(os.path.exists(test_model_path))
        # Cleanup test artifact
        try:
            os.remove(test_model_path)
        except OSError:
            pass

    def test_predict_critical_risk(self):
        model = load_or_train_model()
        row = {
            "sku": "RISK-TEST-001",
            "name": "High Risk Gadget",
            "category": "Electronics",
            "our_price": 25.0,
            "floor_price": 22.0,
            "lowest_competitor_price": 14.0,  # aggressive undercut
            "vrp_drift_pct": -15.0,          # drift shock
            "elasticity_score": -3.5,
            "competitor_count": 15,
            "calculated_margin_pct": 13.6,
        }
        pred = predict_sku_risk(row, model=model)
        self.assertEqual(pred["sku"], "RISK-TEST-001")
        self.assertIn(pred["risk_tier"], ("CRITICAL", "HIGH"))
        self.assertGreaterEqual(pred["breach_probability_7d"], 0.50)
        self.assertIn("LOCK_FLOOR_IMMEDIATELY", pred["preventative_action"])
        self.assertGreater(len(pred["primary_risk_drivers"]), 0)

    def test_predict_low_risk(self):
        model = load_or_train_model()
        row = {
            "sku": "SAFE-TEST-001",
            "name": "Safe Brand Item",
            "category": "Home",
            "our_price": 100.0,
            "floor_price": 40.0,             # huge buffer (+150%)
            "lowest_competitor_price": 98.0,
            "vrp_drift_pct": 0.5,
            "elasticity_score": 0.8,
            "competitor_count": 2,
            "calculated_margin_pct": 60.0,
        }
        pred = predict_sku_risk(row, model=model)
        self.assertEqual(pred["sku"], "SAFE-TEST-001")
        self.assertEqual(pred["risk_tier"], "LOW")
        self.assertLess(pred["breach_probability_7d"], 0.20)
        self.assertEqual(pred["preventative_action"], "MAINTAIN_CURRENT_POLICY")

    def test_predict_catalog_risks(self):
        preds = predict_catalog_risks()
        self.assertGreater(len(preds), 0)
        # Should be sorted descending by probability
        probs = [p["breach_probability_7d"] for p in preds]
        self.assertEqual(probs, sorted(probs, reverse=True))


if __name__ == "__main__":
    main(verbosity=2)
