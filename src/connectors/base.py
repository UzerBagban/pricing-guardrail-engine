"""Base Federated Data Connector & Schema Mapper.

Provides Zero-ETL direct-connect capabilities for enterprise clients:
- Connects directly to external company databases, ERPs, APIs, and data lakes
- Dynamic schema mapping for heterogeneous column names
- In-memory normalized streaming without requiring internal DB migration
- Sub-millisecond evaluation with direct decision callbacks
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple


def _to_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _to_decimal(val: Any, default: str = "0.00") -> Decimal:
    if val is None:
        return Decimal(default)
    try:
        return Decimal(str(val))
    except Exception:
        return Decimal(default)


@dataclass
class NormalizedTelemetry:
    """Canonical telemetry structure expected by the pricing guardrail engine."""
    sku: str
    product_id: str = ""
    category: str = "General"
    channel: str = "amazon"
    our_price: Decimal = field(default_factory=lambda: Decimal("0.00"))
    cogs: Decimal = field(default_factory=lambda: Decimal("0.00"))
    packaging_cost: Decimal = field(default_factory=lambda: Decimal("0.00"))
    fulfillment_cost: Decimal = field(default_factory=lambda: Decimal("0.00"))
    shipping_cost: Decimal = field(default_factory=lambda: Decimal("0.00"))
    referral_rate_pct: Decimal = field(default_factory=lambda: Decimal("15.0"))
    min_margin_amount: Decimal = field(default_factory=lambda: Decimal("2.00"))
    vrp_price: Optional[Decimal] = None
    buybox_price: Optional[Decimal] = None
    lowest_competitor_price: Optional[Decimal] = None
    competitor_count: int = 1
    vrp_drift_pct: float = 0.0
    elasticity_score: float = -1.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SchemaMapping:
    """Defines how arbitrary client column/field names map to canonical engine fields."""
    field_map: Dict[str, str] = field(default_factory=lambda: {
        "sku": "sku",
        "our_price": "our_price",
        "cogs": "cogs",
        "shipping_cost": "shipping_cost",
        "referral_rate_pct": "referral_rate_pct",
        "min_margin_amount": "min_margin_amount",
        "vrp_price": "vrp_price",
        "lowest_competitor_price": "lowest_competitor_price",
        "competitor_count": "competitor_count",
    })
    # Optional field-level transformations (e.g. cents to dollars: lambda x: x / 100.0)
    transformations: Dict[str, Callable[[Any], Any]] = field(default_factory=dict)

    def normalize(self, raw_row: Dict[str, Any]) -> NormalizedTelemetry:
        """Map heterogeneous external row to canonical NormalizedTelemetry."""
        mapped = {}
        for canonical_name, external_key in self.field_map.items():
            val = raw_row.get(external_key)
            if canonical_name in self.transformations and val is not None:
                val = self.transformations[canonical_name](val)
            mapped[canonical_name] = val

        sku = str(mapped.get("sku") or raw_row.get("id") or "UNKNOWN-SKU")
        our_price = _to_decimal(mapped.get("our_price"))
        cogs = _to_decimal(mapped.get("cogs"))
        shipping = _to_decimal(mapped.get("shipping_cost"))
        referral_rate = _to_decimal(mapped.get("referral_rate_pct"), "15.0")
        min_margin = _to_decimal(mapped.get("min_margin_amount"), "2.00")
        
        vrp_val = mapped.get("vrp_price")
        vrp_dec = _to_decimal(vrp_val) if vrp_val is not None else None

        comp_val = mapped.get("lowest_competitor_price")
        comp_dec = _to_decimal(comp_val) if comp_val is not None else None

        return NormalizedTelemetry(
            sku=sku,
            product_id=str(raw_row.get("product_id") or sku),
            category=str(raw_row.get("category") or "General"),
            channel=str(raw_row.get("channel") or "amazon"),
            our_price=our_price,
            cogs=cogs,
            packaging_cost=_to_decimal(mapped.get("packaging_cost")),
            fulfillment_cost=_to_decimal(mapped.get("fulfillment_cost")),
            shipping_cost=shipping,
            referral_rate_pct=referral_rate,
            min_margin_amount=min_margin,
            vrp_price=vrp_dec,
            buybox_price=_to_decimal(mapped.get("buybox_price")) if mapped.get("buybox_price") is not None else None,
            lowest_competitor_price=comp_dec,
            competitor_count=int(mapped.get("competitor_count") or 1),
            vrp_drift_pct=_to_float(raw_row.get("vrp_drift_pct"), 0.0),
            elasticity_score=_to_float(raw_row.get("elasticity_score"), -1.0),
            timestamp=str(raw_row.get("timestamp") or datetime.now(timezone.utc).isoformat()),
            raw_payload=raw_row,
        )


class BaseDataConnector(ABC):
    """Abstract interface for Zero-ETL direct-connect data sources."""

    def __init__(self, tenant_id: str, name: str, schema_mapping: Optional[SchemaMapping] = None):
        self.tenant_id = tenant_id
        self.name = name
        self.schema_mapping = schema_mapping or SchemaMapping()

    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """Test authentication and read access to the client data source."""
        pass

    @abstractmethod
    def stream_live_telemetry(self, limit: Optional[int] = None) -> Iterator[NormalizedTelemetry]:
        """Stream live market pricing events directly from client source in memory."""
        pass

    @abstractmethod
    def fetch_sku_unit_economics(self, sku: str) -> Optional[NormalizedTelemetry]:
        """Query live unit cost breakdown for a specific SKU directly from client data."""
        pass

    @abstractmethod
    def push_pricing_decision(self, sku: str, decision_payload: Dict[str, Any]) -> bool:
        """Push guardrail verdict and approved price directly back to client callback/repricer."""
        pass
