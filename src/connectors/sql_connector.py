"""Direct SQL Database Connector for External Companies.

Enables Zero-ETL direct connections to client PostgreSQL, MySQL, SQLite, or cloud SQL instances.
Executes read-only queries with schema normalization without migrating data into engine tables.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple
from decimal import Decimal

from src.connectors.base import BaseDataConnector, NormalizedTelemetry, SchemaMapping

log = logging.getLogger("connectors.sql")


class DirectSQLConnector(BaseDataConnector):
    """Connects directly to an external company database instance."""

    def __init__(
        self,
        tenant_id: str,
        name: str,
        connection_url: str,
        telemetry_table: str = "pricing_feed",
        catalog_table: Optional[str] = "products",
        schema_mapping: Optional[SchemaMapping] = None,
        callback_webhook: Optional[str] = None,
    ):
        super().__init__(tenant_id=tenant_id, name=name, schema_mapping=schema_mapping)
        self.connection_url = connection_url
        self.telemetry_table = telemetry_table
        self.catalog_table = catalog_table
        self.callback_webhook = callback_webhook

    def _get_connection(self):
        """Create a direct client database connection based on URL scheme."""
        import psycopg2
        from psycopg2.extras import RealDictCursor

        # Supports postgresql / postgres schemes
        if self.connection_url.startswith(("postgres://", "postgresql://")):
            return psycopg2.connect(self.connection_url, cursor_factory=RealDictCursor)
        elif self.connection_url.startswith("sqlite://"):
            import sqlite3
            db_path = self.connection_url.replace("sqlite:///", "").replace("sqlite://", "")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn
        else:
            raise ValueError(f"Unsupported connection scheme: {self.connection_url[:12]}...")

    def test_connection(self) -> Tuple[bool, str]:
        """Verify direct read access to client database and table existence."""
        try:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(f"SELECT 1 FROM {self.telemetry_table} LIMIT 1")
                _ = cur.fetchone()
                return True, f"Successfully connected to external database table '{self.telemetry_table}'"
            finally:
                conn.close()
        except Exception as exc:
            return False, f"Direct SQL connection failed: {str(exc)}"

    def stream_live_telemetry(self, limit: Optional[int] = 50) -> Iterator[NormalizedTelemetry]:
        """Stream live events directly from client database table without persisting locally."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            query = f"SELECT * FROM {self.telemetry_table} ORDER BY 1 DESC"
            if limit:
                query += f" LIMIT {int(limit)}"
            cur.execute(query)
            rows = cur.fetchall()
            for r in rows:
                raw_dict = dict(r)
                yield self.schema_mapping.normalize(raw_dict)
        finally:
            conn.close()

    def fetch_sku_unit_economics(self, sku: str) -> Optional[NormalizedTelemetry]:
        """Query live unit cost breakdown for a specific SKU directly from client tables."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            sku_col = self.schema_mapping.field_map.get("sku", "sku")
            query = f"SELECT * FROM {self.telemetry_table} WHERE {sku_col} = %s LIMIT 1"
            cur.execute(query, (sku,))
            row = cur.fetchone()
            if row:
                return self.schema_mapping.normalize(dict(row))
            return None
        finally:
            conn.close()

    def push_pricing_decision(self, sku: str, decision_payload: Dict[str, Any]) -> bool:
        """Push guardrail verdict back to the client callback webhook."""
        if not self.callback_webhook:
            log.info("No callback webhook configured for tenant %s. Decision logged locally.", self.tenant_id)
            return True

        import urllib.request
        import json

        try:
            payload_bytes = json.dumps({
                "tenant_id": self.tenant_id,
                "sku": sku,
                "decision": decision_payload,
            }).encode("utf-8")
            req = urllib.request.Request(
                self.callback_webhook,
                data=payload_bytes,
                headers={"Content-Type": "application/json", "User-Agent": "PricingGuardrailConnector/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as res:
                return res.status in (200, 201, 202, 204)
        except Exception as exc:
            log.warning("Failed to push decision to client callback: %s", exc)
            return False
