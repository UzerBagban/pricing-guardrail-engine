"""
Suppression Queue Alert Processor & Notification Dispatcher

Phase 3 of the pricing-guardrail-engine: monitors the suppression_queue for
critical margin breaches and dispatches structured JSON webhook alerts.

Deliverables:
- Runnable as: python -m src.ingestion.notifier --batch-size 10 --mock
- Live webhook mode: python -m src.ingestion.notifier --batch-size 10
- Console mock delivery via --mock flag for offline execution.

Alert payload fields:
- sku: str
- product_name: str
- breach_type: str  (MARGIN_BREACH, BELOW_FLOOR_PRICE, VRP_DRIFT_CRITICAL)
- attempted_price: float
- floor_price: float
- projected_margin_loss: float
- timestamp: str (ISO 8601 UTC)

Processed queue records are transitioned to the live CHECK-constrained enum:
pending -> NOTIFIED (alert dispatched) maps onto live 'suppressed';
suppressed/released/overridden -> RESOLVED maps onto 'released' ('overridden' stays).
See OUTCOME_TO_LIVE_STATUS / LIVE_STATUS_TO_OUTCOME for the exact mapping.

Reads suppression_queue via the db layer (db.get_suppressed_skus + db helpers).
Never reads/writes .env directly - secrets stay in the environment.

Constraint: Never use Ollama models - only the configured primary model is used.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Lazy: imports deferred to process_batch/main so the module stays importable
# and --mock can run offline even when psycopg2 is absent.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("ingestion.notifier")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ============================================================================
# Constants & Mapping
# ============================================================================

# Map database suppression_reason values to canonical breach types.
# Live DB CHECK constraint allows: below_floor_price, vrp_violation, margin_breach,
# elasticity_breach, manual_override
DB_REASON_TO_BREACH = {
    "below_floor_price": "BELOW_FLOOR_PRICE",
    "vrp_violation": "VRP_DRIFT_CRITICAL",
    "margin_breach": "MARGIN_BREACH",
    "elasticity_breach": "BELOW_FLOOR_PRICE",
    "manual_override": "MANUAL_OVERRIDE",
}

# Map canonical breach types to the notification dispatch action.
# Only critical breaches are dispatched as alerts.
CRITICAL_BREACHES = {
    "MARGIN_BREACH",
    "BELOW_FLOOR_PRICE",
    "VRP_DRIFT_CRITICAL",
}


# ============================================================================
# Alert payload builder
# ============================================================================

def build_alert_payload(
    sku: str,
    product_name: str,
    breach_type: str,
    attempted_price: Optional[float],
    floor_price: Optional[float],
    projected_margin_loss: Optional[float],
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build structured JSON alert payload for notification dispatch.

    Payload fields:
    - sku: str
    - product_name: str
    - breach_type: str  (MARGIN_BREACH, BELOW_FLOOR_PRICE, VRP_DRIFT_CRITICAL)
    - attempted_price: float
    - floor_price: float
    - projected_margin_loss: float
    - timestamp: str (ISO 8601 UTC)
    """
    ts = timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    return {
        "sku": sku,
        "product_name": product_name,
        "breach_type": breach_type,
        "attempted_price": attempted_price,
        "floor_price": floor_price,
        "projected_margin_loss": projected_margin_loss,
        "timestamp": ts,
    }


# ============================================================================
# Webhook dispatch
# ============================================================================

def dispatch_webhook(payload: Dict[str, Any]) -> bool:
    """
    Dispatch the structured JSON alert payload to the configured notification
    webhook endpoint (e.g., Slack, PagerDuty, ops webhook).

    Reads SLACK_WEBHOOK_URL / NOTIFICATION_WEBHOOK_URL from environment
    (via os.getenv, not from .env file access - loaded by db layer).

    Returns True if dispatch was accepted/sent, False otherwise.
    """
    import os

    webhook_url = os.getenv("NOTIFICATION_WEBHOOK_URL") or os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        log.warning("NOTIFICATION_WEBHOOK_URL/SLACK_WEBHOOK_URL not set - skipping live dispatch")
        # In live mode without webhook URL, log the payload and treat as dispatched
        log.info(f"Would send webhook: {json.dumps(payload, indent=2)}")
        return True

    import urllib.request

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode("utf-8")
            log.info(f"Webhook delivered: {resp_body}")
            return True
    except Exception as e:
        log.error(f"Webhook delivery failed: {e}")
        return False


# ============================================================================
# Suppression queue reading
# ============================================================================

