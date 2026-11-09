"""
Ingestion Pipeline DB Layer
Dedicated DB module for parameterized queries and connection management.
All SQL uses %s placeholders - zero string formatting.
"""

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Generator, Optional, Any

import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from pathlib import Path
import logging

log = logging.getLogger("ingestion.db")

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH, override=False)


class DatabaseConfigError(Exception):
    """Raised when database configuration is missing or invalid."""
    pass


def get_database_url() -> str:
    """Get DATABASE_URL from environment. Raises DatabaseConfigError if not set."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise DatabaseConfigError(
            "DATABASE_URL not set in environment. "
            f"Ensure .env exists at {ENV_PATH} with DATABASE_URL configured."
        )
    return database_url


def get_pool_config() -> dict:
    """Get connection pool configuration from environment with sensible defaults."""
    return {
        "minconn": int(os.getenv("DB_POOL_MIN", "1")),
        "maxconn": int(os.getenv("DB_POOL_MAX", "10")),
    }


# Module-level connection pool (lazy initialization)
_pool: Optional[SimpleConnectionPool] = None


def get_connection_pool() -> SimpleConnectionPool:
    """Get or create the global connection pool."""
    global _pool
    if _pool is None:
        database_url = get_database_url()
        pool_config = get_pool_config()
        _pool = SimpleConnectionPool(
            minconn=pool_config["minconn"],
            maxconn=pool_config["maxconn"],
            dsn=database_url,
            cursor_factory=RealDictCursor,
        )
    return _pool


@contextmanager
def get_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context manager for a database connection from the pool.
    Automatically returns connection to pool on exit.
    """
    pool = get_connection_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_cursor(commit: bool = True) -> Generator[psycopg2.extras.RealDictCursor, None, None]:
    """
    Context manager for a database cursor.
    Commits by default, rolls back on exception.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            yield cursor
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()


def init_schema() -> None:
    """
    Initialize ingestion schema - creates pricing_events and suppression_queue tables if not exist.
    Note: Full schema is managed by migrations (001_init.sql, 002_vrp_tables.sql, 003_audit_log.sql).
    This function verifies the tables exist.
    """
    # Tables are created by migrations. This function can be used to verify.
    verification_queries = [
        "SELECT 1 FROM price_change_log LIMIT 1",
        "SELECT 1 FROM suppression_queue LIMIT 1",
        "SELECT 1 FROM vrp_drift_events LIMIT 1",
    ]
    with get_cursor(commit=False) as cursor:
        for query in verification_queries:
            cursor.execute(query)
            cursor.fetchone()  # Just verify table exists and is queryable


@dataclass
class TelemetryEvent:
    """Inbound telemetry event structure."""
    event_id: Optional[str] = None
    timestamp: Optional[str] = None
    product_id: str = ""
    sku: Optional[str] = None
    asin: Optional[str] = None
    category: Optional[str] = None
    channel: Optional[str] = None
    event_type: Optional[str] = None
    scenario: Optional[str] = None
    our_price: Optional[float] = None
    floor_price: Optional[float] = None
    vrp_price: Optional[float] = None
    buybox_price: Optional[float] = None
    lowest_competitor_price: Optional[float] = None
    competitor_count: Optional[int] = None
    cogs: Optional[float] = None
    shipping: Optional[float] = None
    fees: Optional[float] = None
    min_margin: Optional[float] = None
    calculated_margin_pct: Optional[float] = None
    vrp_drift_pct: Optional[float] = None
    vrp_threshold_pct: Optional[float] = None
    drift_detected: Optional[bool] = None
    elasticity_score: Optional[float] = None
    elasticity_threshold: Optional[float] = None
    elasticity_breach: Optional[bool] = None
    guardrail_status: Optional[str] = None
    suppression_triggered: Optional[bool] = None
    suppression_reason: Optional[str] = None
    metadata: Optional[dict] = None
    created_at: Optional[datetime] = None


@dataclass
class EvaluationResult:
    """Result of evaluating a telemetry payload."""
    product_id: str
    proposed_price: float
    floor_price: float
    margin_pct: float
    vrp_drift_pct: Optional[float]
    vrp_severity: Optional[str]
    guardrail_action: str
    warnings: list
    timestamp: datetime


# ============================================================================
# Functions expected by evaluator.py (compatibility layer)
# ============================================================================

def persist_event(event: TelemetryEvent) -> bool:
    """Persist a telemetry event to telemetry_raw table."""
    query = """
        INSERT INTO telemetry_raw (
            event_id, timestamp, product_id, sku, asin, category, channel,
            event_type, scenario, our_price, floor_price, vrp_price,
            buybox_price, lowest_competitor_price, competitor_count,
            cogs, shipping, fees, min_margin, calculated_margin_pct,
            vrp_drift_pct, vrp_threshold_pct, drift_detected,
            elasticity_score, elasticity_threshold, elasticity_breach,
            guardrail_status, suppression_triggered, suppression_reason,
            metadata, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (event_id) DO NOTHING
    """
    try:
        with get_cursor() as cur:
            cur.execute(query, (
                event.event_id,
                event.timestamp,
                event.product_id,
                event.sku,
                event.asin,
                event.category,
                event.channel,
                event.event_type,
                event.scenario,
                event.our_price,
                event.floor_price,
                event.vrp_price,
                event.buybox_price,
                event.lowest_competitor_price,
                event.competitor_count,
                event.cogs,
                event.shipping,
                event.fees,
                event.min_margin,
                event.calculated_margin_pct,
                event.vrp_drift_pct,
                event.vrp_threshold_pct,
                event.drift_detected,
                event.elasticity_score,
                event.elasticity_threshold,
                event.elasticity_breach,
                event.guardrail_status,
                event.suppression_triggered,
                event.suppression_reason,
                event.metadata,
                event.created_at or datetime.utcnow()
            ))
        return True
    except Exception as e:
        log.error(f"Failed to persist telemetry event: {e}")
        return False


def get_product_id_by_sku(sku: str) -> str | None:
    """
    Look up product UUID by SKU from products table.
    Returns the product_id (UUID) if found, None otherwise.
    """
    query = "SELECT product_id FROM products WHERE sku = %s AND is_active = true"
    try:
        with get_cursor(commit=False) as cursor:
            cursor.execute(query, (sku,))
            row = cursor.fetchone()
            return str(row["product_id"]) if row else None
    except Exception as e:
        log.error(f"Failed to lookup product_id for SKU {sku}: {e}")
        return None


def cleanup_expired_suppressions() -> int:
    """
    Clean up expired suppression entries (released_at in the past).
    Returns number of rows updated.
    """
    query = """
        UPDATE suppression_queue
        SET status = 'released', released_at = NOW()
        WHERE status IN ('pending', 'suppressed')
          AND released_at IS NOT NULL
          AND released_at < NOW()
    """
    try:
        with get_cursor() as cursor:
            cursor.execute(query)
            return cursor.rowcount
    except Exception as e:
        log.error(f"Failed to cleanup expired suppressions: {e}")
        return 0


# ============================================================================
# Functions expected by evaluator.py (compatibility layer)
# ============================================================================

def persist_event(event: TelemetryEvent) -> bool:
    """Persist a telemetry event to telemetry_raw table."""
    query = """
        INSERT INTO telemetry_raw (
            event_id, timestamp, product_id, sku, asin, category, channel,
            event_type, scenario, our_price, floor_price, vrp_price,
            buybox_price, lowest_competitor_price, competitor_count,
            cogs, shipping, fees, min_margin, calculated_margin_pct,
            vrp_drift_pct, vrp_threshold_pct, drift_detected,
            elasticity_score, elasticity_threshold, elasticity_breach,
            guardrail_status, suppression_triggered, suppression_reason,
            metadata, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (event_id) DO NOTHING
    """
    try:
        with get_cursor() as cur:
            cur.execute(query, (
                event.event_id,
                event.timestamp,
                event.product_id,
                event.sku,
                event.asin,
                event.category,
                event.channel,
                event.event_type,
                event.scenario,
                event.our_price,
                event.floor_price,
                event.vrp_price,
                event.buybox_price,
                event.lowest_competitor_price,
                event.competitor_count,
                event.cogs,
                event.shipping,
                event.fees,
                event.min_margin,
                event.calculated_margin_pct,
                event.vrp_drift_pct,
                event.vrp_threshold_pct,
                event.drift_detected,
                event.elasticity_score,
                event.elasticity_threshold,
                event.elasticity_breach,
                event.guardrail_status,
                event.suppression_triggered,
                event.suppression_reason,
                event.metadata,
                event.created_at or datetime.utcnow()
            ))
        return True
    except Exception as e:
        log.error(f"Failed to persist telemetry event: {e}")
        return False


def persist_batch(events: list) -> int:
    """Persist multiple telemetry events in batch."""
    if not events:
        return 0
    
    query = """
        INSERT INTO telemetry_raw (
            event_id, timestamp, product_id, sku, asin, category, channel,
            event_type, scenario, our_price, floor_price, vrp_price,
            buybox_price, lowest_competitor_price, competitor_count,
            cogs, shipping, fees, min_margin, calculated_margin_pct,
            vrp_drift_pct, vrp_threshold_pct, drift_detected,
            elasticity_score, elasticity_threshold, elasticity_breach,
            guardrail_status, suppression_triggered, suppression_reason,
            metadata, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (event_id) DO NOTHING
    """
    success = 0
    try:
        with get_cursor() as cur:
            for event in events:
                try:
                    cur.execute(query, (
                        event.event_id,
                        event.timestamp,
                        event.product_id,
                        event.sku,
                        event.asin,
                        event.category,
                        event.channel,
                        event.event_type,
                        event.scenario,
                        event.our_price,
                        event.floor_price,
                        event.vrp_price,
                        event.buybox_price,
                        event.lowest_competitor_price,
                        event.competitor_count,
                        event.cogs,
                        event.shipping,
                        event.fees,
                        event.min_margin,
                        event.calculated_margin_pct,
                        event.vrp_drift_pct,
                        event.vrp_threshold_pct,
                        event.drift_detected,
                        event.elasticity_score,
                        event.elasticity_threshold,
                        event.elasticity_breach,
                        event.guardrail_status,
                        event.suppression_triggered,
                        event.suppression_reason,
                        event.metadata,
                        event.created_at or datetime.utcnow()
                    ))
                    success += 1
                except Exception as e:
                    log.error(f"Failed to persist event {event.event_id}: {e}")
    except Exception as e:
        log.error(f"Batch persist failed: {e}")
    
    log.info(f"Persisted {success}/{len(events)} telemetry events")
    return success


def get_suppressed_skus() -> list:
    """Get list of SKUs currently in suppression queue with pending status."""
    query = """
        SELECT p.sku
        FROM suppression_queue sq
        JOIN products p ON sq.product_id = p.id
        WHERE sq.status = 'pending'
    """
    try:
        with get_cursor() as cur:
            cur.execute(query)
            return [row["sku"] for row in cur.fetchall()]
    except Exception as e:
        log.error(f"Failed to get suppressed SKUs: {e}")
        return []


def add_suppression(
    sku: str,
    asin: Optional[str],
    channel: str,
    reason: str,
    target_price: float,
    floor_price: float,
    vrp_price: Optional[float],
    margin_pct: float,
    elasticity_score: Optional[float],
    severity: str
) -> bool:
    """Add a suppression entry to the queue."""
    product_uuid = get_product_id_by_sku(sku)
    if not product_uuid:
        log.warning(f"Product not found for SKU: {sku}")
        return False
    
    query = """
        INSERT INTO suppression_queue (
            product_id, asin, channel, suppression_reason,
            target_price, floor_price, vrp_price, margin_pct,
            elasticity_score, severity, status, suppressed_at, released_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    try:
        with get_cursor() as cur:
            cur.execute(query, (
                product_uuid, asin, channel, reason,
                target_price, floor_price, vrp_price, margin_pct,
                elasticity_score, severity, "pending",
                datetime.utcnow(), None
            ))
        log.info(f"Added suppression for SKU {sku}: {reason}")
        return True
    except Exception as e:
        log.error(f"Failed to add suppression: {e}")
        return False


def log_vrp_drift_event(
    sku: str,
    asin: Optional[str],
    drift_type: str,
    severity: str,
    previous_vrp: float,
    current_vrp: float,
    drift_pct: float,
    threshold_pct: float,
    our_price: float,
    floor_price: float,
    recommended_action: str
) -> bool:
    """Log a VRP drift event."""
    product_uuid = get_product_id_by_sku(sku)
    if not product_uuid:
        return False
    
    query = """
        INSERT INTO vrp_drift_events (
            product_id, asin, drift_type, severity, previous_vrp, current_vrp,
            drift_pct, threshold_pct, our_price_at_event, floor_price_at_event,
            recommended_action, status, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    try:
        with get_cursor() as cur:
            cur.execute(query, (
                product_uuid, asin, drift_type, severity,
                previous_vrp, current_vrp, drift_pct, threshold_pct,
                our_price, floor_price, recommended_action,
                "new", datetime.utcnow()
            ))
        return True
    except Exception as e:
        log.error(f"Failed to log VRP drift event: {e}")
        return False


def log_price_change(
    sku: str,
    change_type: str,
    old_price: Optional[float],
    new_price: float,
    floor_price: float,
    vrp_price: Optional[float],
    margin_before: Optional[float],
    margin_after: float,
    elasticity_score: Optional[float],
    triggered_by: str,
    metadata: dict
) -> bool:
    """Log a price change decision."""
    product_uuid = get_product_id_by_sku(sku)
    if not product_uuid:
        return False
    
    query = """
        INSERT INTO price_change_log (
            product_id, change_type, old_price, new_price, floor_price, vrp_price,
            margin_pct_before, margin_pct_after, elasticity_score, triggered_by, metadata, executed_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    try:
        with get_cursor() as cur:
            import json
            cur.execute(query, (
                product_uuid, change_type, old_price, new_price, floor_price, vrp_price,
                margin_before, margin_after, elasticity_score, triggered_by,
                json.dumps(metadata), datetime.utcnow()
            ))
        return True
    except Exception as e:
        log.error(f"Failed to log price change: {e}")
        return False


def enqueue_suppression(
    sku: str,
    asin: Optional[str],
    channel: str,
    reason: str,
    target_price: float,
    floor_price: float,
    vrp_price: Optional[float],
    margin_pct: float,
    elasticity_score: Optional[float],
    severity: str
) -> bool:
    """Enqueue a suppression (alias for add_suppression)."""
    return add_suppression(
        sku, asin, channel, reason,
        target_price, floor_price, vrp_price,
        margin_pct, elasticity_score, severity
    )


def payload_to_event(payload: Any) -> TelemetryEvent:
    """Convert a TelemetryPayload to TelemetryEvent for persistence."""
    import uuid
    
    # Parse timestamp
    ts = datetime.utcnow()
    if payload.timestamp:
        try:
            ts = datetime.fromisoformat(payload.timestamp.replace('Z', '+00:00'))
        except Exception:
            pass
    
    return TelemetryEvent(
        event_id=payload.event_id or str(uuid.uuid4()),
        timestamp=ts,
        product_id=payload.product_id,
        sku=payload.sku or "",
        asin=payload.asin,
        category=payload.category,
        channel=payload.channel,
        event_type=payload.event_type or "price_update",
        scenario=payload.scenario,
        our_price=payload.our_price,
        floor_price=payload.floor_price,
        vrp_price=payload.vrp_price,
        buybox_price=payload.buybox_price,
        lowest_competitor_price=payload.lowest_competitor_price,
        competitor_count=payload.competitor_count,
        cogs=payload.cogs,
        shipping=payload.shipping,
        fees=payload.fees,
        min_margin=payload.min_margin,
        calculated_margin_pct=payload.calculated_margin_pct,
        vrp_drift_pct=payload.vrp_drift_pct,
        vrp_threshold_pct=payload.vrp_threshold_pct,
        drift_detected=payload.drift_detected,
        elasticity_score=payload.elasticity_score,
        elasticity_threshold=payload.elasticity_threshold,
        elasticity_breach=payload.elasticity_breach,
        guardrail_status=payload.guardrail_status,
        suppression_triggered=payload.suppression_triggered,
        suppression_reason=payload.suppression_reason,
        metadata=payload.metadata,
        created_at=datetime.utcnow()
    )


def close_pool() -> None:
    """Close the connection pool (call on shutdown)."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


def test_connection() -> bool:
    """Test database connectivity. Returns True if successful."""
    try:
        with get_cursor(commit=False) as cursor:
            cursor.execute("SELECT 1 as test, version() as version")
            result = cursor.fetchone()
            print(f"Database connection successful: {result['version']}")
            return True
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        success = test_connection()
        sys.exit(0 if success else 1)
    elif len(sys.argv) > 1 and sys.argv[1] == "init":
        init_schema()
        print("Schema initialized/verified")
    else:
        print("Usage: python -m src.ingestion.db [test|init]")