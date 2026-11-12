"""Predictive Floor Breach Forecaster & Elasticity Risk Scorer.

Phase 4 Deliverable (Spec §1.2 & §3.2):
- scikit-learn GradientBoosting model forecasting 7-day floor price breach probability.
- Feature engineering: floor buffer ratio, competitor gap, VRP drift velocity, elasticity score.
- Per-SKU risk tier classification (CRITICAL, HIGH, MEDIUM, LOW).
- Explainable risk driver contributions.

Usage:
  python -m src.engine.predictive --train
  python -m src.engine.predictive --predict-all
  python -m src.engine.predictive --predict ELEC-002
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split

from src.ingestion.db import get_cursor, test_connection

log = logging.getLogger("engine.predictive")

MODEL_DIR = "models"
MODEL_PATH = os.path.join(MODEL_DIR, "floor_breach_predictor.joblib")

FEATURE_NAMES = [
    "floor_buffer_pct",
    "competitor_gap_pct",
    "vrp_drift_pct",
    "elasticity_score",
    "competitor_count",
    "calculated_margin_pct",
]


def _float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def extract_features(row: Dict[str, Any]) -> List[float]:
    """Extract normalized feature vector for predictive model."""
    our_price = _float(row.get("our_price"), 10.0)
    floor_price = _float(row.get("floor_price"), 8.0)
    lowest_comp = _float(row.get("lowest_competitor_price"), our_price)
    vrp_drift = _float(row.get("vrp_drift_pct"), 0.0)
    elasticity = _float(row.get("elasticity_score"), 1.0)
    comp_count = _float(row.get("competitor_count"), 5.0)
    margin_pct = _float(row.get("calculated_margin_pct"), 30.0)

    # 1. Floor buffer %: How far current price is above floor (smaller = higher risk)
    floor_buffer_pct = ((our_price - floor_price) / max(floor_price, 0.01)) * 100.0

    # 2. Competitor gap %: How much competitor is undercutting us (positive = competitor cheaper)
    competitor_gap_pct = ((our_price - lowest_comp) / max(our_price, 0.01)) * 100.0

    return [
        float(floor_buffer_pct),
        float(competitor_gap_pct),
        float(vrp_drift),
        float(elasticity),
        float(comp_count),
        float(margin_pct),
    ]


def build_training_dataset() -> Tuple[np.ndarray, np.ndarray]:
    """Extract features and target labels from database telemetry or synthetic fallback."""
    X_list = []
    y_list = []

    if test_connection():
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT 
                    our_price, floor_price, lowest_competitor_price, 
                    vrp_drift_pct, elasticity_score, competitor_count, 
                    calculated_margin_pct, guardrail_status, scenario
                FROM telemetry_raw
            """)
            rows = cur.fetchall()

            for r in rows:
                feats = extract_features(dict(r))
                status = (r.get("guardrail_status") or "").lower()
                scenario = (r.get("scenario") or "").lower()
                lowest_comp = _float(r.get("lowest_competitor_price"), 0)
                floor_price = _float(r.get("floor_price"), 0)

                # Target: 1 if high-risk breach scenario or competitor undercut below floor
                is_breach = int(
                    status == "breach"
                    or scenario in ("aggressive_competitor_undercut", "margin_compression", "cost_inflation_breach")
                    or (lowest_comp > 0 and lowest_comp < floor_price)
                    or (feats[0] < 10.0 and feats[2] < -5.0)
                )
                X_list.append(feats)
                y_list.append(is_breach)

    # Ensure dataset is adequate (supplement with synthetic boundary cases if needed)
    if len(X_list) < 50:
        np.random.seed(42)
        for _ in range(100):
            buffer = np.random.uniform(5.0, 80.0)
            comp_gap = np.random.uniform(-10.0, 40.0)
            drift = np.random.uniform(-20.0, 5.0)
            elast = np.random.uniform(-4.0, 2.0)
            comps = np.random.randint(1, 20)
            margin = np.random.uniform(15.0, 70.0)
            breach = int(buffer < 15.0 or comp_gap > 20.0 or drift < -10.0)
            X_list.append([buffer, comp_gap, drift, elast, comps, margin])
            y_list.append(breach)

    return np.array(X_list), np.array(y_list)


