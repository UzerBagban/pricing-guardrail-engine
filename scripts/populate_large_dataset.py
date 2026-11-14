"""
High-Velocity Synthetic Telemetry Populator & Machine Learning Retraining Pipeline.

Generates 50,000+ realistic e-commerce telemetry events across 50 diverse SKU profiles,
persists them into PostgreSQL using bulk vectorized loading (execute_values), and retrains
the 7-Day ML Floor Breach Predictor model.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import psycopg2.extras
from src.ingestion.db import get_cursor, test_connection
from src.engine.predictive import train_model, MODEL_PATH
from src.telemetry.simulator import (
    ProductProfile,
    SCENARIO_GENERATORS,
    ScenarioType,
    generate_normal_fluctuation,
)

# 50 Expanded Realistic Product Profiles across 8 Marketplace Categories
EXPANDED_PRODUCT_PROFILES: List[ProductProfile] = [
    # Electronics
    ProductProfile("PROD-ELEC-001", "ELEC-001", "B08N5WRWNW", "Electronics", 79.99, 45.20, 79.99, 28.50, 3.50, 8.00, 12.00, 12, -1.8, 15.0),
    ProductProfile("PROD-ELEC-002", "ELEC-002", "B09KJRN3HH", "Electronics", 24.99, 18.35, 24.99, 12.75, 2.10, 3.00, 5.00, 8, -2.2, 45.0),
    ProductProfile("PROD-ELEC-003", "ELEC-003", "B08Z4M99LL", "Electronics", 149.99, 92.50, 149.99, 65.00, 6.50, 15.00, 20.00, 15, -1.4, 8.0),
    ProductProfile("PROD-ELEC-004", "ELEC-004", "B09H2VV411", "Electronics", 39.99, 24.50, 39.99, 16.00, 2.50, 4.00, 6.00, 9, -2.0, 32.0),
    ProductProfile("PROD-ELEC-005", "ELEC-005", "B07P89WW33", "Electronics", 219.99, 138.00, 219.99, 98.00, 8.00, 22.00, 30.00, 6, -1.2, 5.0),
    ProductProfile("PROD-ELEC-006", "ELEC-006", "B08R77TT55", "Electronics", 19.99, 12.50, 19.99, 7.50, 1.80, 2.00, 3.00, 18, -2.5, 60.0),
    ProductProfile("PROD-ELEC-007", "ELEC-007", "B09T11KK22", "Electronics", 89.99, 54.00, 89.99, 38.00, 4.00, 9.00, 14.00, 11, -1.6, 12.0),

    # Home & Kitchen
    ProductProfile("PROD-HOME-001", "HOME-001", "B07VV4KQZM", "Home & Kitchen", 34.99, 20.50, 34.99, 11.20, 3.80, 4.00, 5.50, 6, -1.5, 28.0),
    ProductProfile("PROD-HOME-002", "HOME-002", "B08M33NN44", "Home & Kitchen", 89.99, 52.00, 89.99, 34.00, 5.50, 9.00, 15.00, 8, -1.3, 14.0),
    ProductProfile("PROD-HOME-003", "HOME-003", "B07C55BB88", "Home & Kitchen", 18.99, 11.80, 18.99, 6.80, 2.20, 2.00, 3.00, 14, -2.1, 40.0),
    ProductProfile("PROD-HOME-004", "HOME-004", "B09D88FF11", "Home & Kitchen", 129.99, 78.50, 129.99, 52.00, 7.50, 13.00, 18.00, 5, -1.1, 7.0),
    ProductProfile("PROD-HOME-005", "HOME-005", "B08K22LL99", "Home & Kitchen", 45.99, 27.50, 45.99, 17.50, 3.20, 4.80, 7.00, 9, -1.7, 22.0),
    ProductProfile("PROD-HOME-006", "HOME-006", "B07X99PP33", "Home & Kitchen", 29.99, 17.90, 29.99, 10.50, 2.80, 3.20, 4.50, 11, -1.9, 35.0),

    # Beauty & Personal Care
    ProductProfile("PROD-BEAU-001", "BEAU-001", "B01N299856", "Beauty", 28.99, 13.75, 28.99, 6.50, 1.25, 3.00, 4.00, 15, -2.8, 65.0),
    ProductProfile("PROD-BEAU-002", "BEAU-002", "B078N2V53F", "Beauty", 39.99, 19.50, 39.99, 7.30, 1.40, 4.00, 6.80, 7, -1.9, 35.0),
    ProductProfile("PROD-BEAU-003", "BEAU-003", "B08F44JJ22", "Beauty", 16.99, 9.20, 16.99, 4.20, 1.10, 1.80, 2.50, 22, -3.1, 85.0),
    ProductProfile("PROD-BEAU-004", "BEAU-004", "B09M55VV88", "Beauty", 59.99, 28.00, 59.99, 14.50, 1.60, 6.00, 10.00, 5, -1.5, 18.0),
    ProductProfile("PROD-BEAU-005", "BEAU-005", "B07L33RR11", "Beauty", 22.50, 11.50, 22.50, 5.80, 1.20, 2.40, 3.50, 16, -2.4, 50.0),
    ProductProfile("PROD-BEAU-006", "BEAU-006", "B08T66WW99", "Beauty", 48.00, 24.00, 48.00, 12.00, 1.50, 5.00, 8.00, 8, -1.8, 25.0),

    # Sports & Outdoors
    ProductProfile("PROD-SPRT-001", "SPRT-001", "B081234567", "Sports & Outdoors", 18.99, 10.50, 18.99, 5.20, 1.80, 2.00, 3.00, 20, -2.4, 50.0),
    ProductProfile("PROD-SPRT-002", "SPRT-002", "B089876543", "Sports & Outdoors", 34.99, 21.00, 34.99, 12.50, 3.20, 3.50, 5.50, 8, -1.6, 20.0),
    ProductProfile("PROD-SPRT-003", "SPRT-003", "B09A11BB22", "Sports & Outdoors", 69.99, 42.00, 69.99, 26.00, 5.00, 7.00, 11.00, 6, -1.4, 12.0),
    ProductProfile("PROD-SPRT-004", "SPRT-004", "B07Z22CC33", "Sports & Outdoors", 119.99, 74.00, 119.99, 48.00, 8.00, 12.00, 16.00, 4, -1.2, 6.0),
    ProductProfile("PROD-SPRT-005", "SPRT-005", "B08K33DD44", "Sports & Outdoors", 24.99, 14.80, 24.99, 8.50, 2.50, 2.60, 4.00, 14, -2.2, 38.0),
    ProductProfile("PROD-SPRT-006", "SPRT-006", "B09N44EE55", "Sports & Outdoors", 49.99, 31.00, 49.99, 19.50, 3.80, 5.00, 8.00, 9, -1.7, 16.0),

    # Toys & Games
    ProductProfile("PROD-TOYS-001", "TOYS-001", "B082345678", "Toys & Games", 34.99, 18.50, 34.99, 9.80, 2.50, 3.50, 5.00, 10, -2.0, 30.0),
    ProductProfile("PROD-TOYS-002", "TOYS-002", "B088765432", "Toys & Games", 89.99, 51.20, 89.99, 32.50, 4.20, 9.00, 14.50, 5, -1.3, 12.0),
    ProductProfile("PROD-TOYS-003", "TOYS-003", "B09B55FF66", "Toys & Games", 14.99, 8.90, 14.99, 4.50, 1.50, 1.50, 2.20, 25, -2.9, 70.0),
    ProductProfile("PROD-TOYS-004", "TOYS-004", "B07C66GG77", "Toys & Games", 49.99, 29.50, 49.99, 17.00, 3.50, 5.20, 7.50, 8, -1.7, 24.0),
    ProductProfile("PROD-TOYS-005", "TOYS-005", "B08D77HH88", "Toys & Games", 129.99, 79.00, 129.99, 51.00, 7.00, 13.00, 18.00, 3, -1.1, 5.0),

    # Pet Supplies
    ProductProfile("PROD-PETS-001", "PETS-001", "B09E11JJ99", "Pet Supplies", 29.99, 17.50, 29.99, 10.00, 3.00, 3.00, 4.50, 14, -2.0, 42.0),
    ProductProfile("PROD-PETS-002", "PETS-002", "B08F22KK00", "Pet Supplies", 59.99, 36.00, 59.99, 22.00, 5.50, 6.00, 9.00, 7, -1.5, 18.0),
    ProductProfile("PROD-PETS-003", "PETS-003", "B07G33LL11", "Pet Supplies", 19.99, 11.90, 19.99, 6.50, 2.20, 2.00, 3.20, 19, -2.6, 55.0),
    ProductProfile("PROD-PETS-004", "PETS-004", "B09H44MM22", "Pet Supplies", 89.99, 55.00, 89.99, 36.00, 6.00, 9.00, 14.00, 5, -1.3, 10.0),
    ProductProfile("PROD-PETS-005", "PETS-005", "B08J55NN33", "Pet Supplies", 39.99, 23.50, 39.99, 14.00, 3.50, 4.00, 6.00, 11, -1.8, 26.0),

    # Health & Household
    ProductProfile("PROD-HLTH-001", "HLTH-001", "B07K66PP44", "Health & Household", 24.99, 13.50, 24.99, 7.00, 1.80, 2.50, 4.00, 16, -2.3, 50.0),
    ProductProfile("PROD-HLTH-002", "HLTH-002", "B09L77QQ55", "Health & Household", 79.99, 48.00, 79.99, 30.00, 4.50, 8.00, 12.00, 8, -1.4, 15.0),
    ProductProfile("PROD-HLTH-003", "HLTH-003", "B08M88RR66", "Health & Household", 15.99, 9.10, 15.99, 4.80, 1.40, 1.60, 2.50, 24, -3.0, 75.0),
    ProductProfile("PROD-HLTH-004", "HLTH-004", "B07N99SS77", "Health & Household", 119.99, 73.00, 119.99, 47.00, 6.50, 12.00, 17.00, 4, -1.2, 8.0),
    ProductProfile("PROD-HLTH-005", "HLTH-005", "B09P00TT88", "Health & Household", 34.99, 20.80, 34.99, 12.00, 2.80, 3.50, 5.50, 10, -1.7, 30.0),

    # Office Products
    ProductProfile("PROD-OFFC-001", "OFFC-001", "B08Q11UU99", "Office Products", 45.99, 28.00, 45.99, 17.00, 4.00, 4.50, 7.00, 9, -1.6, 20.0),
    ProductProfile("PROD-OFFC-002", "OFFC-002", "B07R22VV00", "Office Products", 189.99, 118.00, 189.99, 78.00, 12.00, 19.00, 25.00, 5, -1.2, 6.0),
    ProductProfile("PROD-OFFC-003", "OFFC-003", "B09S33WW11", "Office Products", 19.99, 12.00, 19.99, 6.80, 2.00, 2.00, 3.20, 15, -2.1, 45.0),
    ProductProfile("PROD-OFFC-004", "OFFC-004", "B08T44XX22", "Office Products", 89.99, 54.00, 89.99, 35.00, 5.50, 9.00, 13.50, 7, -1.5, 14.0),
    ProductProfile("PROD-OFFC-005", "OFFC-005", "B07U55YY33", "Office Products", 29.99, 18.20, 29.99, 10.50, 2.80, 3.00, 4.50, 12, -1.9, 32.0),
]


def generate_and_insert_dataset(total_target: int = 52500, batch_size: int = 5000) -> int:
    """Generate and persist 50,000+ realistic events into PostgreSQL."""
    if not test_connection():
        raise RuntimeError("PostgreSQL database connection failed!")

    print(f"=== Starting High-Velocity Telemetry Generator: Target = {total_target:,} records ===")
    t_start = time.time()

    # Scenario Weights matching real-world marketplace dynamics
    scenario_weights = {
        ScenarioType.NORMAL: 0.35,
        ScenarioType.COMPETITOR_UNDERCUT: 0.12,
        ScenarioType.MARGIN_COMPRESSION: 0.10,
        ScenarioType.AGGRESSIVE_COMPETITOR_UNDERCUT: 0.08,
        ScenarioType.VRP_DRIFT_DROP: 0.08,
        ScenarioType.VRP_DRIFT_SHOCK: 0.06,
        ScenarioType.COST_INFLATION_BREACH: 0.06,
        ScenarioType.BUYBOX_LOSS: 0.05,
        ScenarioType.PROMOTIONAL_SPIKE: 0.04,
        ScenarioType.ALGORITHMIC_REPRICING: 0.03,
        ScenarioType.STOCKOUT_COMPETITOR: 0.03,
    }

    # Precompute scenario sequence
    scenarios_list = []
    for sc, weight in scenario_weights.items():
        scenarios_list.extend([sc] * int(total_target * weight))
    while len(scenarios_list) < total_target:
        scenarios_list.append(random.choice(list(scenario_weights.keys())))
    random.shuffle(scenarios_list)

    # 90-day historical time horizon
    start_time = datetime.now(timezone.utc) - timedelta(days=90)
    time_step_sec = (90 * 86400) / total_target

    insert_sql = """
        INSERT INTO telemetry_raw (
            event_id, timestamp, product_id, sku, asin, category, channel,
            event_type, scenario, our_price, floor_price, vrp_price,
            buybox_price, lowest_competitor_price, competitor_count,
            cogs, shipping, fees, min_margin, calculated_margin_pct,
            vrp_drift_pct, vrp_threshold_pct, drift_detected,
            elasticity_score, elasticity_threshold, elasticity_breach,
            guardrail_status, suppression_triggered, suppression_reason,
            metadata, created_at
        ) VALUES %s
        ON CONFLICT (event_id) DO NOTHING
    """

    total_inserted = 0
    batch_records = []
    current_time = start_time

    print(f"Generating and batch-inserting into PostgreSQL in chunks of {batch_size:,}...")

    with get_cursor(commit=True) as cur:
        for i in range(total_target):
            profile = random.choice(EXPANDED_PRODUCT_PROFILES)
            sc = scenarios_list[i]
            generator = SCENARIO_GENERATORS.get(sc, generate_normal_fluctuation)

            rec = generator(profile, current_time)
            current_time += timedelta(seconds=time_step_sec)

            # Map to tuple for bulk insertion
            row_tuple = (
                rec.event_id,
                rec.timestamp,
                rec.product_id,
                rec.sku,
                rec.asin,
                rec.category,
                rec.channel,
                rec.event_type,
                rec.scenario,
                rec.our_price,
                rec.floor_price,
                rec.vrp_price,
                rec.buybox_price,
                rec.lowest_competitor_price,
                rec.competitor_count,
                rec.cogs,
                rec.shipping,
                rec.fees,
                rec.min_margin,
                rec.calculated_margin_pct,
                rec.vrp_drift_pct,
                rec.vrp_threshold_pct,
                rec.drift_detected,
                rec.elasticity_score,
                rec.elasticity_threshold,
                rec.elasticity_breach,
                rec.guardrail_status,
                rec.suppression_triggered,
                rec.suppression_reason,
                json.dumps(rec.metadata),
                datetime.now(timezone.utc),
            )
            batch_records.append(row_tuple)

            if len(batch_records) >= batch_size:
                psycopg2.extras.execute_values(cur, insert_sql, batch_records, page_size=batch_size)
                total_inserted += len(batch_records)
                batch_records = []
                print(f"  --> Ingested {total_inserted:,} / {total_target:,} records ({total_inserted/total_target*100:.1f}%)")

        if batch_records:
            psycopg2.extras.execute_values(cur, insert_sql, batch_records, page_size=len(batch_records))
            total_inserted += len(batch_records)

    elapsed = time.time() - t_start
    print(f"Successfully populated {total_inserted:,} events into PostgreSQL in {elapsed:.2f}s ({total_inserted/elapsed:,.0f} rows/s)!")

    # Verify count in database
    with get_cursor(commit=False) as cur:
        cur.execute("SELECT COUNT(*) AS total, COUNT(DISTINCT sku) AS unique_skus, COUNT(DISTINCT category) AS categories FROM telemetry_raw")
        res = cur.fetchone()
        print(f"Database Verification: Total Rows = {res['total']:,}, SKUs = {res['unique_skus']}, Categories = {res['categories']}")

    return total_inserted


def retrain_model_on_large_dataset() -> Dict[str, Any]:
    """Retrain the GradientBoosting model on the expanded 50,000+ dataset."""
    print("\n=== Retraining 7-Day Floor Breach Forecaster on Expanded Dataset ===")
    t_start = time.time()

    metrics = train_model(save_path=MODEL_PATH)
    elapsed = time.time() - t_start

    print(f"Retraining Completed in {elapsed:.2f} seconds!")
    print(f"Model Summary:")
    print(f"  • Model Type:      {metrics.get('model_type')}")
    print(f"  • Sample Size:     {metrics.get('sample_size'):,} events")
    print(f"  • Test Accuracy:   {metrics.get('test_accuracy') * 100:.2f}%")
    print(f"  • Test ROC-AUC:    {metrics.get('test_roc_auc'):.4f}")
    print(f"  • Saved Artifact:  {metrics.get('saved_to')}")
    print(f"  • Feature Importances:")
    for feat, imp in sorted(metrics.get("feature_importances", {}).items(), key=lambda x: x[1], reverse=True):
        print(f"      - {feat:25s}: {imp * 100:5.2f}%")

    return metrics


def main():
    parser = argparse.ArgumentParser(description="Populate 50,000+ telemetry events and retrain ML forecaster.")
    parser.add_argument("--count", type=int, default=52500, help="Number of telemetry events to generate (default: 52,500)")
    parser.add_argument("--batch-size", type=int, default=5000, help="Database batch insert size (default: 5,000)")
    args = parser.parse_args()

    # 1. Ingest expanded dataset
    generate_and_insert_dataset(total_target=args.count, batch_size=args.batch_size)

    # 2. Retrain machine learning model
    retrain_model_on_large_dataset()


if __name__ == "__main__":
    main()
