"""Executive Audit Metrics & Terminal Dashboard for Phase 3 of Pricing Guardrail Engine."""

import argparse
import json
from typing import Dict, Any
from src.ingestion.db import get_connection, test_connection, init_schema, get_cursor


def _count(sql: str, params: tuple = ()) -> int:
    with get_cursor(commit=False) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return 0
        return int(list(row.values())[0])


def get_telemetry_stats() -> Dict[str, Any]:
    """Fetch telemetry statistics from the database."""
    if not test_connection():
        raise RuntimeError("Database connection failed: test_connection() returned False")

    try:
        init_schema()
    except Exception:
        pass

    total_events = _count("SELECT COUNT(*) FROM telemetry_raw")
    suppressions_triggered = _count("SELECT COUNT(*) FROM telemetry_raw WHERE suppression_triggered = true")
    total_suppressions = _count("SELECT COUNT(*) FROM suppression_queue")
    vrp_drift_events_total = _count("SELECT COUNT(*) FROM vrp_drift_events")

    # VRP drift severity breakdown
    severity_breakdown = []
    try:
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT severity, COUNT(*) AS count
                FROM vrp_drift_events
                GROUP BY severity
                ORDER BY count DESC
            """)
            severity_breakdown = [{"severity": row["severity"], "count": int(row["count"])} for row in (cur.fetchall() or [])]
    except Exception:
        severity_breakdown = []

    # Top risk SKUs by margin pressure (margin below floor)
    top_risk_skus = []
    try:
        with get_cursor(commit=False) as cur:
            cur.execute("""
                SELECT
                    tr.sku,
                    p.name,
                    tr.category,
                    tr.calculated_margin_pct,
                    tr.min_margin,
                    ((tr.min_margin - tr.calculated_margin_pct) / NULLIF(tr.min_margin, 0)) * 100 AS margin_pressure_pct
                FROM telemetry_raw tr
                LEFT JOIN products p ON tr.sku = p.sku
                WHERE tr.calculated_margin_pct IS NOT NULL AND tr.min_margin > 0
                  AND tr.calculated_margin_pct < tr.min_margin
                ORDER BY margin_pressure_pct DESC NULLS LAST
                LIMIT 20
            """)
            top_risk_skus = [
                {
                    "sku": row["sku"],
                    "name": row.get("name") or row["sku"],
                    "category": row.get("category"),
                    "calculated_margin_pct": float(row["calculated_margin_pct"]),
                    "min_margin_pct": float(row["min_margin"]),
                    "margin_pressure_pct": round(float(row["margin_pressure_pct"] or 0), 4),
                }
                for row in (cur.fetchall() or [])
            ]
    except Exception:
        top_risk_skus = []

    return {
        "total_events_ingested": total_events,
        "suppressions_triggered": suppressions_triggered,
        "total_suppressions_in_queue": total_suppressions,
        "vrp_drift_events_total": vrp_drift_events_total,
        "vrp_drift_severity_breakdown": severity_breakdown,
        "top_risk_skus": top_risk_skus,
    }


def main():
    """Main entry point for the executive dashboard."""
    import sys

    parser = argparse.ArgumentParser(description="Executive Audit Metrics Dashboard")
    parser.add_argument('--summary', action='store_true', help='Show summary metrics')
    parser.add_argument('--export-json', type=str, default=None, help='Path to export metrics JSON file')
    args = parser.parse_args()

    try:
        stats = get_telemetry_stats()
        summary = {"executive_summary": stats}

        if args.export_json:
            with open(args.export_json, "w") as f:
                json.dump(summary, f, indent=2)
            print(f"Metrics exported to: {args.export_json}")

        if args.summary:
            print("Executive Dashboard Summary:")
            print(json.dumps(summary, indent=2))
        else:
            print(json.dumps(summary, indent=2))

        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