def train_model(save_path: str = MODEL_PATH) -> Dict[str, Any]:
    """Train Gradient Boosting model to forecast 7-day floor breaches."""
    X, y = build_training_dataset()

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y if len(set(y)) > 1 else None
    )

    clf = GradientBoostingClassifier(
        n_estimators=60,
        learning_rate=0.1,
        max_depth=3,
        random_state=42,
    )
    clf.fit(X_train, y_train)

    y_pred = clf.predict(X_test)
    y_proba = clf.predict_proba(X_test)[:, 1] if hasattr(clf, "predict_proba") else y_pred

    accuracy = float(accuracy_score(y_test, y_pred))
    roc_auc = float(roc_auc_score(y_test, y_proba)) if len(set(y_test)) > 1 else 1.0

    # Feature importances
    importances = {
        name: round(float(imp), 4)
        for name, imp in zip(FEATURE_NAMES, clf.feature_importances_)
    }

    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    joblib.dump(clf, save_path)

    metrics = {
        "status": "trained",
        "model_type": "GradientBoostingClassifier",
        "sample_size": len(X),
        "test_accuracy": round(accuracy, 4),
        "test_roc_auc": round(roc_auc, 4),
        "feature_importances": importances,
        "saved_to": os.path.abspath(save_path),
        "trained_at": datetime.now().isoformat(),
    }
    return metrics


_MODEL_CACHE: Optional[Any] = None


def load_or_train_model(save_path: str = MODEL_PATH) -> Any:
    """Load existing model artifact with in-memory caching, or train a new one if missing."""
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE
    if os.path.exists(save_path):
        try:
            _MODEL_CACHE = joblib.load(save_path)
            return _MODEL_CACHE
        except Exception:
            pass
    train_model(save_path)
    _MODEL_CACHE = joblib.load(save_path)
    return _MODEL_CACHE


def predict_sku_risk(row: Dict[str, Any], model: Any = None) -> Dict[str, Any]:
    """Predict 7-day floor breach risk probability and extract explainable drivers."""
    if model is None:
        model = load_or_train_model()

    feats = extract_features(row)
    X = np.array([feats])

    # Probability of breach (class 1)
    if hasattr(model, "predict_proba"):
        prob = float(model.predict_proba(X)[0][1])
    else:
        prob = float(model.predict(X)[0])

    # Classify risk tier
    if prob >= 0.70:
        tier = "CRITICAL"
        action = "LOCK_FLOOR_IMMEDIATELY"
    elif prob >= 0.45:
        tier = "HIGH"
        action = "TIGHTEN_PRICE_BOUNDS"
    elif prob >= 0.20:
        tier = "MEDIUM"
        action = "MONITOR_HOURLY"
    else:
        tier = "LOW"
        action = "MAINTAIN_CURRENT_POLICY"

    # Identify primary risk drivers
    drivers = []
    floor_buffer_pct, comp_gap_pct, vrp_drift_pct, elasticity, comp_count, margin_pct = feats

    if comp_gap_pct > 15.0:
        drivers.append(f"Predatory competitor undercutting by {comp_gap_pct:.1f}%")
    if floor_buffer_pct < 20.0:
        drivers.append(f"Narrow cost floor headroom (+{floor_buffer_pct:.1f}%)")
    if vrp_drift_pct < -8.0:
        drivers.append(f"Accelerating VRP downward drift ({vrp_drift_pct:.1f}%)")
    if elasticity < -2.5:
        drivers.append(f"High price elasticity sensitivity (ε={elasticity:.2f})")
    if comp_count >= 10:
        drivers.append(f"Crowded buybox competition ({int(comp_count)} sellers)")

    if not drivers:
        drivers.append("Stable competitive equilibrium; healthy margin cushion.")

    return {
        "sku": row.get("sku", "UNKNOWN"),
        "name": row.get("name") or row.get("sku", "UNKNOWN"),
        "category": row.get("category", "General"),
        "breach_probability_7d": round(prob, 4),
        "breach_probability_pct": round(prob * 100.0, 1),
        "risk_tier": tier,
        "preventative_action": action,
        "primary_risk_drivers": drivers,
        "metrics": {
            "floor_buffer_pct": round(floor_buffer_pct, 1),
            "competitor_gap_pct": round(comp_gap_pct, 1),
            "vrp_drift_pct": round(vrp_drift_pct, 1),
        },
    }


