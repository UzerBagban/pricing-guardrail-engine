"""Telemetry Input Sanitizer & Schema Validator.

Phase 5 Option 3: Security & Compliance
Guards the ingestion boundary against:
  - Corrupt or negative pricing inputs
  - SQL injection and script injection attacks
  - Unauthorized marketplace channels
  - Extreme predatory price poisoning anomalies
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

VALID_CHANNELS: Set[str] = {"amazon", "ebay", "walmart", "shopify", "internal"}

# Dangerous injection keywords
SQLI_PATTERN = re.compile(
    r"(\b(UNION|SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|EXEC|EXECUTE)\b|--|\bOR\b\s+['\"0-9a-zA-Z]+\s*=\s*['\"0-9a-zA-Z]+|;)",
    re.IGNORECASE,
)
XSS_PATTERN = re.compile(r"(<script|javascript:|onload=|onerror=|<iframe|<img)", re.IGNORECASE)

MAX_PRICE_BOUND = 100000.0
MIN_PRICE_BOUND = 0.01


@dataclass
class ValidationResult:
    is_valid: bool
    sanitized_data: Dict[str, Any]
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    threat_indicators: List[str] = field(default_factory=list)


def _sanitize_string(val: Any, max_len: int = 100) -> str:
    """Strip, escape, and truncate string input."""
    if val is None:
        return ""
    text = str(val).strip()
    clean = html.escape(text)
    return clean[:max_len]


def _check_injection(val: Any, field_name: str, threat_list: List[str]) -> None:
    """Check for SQL injection or script injection signatures."""
    if not isinstance(val, str):
        return
    if SQLI_PATTERN.search(val):
        threat_list.append(f"SQL injection signature detected in '{field_name}'")
    if XSS_PATTERN.search(val):
        threat_list.append(f"XSS injection signature detected in '{field_name}'")


def _to_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def validate_telemetry_payload(payload: Dict[str, Any]) -> ValidationResult:
    """Validate and sanitize an inbound market telemetry payload."""
    errors: List[str] = []
    warnings: List[str] = []
    threats: List[str] = []
    sanitized: Dict[str, Any] = {}

    # 1. Product Identifiers Validation
    sku_raw = payload.get("sku") or payload.get("product_id")
    if not sku_raw:
        errors.append("Missing required product identifier ('sku' or 'product_id')")
    else:
        _check_injection(str(sku_raw), "sku", threats)
        sanitized["sku"] = _sanitize_string(sku_raw, max_len=80)
        sanitized["product_id"] = sanitized["sku"]

    asin_raw = payload.get("asin")
    if asin_raw:
        _check_injection(str(asin_raw), "asin", threats)
        sanitized["asin"] = _sanitize_string(asin_raw, max_len=20)
    else:
        sanitized["asin"] = None

    category_raw = payload.get("category")
    if category_raw:
        _check_injection(str(category_raw), "category", threats)
        sanitized["category"] = _sanitize_string(category_raw, max_len=80)
    else:
        sanitized["category"] = "General"

    # 2. Marketplace Channel Whitelist
    channel_raw = str(payload.get("channel") or "amazon").lower().strip()
    if channel_raw not in VALID_CHANNELS:
        errors.append(f"Invalid marketplace channel '{channel_raw}'. Must be one of {sorted(VALID_CHANNELS)}")
        sanitized["channel"] = "internal"
    else:
        sanitized["channel"] = channel_raw

    # 3. Numeric Pricing Boundary Validations
    our_price = _to_float(payload.get("our_price"))
    if our_price is None:
        errors.append("Missing or non-numeric 'our_price'")
    elif our_price < MIN_PRICE_BOUND:
        errors.append(f"'our_price' must be at least ${MIN_PRICE_BOUND:.2f} (got {our_price})")
    elif our_price > MAX_PRICE_BOUND:
        errors.append(f"'our_price' exceeds maximum allowable limit of ${MAX_PRICE_BOUND:,.2f} (got {our_price})")
    sanitized["our_price"] = our_price

    floor_price = _to_float(payload.get("floor_price"))
    if floor_price is not None:
        if floor_price < 0:
            errors.append(f"'floor_price' cannot be negative (got {floor_price})")
        elif floor_price > MAX_PRICE_BOUND:
            errors.append(f"'floor_price' exceeds maximum allowable limit (got {floor_price})")
    sanitized["floor_price"] = floor_price

    cogs = _to_float(payload.get("cogs"))
    if cogs is not None and cogs < 0:
        errors.append(f"'cogs' cannot be negative (got {cogs})")
    sanitized["cogs"] = cogs or 0.0

    shipping = _to_float(payload.get("shipping"))
    if shipping is not None and shipping < 0:
        errors.append(f"'shipping' cannot be negative (got {shipping})")
    sanitized["shipping"] = shipping or 0.0

    fees = _to_float(payload.get("fees"))
    if fees is not None and fees < 0:
        errors.append(f"'fees' cannot be negative (got {fees})")
    sanitized["fees"] = fees or 0.0

    # 4. Competitor & VRP Pricing Validations
    lowest_comp = _to_float(payload.get("lowest_competitor_price"))
    if lowest_comp is not None and lowest_comp < 0:
        warnings.append(f"Negative competitor price ignored (got {lowest_comp})")
        lowest_comp = None
    sanitized["lowest_competitor_price"] = lowest_comp

    vrp_price = _to_float(payload.get("vrp_price"))
    if vrp_price is not None and vrp_price < 0:
        warnings.append(f"Negative VRP price ignored (got {vrp_price})")
        vrp_price = None
    sanitized["vrp_price"] = vrp_price

    # 5. Anomaly & Poisoning Detection
    if lowest_comp and floor_price and floor_price > 0:
        if lowest_comp < floor_price * 0.25:
            warnings.append(
                f"Severe predatory pricing anomaly: competitor price ${lowest_comp:.2f} is >75% below cost floor ${floor_price:.2f}"
            )

    vrp_drift = _to_float(payload.get("vrp_drift_pct")) or 0.0
    if vrp_drift < -60.0:
        warnings.append(f"Extreme VRP collapse anomaly: reference price drop of {vrp_drift:.1f}%")
    sanitized["vrp_drift_pct"] = vrp_drift

    # Forward other metadata safely
    sanitized["scenario"] = _sanitize_string(payload.get("scenario") or "normal", max_len=50)
    sanitized["event_type"] = _sanitize_string(payload.get("event_type") or "price_update", max_len=50)
    sanitized["competitor_count"] = max(0, int(payload.get("competitor_count") or 0))
    sanitized["calculated_margin_pct"] = _to_float(payload.get("calculated_margin_pct")) or 0.0
    sanitized["elasticity_score"] = _to_float(payload.get("elasticity_score")) or 0.0

    if threats:
        errors.append(f"Payload rejected due to security threat indicators: {', '.join(threats)}")

    is_valid = len(errors) == 0

    return ValidationResult(
        is_valid=is_valid,
        sanitized_data=sanitized if is_valid else {},
        errors=errors,
        warnings=warnings,
        threat_indicators=threats,
    )
