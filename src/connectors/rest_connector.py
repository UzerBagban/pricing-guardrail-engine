"""Direct REST API Connector for External Companies.

Enables Zero-ETL direct connections to client ERPs, inventory APIs, and repricer feeds.
Streams live market events over HTTP/REST with bearer token / API key authentication.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.connectors.base import BaseDataConnector, NormalizedTelemetry, SchemaMapping

log = logging.getLogger("connectors.rest")


class DirectRESTConnector(BaseDataConnector):
    """Directly queries external company pricing/ERP APIs without data migration."""

    def __init__(
        self,
        tenant_id: str,
        name: str,
        feed_url: str,
        auth_header: Optional[str] = None,
        callback_webhook: Optional[str] = None,
        schema_mapping: Optional[SchemaMapping] = None,
    ):
        super().__init__(tenant_id=tenant_id, name=name, schema_mapping=schema_mapping)
        self.feed_url = feed_url
        self.auth_header = auth_header
        self.callback_webhook = callback_webhook

    def _make_request(self, url: str, method: str = "GET", data: Optional[Dict[str, Any]] = None) -> Any:
        headers = {
            "Accept": "application/json",
            "User-Agent": "PricingGuardrailConnector/1.0",
        }
        if self.auth_header:
            headers["Authorization"] = self.auth_header

        req_data = json.dumps(data).encode("utf-8") if data else None
        if req_data:
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_connection(self) -> Tuple[bool, str]:
        """Test authentication and read access against the client feed endpoint."""
        try:
            res = self._make_request(f"{self.feed_url}?limit=1")
            return True, f"Successfully authenticated and queried client REST feed: {self.feed_url}"
        except Exception as exc:
            return False, f"REST feed connection failed: {str(exc)}"

    def stream_live_telemetry(self, limit: Optional[int] = 50) -> Iterator[NormalizedTelemetry]:
        """Fetch live market batch directly from external API in memory."""
        try:
            url = self.feed_url
            if limit:
                delim = "&" if "?" in url else "?"
                url += f"{delim}limit={int(limit)}"
            payload = self._make_request(url)

            items = payload if isinstance(payload, list) else payload.get("items") or payload.get("data") or []
            for raw_row in items:
                if isinstance(raw_row, dict):
                    yield self.schema_mapping.normalize(raw_row)
        except Exception as exc:
            log.error("Failed streaming from client REST connector %s: %s", self.tenant_id, exc)

    def fetch_sku_unit_economics(self, sku: str) -> Optional[NormalizedTelemetry]:
        """Query external client product API for single SKU unit economics."""
        try:
            delim = "&" if "?" in self.feed_url else "?"
            url = f"{self.feed_url}{delim}sku={urllib.request.quote(sku)}"
            payload = self._make_request(url)
            item = payload if isinstance(payload, dict) else (payload[0] if payload else None)
            if item and isinstance(item, dict):
                return self.schema_mapping.normalize(item)
            return None
        except Exception as exc:
            log.warning("Failed fetching SKU %s from REST connector: %s", sku, exc)
            return None

    def push_pricing_decision(self, sku: str, decision_payload: Dict[str, Any]) -> bool:
        """Post guardrail verdict back to the client callback endpoint."""
        target_url = self.callback_webhook or self.feed_url.replace("/feed", "/callback")
        try:
            self._make_request(
                target_url,
                method="POST",
                data={"tenant_id": self.tenant_id, "sku": sku, "decision": decision_payload},
            )
            return True
        except Exception as exc:
            log.warning("Failed pushing decision to client endpoint: %s", exc)
            return False