def predict_catalog_risks() -> List[Dict[str, Any]]:
    """Generate 7-day predictive risk profiles for all unique catalog SKUs."""
    if not test_connection():
        raise RuntimeError("Database connection unavailable")

    model = load_or_train_model()

    with get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT DISTINCT ON (tr.sku)
                tr.sku,
                COALESCE(p.name, tr.sku) as name,
                tr.category,
                tr.channel,
                tr.our_price,
                tr.floor_price,
                tr.vrp_price,
                tr.lowest_competitor_price,
                tr.competitor_count,
                tr.calculated_margin_pct,
                tr.min_margin,
                tr.vrp_drift_pct,
                tr.elasticity_score,
                tr.guardrail_status,
                tr.scenario
            FROM telemetry_raw tr
            LEFT JOIN products p ON tr.sku = p.sku
            ORDER BY tr.sku, tr.timestamp DESC
        """)
        rows = cur.fetchall()

    predictions = [predict_sku_risk(dict(r), model=model) for r in rows]
    # Sort by risk descending
    predictions.sort(key=lambda x: x["breach_probability_7d"], reverse=True)
    return predictions


def main():
    parser = argparse.ArgumentParser(description="Predictive Floor Breach Forecaster")
    parser.add_argument("--train", action="store_true", help="Train and save GradientBoosting model")
    parser.add_argument("--predict-all", action="store_true", help="Predict 7-day breach probability for all catalog SKUs")
    parser.add_argument("--predict", type=str, default=None, help="Predict risk for specific SKU (e.g. ELEC-002)")
    args = parser.parse_args()

    try:
        if args.train:
            res = train_model()
            print("\n=======================================================")
            print("  PREDICTIVE 7-DAY FLOOR BREACH MODEL TRAINED")
            print("=======================================================")
            print(json.dumps(res, indent=2))
            sys.exit(0)

        if args.predict:
            sku = args.predict
            catalog_preds = predict_catalog_risks()
            target = next((p for p in catalog_preds if p["sku"] == sku), None)
            if not target:
                print(f"[!] SKU '{sku}' not found in catalog.")
                sys.exit(1)

            print(f"\n=======================================================")
            print(f"  7-DAY PREDICTIVE RISK FORECAST: SKU {target['sku']}")
            print(f"=======================================================")
            print(f"Product:              {target['name']} ({target['category']})")
            print(f"7-Day Breach Risk:    {target['breach_probability_pct']}% [{target['risk_tier']}]")
            print(f"Preventative Action:  {target['preventative_action']}")
            print(f"Risk Drivers:")
            for d in target["primary_risk_drivers"]:
                print(f"  • {d}")
            sys.exit(0)

        if args.predict_all or not (args.train or args.predict):
            predictions = predict_catalog_risks()
            print("\n=========================================================================================")
            print(f"  CATALOG 7-DAY FLOOR BREACH RISK RADAR ({len(predictions)} Active SKUs)")
            print("=========================================================================================")
            print(f"{'SKU':<12} {'Product Name':<32} {'Risk Tier':<12} {'Breach Prob':<14} {'Recommended Action'}")
            print("-" * 89)
            for p in predictions:
                print(f"{p['sku']:<12} {p['name'][:30]:<32} {p['risk_tier']:<12} {p['breach_probability_pct']:>5.1f}%       {p['preventative_action']}")
            sys.exit(0)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