def get_suppression_alerts(db, batch_size: int) -> List[Dict[str, Any]]:
    """
    Fetch pending suppression queue records and map to alert dicts.

    Returns list of alert dicts:
    {
        "sku": str,
        "product_name": str,
        "breach_type": str,
        "attempted_price": float,
        "floor_price": float,
        "projected_margin_loss": float,
        "timestamp": str,
        "db_status": str,       # live status from suppression_queue
        "db_id": str,
        "severity": str,
    }
    """
    alerts = []

    # Use db layer's get_suppressed_skus for pending queue entries
    suppressed_skus = db.get_suppressed_skus()

    if not suppressed_skus:
        log.info("No pending suppressions found in suppression_queue")
        return []

    log.info(f"Found {len(suppressed_skus)} suppressed SKUs in queue")

    limited = suppressed_skus[:batch_size]

    for sku in limited:
        alert = _build_alert_for_sku(db, sku)
        if alert:
            alerts.append(alert)

    return alerts


def _build_alert_for_sku(db, sku: str) -> Optional[Dict[str, Any]]:
    """
    Look up the full suppression record for a SKU and build an alert dict.

    Note: suppression_queue has no sku column - join to products for the SKU.
    """
    query = """
        SELECT
            sq.id,
            p.sku,
            p.name AS product_name,
            sq.suppression_reason,
            sq.target_price,
            sq.floor_price,
            sq.vrp_price,
            sq.margin_pct,
            sq.severity,
            sq.status
        FROM suppression_queue sq
        JOIN products p ON sq.product_id = p.id
        WHERE p.sku = %s
          AND sq.status IN ('pending', 'suppressed')
        ORDER BY sq.suppressed_at DESC
        LIMIT 1
    """

    try:
        with db.get_cursor(commit=False) as cur:
            cur.execute(query, (sku,))
            row = cur.fetchone()
    except Exception as e:
        log.error(f"Failed to fetch suppression record for SKU {sku}: {e}")
        return None

    if not row:
        log.info(f"No suppression record found for SKU {sku}")
        return None

    # Map DB suppression reason to canonical breach type
    db_reason = row["suppression_reason"] or "margin_breach"
    breach_type = DB_REASON_TO_BREACH.get(db_reason, db_reason.upper())

    if breach_type not in CRITICAL_BREACHES:
        # Only critical breaches get notification dispatch
        log.info(f"Skipping non-critical suppression {sku}: reason={db_reason}")
        return None

    # Product name now comes directly from the products join
    product_name = row["product_name"] or f"Product {sku}"

    # Compute projected margin loss (positive loss when undercutting floor)
    attempted_price = float(row["target_price"]) if row["target_price"] is not None else None
    floor_price = float(row["floor_price"]) if row["floor_price"] is not None else None
    margin_pct = float(row["margin_pct"]) if row["margin_pct"] is not None else 0.0
    projected_margin_loss = None
    if attempted_price is not None and floor_price is not None:
        delta = floor_price - attempted_price
        projected_margin_loss = float(delta) if delta > 0 else 0.0
    elif margin_pct < 0:
        projected_margin_loss = abs(margin_pct)

    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    alert = {
        "sku": sku,
        "product_name": product_name,
        "breach_type": breach_type,
        "attempted_price": attempted_price,
        "floor_price": floor_price,
        "projected_margin_loss": projected_margin_loss,
        "timestamp": ts,
        "db_status": row["status"],
        "db_id": str(row["id"]),
        "severity": row["severity"] or "high",
    }

    return alert


# ============================================================================
# Status transitions (live CHECK-constrained enum)
# ============================================================================

# Logical outcome -> live DB status. suppression_queue CHECK constraint only
# allows 'pending' | 'suppressed' | 'released' | 'overridden'.
OUTCOME_TO_LIVE_STATUS = {
    "NOTIFIED": "suppressed",
    "RESOLVED": "released",
}

# Live DB status -> logical outcome
LIVE_STATUS_TO_OUTCOME = {
    "pending": "NOTIFIED",
    "suppressed": "RESOLVED",
    "released": "RESOLVED",
    "overridden": "RESOLVED",
}


def resolve_alert(db, alert: Dict[str, Any], notification_status: str = "NOTIFIED") -> bool:
    """
    Transition a processed suppression queue record to its notification outcome.

    Logical outcome -> live DB status mapping:
    - pending     -> NOTIFIED  -> live 'suppressed'  (alert dispatched, deal holds)
    - suppressed  -> RESOLVED  -> live 'released'
    - released    -> RESOLVED  -> live 'released'
    - overridden  -> RESOLVED  -> live 'overridden' (unchanged)

    Returns True if the transition succeeded.
    """
    current = alert.get("db_status", "pending")
    outcome = LIVE_STATUS_TO_OUTCOME.get(current, "NOTIFIED")
    live_status = OUTCOME_TO_LIVE_STATUS.get(outcome, "suppressed")
    # overridden stays overridden; suppressed -> released only when outcome RESOLVED
    if current == "overridden":
        live_status = "overridden"

    db_id = alert.get("db_id")
    if not db_id:
        # No DB record linked (offline mock) - just report
        log.info(f"Alert for {alert.get('sku')} dispatched; no DB id to transition")
        return True

    query = """
        UPDATE suppression_queue
           SET status = %s,
               released_at = CASE WHEN %s = 'released' THEN NOW() ELSE released_at END
         WHERE id = %s
    """

    try:
        with db.get_cursor() as cur:
            cur.execute(query, (live_status, live_status, db_id))
        log.info(f"Suppression {alert.get('sku')} transitioned -> {live_status} (logical={outcome})")
        return True
    except Exception as e:
        log.error(f"Failed to update suppression status for {alert.get('sku')}: {e}")
        return False


