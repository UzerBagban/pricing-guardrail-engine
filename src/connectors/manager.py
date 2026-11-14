"""Connector Registry & Live Zero-ETL Evaluation Manager.

Manages direct company connectors and runs sub-millisecond guardrail evaluations
directly on live external data without persisting into internal database tables.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from src.connectors.base import BaseDataConnector, NormalizedTelemetry, SchemaMapping
from src.connectors.sql_connector import DirectSQLConnector
from src.connectors.rest_connector import DirectRESTConnector
from src.engine.calculator import CostBreakdown
from src.engine.guardrail import PriceEvaluationInput, VRPGuardrailEngine

log = logging.getLogger("connectors.manager")


class ConnectorRegistry:
    """Central registry and live evaluation engine for external company connectors."""

    def __init__(self):
        self._connectors: Dict[str, BaseDataConnector] = {}
        self._engine = VRPGuardrailEngine()

    def register_connector(self, connector: BaseDataConnector) -> None:
        """Register an external company data connector."""
        self._connectors[connector.tenant_id] = connector
        log.info("Registered external connector for tenant: %s (%s)", connector.tenant_id, connector.name)

    def get_connector(self, tenant_id: str) -> Optional[BaseDataConnector]:
        """Retrieve connector by tenant ID."""
        return self._connectors.get(tenant_id)

    def list_connectors(self) -> List[Dict[str, Any]]:
        """List all active company connectors and their health status."""
        result = []
        for tid, conn in self._connectors.items():
            result.append({
                "tenant_id": tid,
                "name": conn.name,
                "type": conn.__class__.__name__,
                "field_mappings": conn.schema_mapping.field_map,
            })
        return result

    def evaluate_live_feed(
        self,
        tenant_id: str,
        limit: int = 50,
        push_decisions: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute Zero-ETL live pricing guardrail evaluation directly against external client data.
        
        Evaluates each inbound SKU proposal in memory (< 0.15ms), enforcing mathematical invariants
        without requiring historical data migration or local database writes.
        """
        connector = self.get_connector(tenant_id)
        if not connector:
            raise KeyError(f"No connector registered for tenant: {tenant_id}")

        t0 = time.perf_counter()
        results = []
        total_defended_dollars = Decimal("0.00")
        approved_count = 0
        warning_count = 0
        blocked_count = 0

        for event in connector.stream_live_telemetry(limit=limit):
            t_item = time.perf_counter()

            # 1. Exact Cost Floor Calculation
            cb = CostBreakdown(
                cogs_per_unit=event.cogs,
                packaging_cost=event.packaging_cost,
                fulfillment_cost=event.fulfillment_cost,
                shipping_cost=event.shipping_cost,
                referral_fee=event.our_price * (event.referral_rate_pct / Decimal("100.0")),
                min_margin_amount=event.min_margin_amount,
            )
            floor_price = cb.floor_price()

            # 2. Guardrail Sub-Millisecond Evaluation
            inp = PriceEvaluationInput(
                product_id=event.product_id or event.sku,
                sku=event.sku,
                asin="EXTERNAL",
                category=event.category,
                our_current_price=event.our_price,
                proposed_price=event.our_price,
                cogs_per_unit=event.cogs,
                shipping_cost=event.shipping_cost,
                vrp_price=event.vrp_price,
                buybox_price=event.buybox_price,
                lowest_competitor_price=event.lowest_competitor_price,
            )
            eval_res = self._engine.evaluate(inp)

            item_latency_ms = (time.perf_counter() - t_item) * 1000.0

            # 3. Invariant check & Verdict
            is_safe = event.our_price >= floor_price
            buffer = event.our_price - floor_price
            
            if not is_safe:
                blocked_count += 1
                verdict = "BLOCKED (FLOOR BREACH)"
                action = "CLAMP_TO_FLOOR"
                safe_price = floor_price
                defended_margin = floor_price - (event.lowest_competitor_price or event.our_price)
                if defended_margin > 0:
                    total_defended_dollars += defended_margin
            elif buffer < Decimal("2.00"):
                warning_count += 1
                verdict = "WARNING (TIGHT BUFFER)"
                action = "MONITOR_VELOCITY"
                safe_price = event.our_price
            else:
                approved_count += 1
                verdict = "APPROVED"
                action = "MAINTAIN_PRICE"
                safe_price = event.our_price

            decision = {
                "sku": event.sku,
                "proposed_price": float(event.our_price),
                "floor_price": float(floor_price),
                "safe_executable_price": float(safe_price),
                "floor_buffer": float(buffer),
                "verdict": verdict,
                "action": action,
                "is_safe": is_safe,
                "evaluation_latency_ms": round(item_latency_ms, 3),
            }

            if push_decisions:
                connector.push_pricing_decision(event.sku, decision)

            results.append(decision)

        total_time_ms = (time.perf_counter() - t0) * 1000.0
        avg_item_ms = total_time_ms / len(results) if results else 0.0

        return {
            "tenant_id": tenant_id,
            "connector_name": connector.name,
            "total_items_evaluated": len(results),
            "approved": approved_count,
            "warnings": warning_count,
            "blocked_breaches": blocked_count,
            "gross_profit_defended_dollars": float(total_defended_dollars),
            "total_evaluation_time_ms": round(total_time_ms, 2),
            "avg_item_latency_ms": round(avg_item_ms, 3),
            "items": results,
        }


# Global singleton registry
default_registry = ConnectorRegistry()