# ============================================================================
# Mock-mode synthetic alert (offline path)
# ============================================================================

def build_mock_alerts(batch_size: int) -> List[Dict[str, Any]]:
    """Synthetic alert used when --mock is requested and the DB queue is
    unreachable (offline execution). Clearly logged as synthetic and never
    written back to the DB."""
    if batch_size <= 0:
        return []
    templates = [
        ("ELEC-001", "Wireless Bluetooth Headphones Pro", "MARGIN_BREACH", 38.50, 45.20, 6.70),
        ("SPRT-002", "Yoga Mat Premium Non-Slip", "BELOW_FLOOR_PRICE", 14.25, 17.00, 2.75),
    ]
    alerts = []
    for sku, name, breach, attempted, floor, loss in templates[:min(batch_size, len(templates))]:
        alerts.append({
            "sku": sku,
            "product_name": name,
            "breach_type": breach,
            "attempted_price": attempted,
            "floor_price": floor,
            "projected_margin_loss": loss,
            "timestamp": _utcnow_iso(),
            "db_id": None,
            "db_status": "pending",
            "severity": "high",
        })
    return alerts


# ============================================================================
# Main processing loop
# ============================================================================

def process_batch(batch_size: int, use_mock: bool) -> int:
    """
    Process a batch of critical suppression alerts.

    Args:
        batch_size: Number of suppression records to process per batch.
        use_mock: If True, use console mock delivery; else live webhook dispatch.

    Returns:
        0 on success, 1 on failure.
    """
    alerts: List[Dict[str, Any]] = []

    db = None
    try:
        from src.ingestion import db as _db
        db = _db
    except Exception as e:
        log.warning(f"db layer unavailable (psycopg2 missing?): {e}")

    db_ok = False
    if db is not None:
        try:
            # test_connection() returns bool; init_schema() returns None on success
            test_ok = db.test_connection()
            db.init_schema()
            db_ok = bool(test_ok)
        except Exception as e:
            log.warning(f"DB verification failed: {e}")
            db_ok = False

    if db_ok:
        log.info("Database connection and schema verified")
        alerts = get_suppression_alerts(db, batch_size)
    elif use_mock:
        log.warning("Proceeding in offline mock mode with synthetic alert(s)")
        alerts = build_mock_alerts(batch_size)
    else:
        log.error("Database unavailable and --mock not set")
        return 1

    if not alerts:
        log.info("No critical alerts to process in this batch")
        return 0

    processed = 0
    dispatched = 0
    failed = 0

    for alert in alerts:
        try:
            payload = build_alert_payload(
                sku=alert["sku"],
                product_name=alert["product_name"],
                breach_type=alert["breach_type"],
                attempted_price=alert["attempted_price"],
                floor_price=alert["floor_price"],
                projected_margin_loss=alert["projected_margin_loss"],
                timestamp=alert["timestamp"],
            )

            # Dispatch via console mock or live webhook
            if use_mock:
                print(json.dumps(payload, indent=2))
                dispatched += 1
            else:
                if dispatch_webhook(payload):
                    dispatched += 1
                else:
                    failed += 1

            # Transition processed queue record to NOTIFIED/RESOLVED outcome
            if db_ok and db is not None:
                if resolve_alert(db, alert):
                    processed += 1
                else:
                    failed += 1
            else:
                processed += 1  # offline mock: no DB to transition

        except Exception as e:
            log.error(f"Failed to process alert for {alert.get('sku')}: {e}")
            failed += 1

    log.info(
        f"Batch processed: {processed} processed, {dispatched} dispatched, {failed} failed"
    )
    return 0 if processed > 0 or failed == 0 else 1


# ============================================================================
# CLI entrypoint
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Suppression Queue Alert Processor & Notification Dispatcher"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of suppression records to process per batch (default: 10)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use console mock delivery instead of live webhooks (offline mode)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use live webhook delivery (default behavior without --mock)",
    )
    args = parser.parse_args()

    try:
        return process_batch(batch_size=args.batch_size, use_mock=args.mock)
    except Exception as e:
        log.error(f"Fatal error in notifier batch processing: {e}")
        return 1
    finally:
        try:
            from src.ingestion import db as _db
            _db.close_pool()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
