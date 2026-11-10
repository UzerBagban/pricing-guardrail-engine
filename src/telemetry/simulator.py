"""
Synthetic Telemetry Simulator
Generates realistic market telemetry for testing the pricing guardrail engine
without requiring live API connections.

Supports scenarios:
- Normal price fluctuations
- Sudden VRP drift drops (>5%)
- Competitor price undercutting
- Margin compression events
"""

import json
import random
import argparse
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class ScenarioType(str, Enum):
    NORMAL = "normal"
    VRP_DRIFT_DROP = "vrp_drift_drop"
    COMPETITOR_UNDERCUT = "competitor_undercut"
    MARGIN_COMPRESSION = "margin_compression"
    BUYBOX_LOSS = "buybox_loss"
    PROMOTIONAL_SPIKE = "promotional_spike"
    STOCKOUT_COMPETITOR = "stockout_competitor"
    ALGORITHMIC_REPRICING = "algorithmic_repricing"
    # Phase 2: Targeted shock scenarios
    AGGRESSIVE_COMPETITOR_UNDERCUT = "aggressive_competitor_undercut"  # Price below absolute floor
    VRP_DRIFT_SHOCK = "vrp_drift_shock"  # Reference price drops >10%
    COST_INFLATION_BREACH = "cost_inflation_breach"  # Shipping/packaging cost spike


@dataclass
class ProductProfile:
    """Product characteristics for realistic simulation."""
    product_id: str
    sku: str
    asin: str
    category: str
    base_price: float
    floor_price: float
    vrp_price: float
    cogs: float
    shipping: float
    fees: float
    min_margin: float
    competitor_count: int
    elasticity: float  # price elasticity coefficient
    velocity: float  # sales per day


@dataclass
class TelemetryRecord:
    """Single telemetry event record."""
    event_id: str
    timestamp: str
    product_id: str
    sku: str
    asin: str
    category: str
    channel: str
    event_type: str
    scenario: str

    # Price data
    our_price: float
    floor_price: float
    vrp_price: float
    buybox_price: float
    lowest_competitor_price: float
    competitor_count: int

    # Cost structure
    cogs: float
    shipping: float
    fees: float
    min_margin: float
    calculated_margin_pct: float

    # VRP & Drift
    vrp_drift_pct: float
    vrp_threshold_pct: float
    drift_detected: bool

    # Elasticity
    elasticity_score: float
    elasticity_threshold: float
    elasticity_breach: bool

    # Guardrail status
    guardrail_status: str  # 'healthy', 'warning', 'breach', 'suppressed'
    suppression_triggered: bool
    suppression_reason: Optional[str]

    # Metadata
    metadata: Dict[str, Any]


# Realistic product profiles matching seed data
PRODUCT_PROFILES = [
    ProductProfile(
        product_id="prod-elec-001", sku="ELEC-001", asin="B08N5WRWNW",
        category="Electronics", base_price=79.99, floor_price=45.20,
        vrp_price=79.99, cogs=28.50, shipping=3.50, fees=8.00, min_margin=12.00,
        competitor_count=12, elasticity=-1.8, velocity=15.0
    ),
    ProductProfile(
        product_id="prod-elec-002", sku="ELEC-002", asin="B09KJRN3HH",
        category="Electronics", base_price=24.99, floor_price=18.35,
        vrp_price=24.99, cogs=12.75, shipping=2.10, fees=3.00, min_margin=5.00,
        competitor_count=8, elasticity=-2.2, velocity=45.0
    ),
    ProductProfile(
        product_id="prod-home-001", sku="HOME-001", asin="B07VV4KQZM",
        category="Home & Kitchen", base_price=34.99, floor_price=20.50,
        vrp_price=34.99, cogs=8.90, shipping=2.80, fees=4.00, min_margin=4.80,
        competitor_count=15, elasticity=-1.5, velocity=25.0
    ),
    ProductProfile(
        product_id="prod-home-002", sku="HOME-002", asin="B08XJ7K9L2",
        category="Home & Kitchen", base_price=18.99, floor_price=10.50,
        vrp_price=18.99, cogs=5.40, shipping=1.90, fees=2.50, min_margin=2.70,
        competitor_count=20, elasticity=-2.5, velocity=60.0
    ),
    ProductProfile(
        product_id="prod-sprt-001", sku="SPRT-001", asin="B07Z4JQJQJ",
        category="Sports & Outdoors", base_price=22.99, floor_price=12.80,
        vrp_price=22.99, cogs=6.20, shipping=1.50, fees=2.80, min_margin=3.30,
        competitor_count=10, elasticity=-1.9, velocity=30.0
    ),
    ProductProfile(
        product_id="prod-sprt-002", sku="SPRT-002", asin="B08Y7K3L4M",
        category="Sports & Outdoors", base_price=29.99, floor_price=17.00,
        vrp_price=29.99, cogs=9.80, shipping=2.20, fees=3.50, min_margin=4.50,
        competitor_count=7, elasticity=-1.6, velocity=18.0
    ),
    ProductProfile(
        product_id="prod-beau-001", sku="BEAU-001", asin="B09M2N4P6Q",
        category="Beauty", base_price=28.99, floor_price=13.75,
        vrp_price=28.99, cogs=4.50, shipping=1.20, fees=3.20, min_margin=5.85,
        competitor_count=9, elasticity=-2.8, velocity=35.0
    ),
    ProductProfile(
        product_id="prod-beau-002", sku="BEAU-002", asin="B08K3L5M7N",
        category="Beauty", base_price=39.99, floor_price=19.50,
        vrp_price=39.99, cogs=7.30, shipping=1.40, fees=4.00, min_margin=6.80,
        competitor_count=6, elasticity=-2.0, velocity=20.0
    ),
    ProductProfile(
        product_id="prod-toys-001", sku="TOYS-001", asin="B07P8R9S2T",
        category="Toys & Games", base_price=34.99, floor_price=18.50,
        vrp_price=34.99, cogs=11.40, shipping=2.00, fees=3.80, min_margin=4.30,
        competitor_count=18, elasticity=-3.2, velocity=40.0
    ),
    ProductProfile(
        product_id="prod-toys-002", sku="TOYS-002", asin="B09V2W4X6Y",
        category="Toys & Games", base_price=89.99, floor_price=51.20,
        vrp_price=89.99, cogs=22.60, shipping=3.80, fees=9.50, min_margin=15.30,
        competitor_count=5, elasticity=-1.4, velocity=8.0
    ),
]


def calculate_margin(price: float, cogs: float, shipping: float, fees: float) -> float:
    """Calculate margin percentage."""
    total_cost = cogs + shipping + fees
    if price <= 0:
        return -100.0
    return ((price - total_cost) / price) * 100


def calculate_vrp_drift(vrp: float, our_price: float) -> float:
    """Calculate VRP drift percentage: (vrp - our_price) / our_price * 100"""
    if our_price <= 0:
        return 0.0
    return ((vrp - our_price) / our_price) * 100


def calculate_elasticity_score(base_price: float, current_price: float,
                                base_velocity: float, current_velocity: float,
                                elasticity_coeff: float) -> float:
    """
    Calculate elasticity score based on price and velocity changes.
    ε = (ΔP/P) / (ΔQ/Q)
    """
    if base_price <= 0 or base_velocity <= 0:
        return 0.0

    price_change_pct = (current_price - base_price) / base_price
    velocity_change_pct = (current_velocity - base_velocity) / base_velocity

    if abs(velocity_change_pct) < 0.001:
        return abs(elasticity_coeff) * 10  # Large elasticity if no volume change

    epsilon = price_change_pct / velocity_change_pct
    return abs(epsilon)


def generate_normal_fluctuation(profile: ProductProfile, timestamp: datetime) -> TelemetryRecord:
    """Generate normal market fluctuation."""
    # Small random walk around base price (±2%)
    price_noise = random.uniform(-0.02, 0.02)
    our_price = round(profile.base_price * (1 + price_noise), 2)

    # VRP also fluctuates slightly
    vrp_noise = random.uniform(-0.015, 0.015)
    vrp_price = round(profile.vrp_price * (1 + vrp_noise), 2)

    # Competitors near our price
    competitor_spread = random.uniform(-0.03, 0.05)
    lowest_competitor = round(our_price * (1 + competitor_spread), 2)
    buybox_price = min(our_price, lowest_competitor)

    # Velocity affected by price (elasticity)
    price_change_pct = (our_price - profile.base_price) / profile.base_price
    velocity_change = -profile.elasticity * price_change_pct * 0.5  # dampened
    current_velocity = max(0.1, profile.velocity * (1 + velocity_change))

    vrp_drift = calculate_vrp_drift(vrp_price, our_price)
    margin_pct = calculate_margin(our_price, profile.cogs, profile.shipping, profile.fees)
    elasticity_score = calculate_elasticity_score(
        profile.base_price, our_price, profile.velocity, current_velocity, profile.elasticity
    )

    # Determine guardrail status
    guardrail_status = "healthy"
    suppression_triggered = False
    suppression_reason = None

    if margin_pct < profile.min_margin:
        guardrail_status = "breach"
        suppression_triggered = True
        suppression_reason = "below_floor_price"
    elif vrp_drift > 5.0:
        guardrail_status = "warning"
    elif elasticity_score > 2.5:
        guardrail_status = "warning"

    return TelemetryRecord(
        event_id=str(uuid.uuid4()),
        timestamp=timestamp.isoformat(),
        product_id=profile.product_id,
        sku=profile.sku,
        asin=profile.asin,
        category=profile.category,
        channel="amazon",
        event_type="price_update",
        scenario=ScenarioType.NORMAL.value,
        our_price=our_price,
        floor_price=profile.floor_price,
        vrp_price=vrp_price,
        buybox_price=buybox_price,
        lowest_competitor_price=lowest_competitor,
        competitor_count=profile.competitor_count,
        cogs=profile.cogs,
        shipping=profile.shipping,
        fees=profile.fees,
        min_margin=profile.min_margin,
        calculated_margin_pct=round(margin_pct, 2),
        vrp_drift_pct=round(vrp_drift, 2),
        vrp_threshold_pct=5.0,
        drift_detected=vrp_drift > 5.0,
        elasticity_score=round(elasticity_score, 3),
        elasticity_threshold=2.5,
        elasticity_breach=elasticity_score > 2.5,
        guardrail_status=guardrail_status,
        suppression_triggered=suppression_triggered,
        suppression_reason=suppression_reason,
        metadata={"velocity": round(current_velocity, 1), "price_change_pct": round(price_change_pct * 100, 2)}
    )


def generate_vrp_drift_drop(profile: ProductProfile, timestamp: datetime, severity: float = 0.08) -> TelemetryRecord:
    """Generate VRP drift drop scenario (>5% drop)."""
    # VRP drops significantly
    vrp_drop = random.uniform(severity, severity * 1.5)
    vrp_price = round(profile.vrp_price * (1 - vrp_drop), 2)

    # Our price stays same initially (lag)
    our_price = profile.base_price

    # Competitors react faster
    competitor_reaction = random.uniform(0.5, 0.8) * vrp_drop
    lowest_competitor = round(our_price * (1 - competitor_reaction), 2)
    buybox_price = lowest_competitor

    vrp_drift = calculate_vrp_drift(vrp_price, our_price)
    margin_pct = calculate_margin(our_price, profile.cogs, profile.shipping, profile.fees)
    elasticity_score = profile.elasticity * 1.5  # elevated during drift

    return TelemetryRecord(
        event_id=str(uuid.uuid4()),
        timestamp=timestamp.isoformat(),
        product_id=profile.product_id,
        sku=profile.sku,
        asin=profile.asin,
        category=profile.category,
        channel="amazon",
        event_type="vrp_drift_alert",
        scenario=ScenarioType.VRP_DRIFT_DROP.value,
        our_price=our_price,
        floor_price=profile.floor_price,
        vrp_price=vrp_price,
        buybox_price=buybox_price,
        lowest_competitor_price=lowest_competitor,
        competitor_count=profile.competitor_count,
        cogs=profile.cogs,
        shipping=profile.shipping,
        fees=profile.fees,
        min_margin=profile.min_margin,
        calculated_margin_pct=round(margin_pct, 2),
        vrp_drift_pct=round(vrp_drift, 2),
        vrp_threshold_pct=5.0,
        drift_detected=True,
        elasticity_score=round(elasticity_score, 3),
        elasticity_threshold=2.5,
        elasticity_breach=abs(elasticity_score) > 2.5,
        guardrail_status="warning",
        suppression_triggered=False,
        suppression_reason=None,
        metadata={
            "vrp_drop_pct": round(vrp_drop * 100, 2),
            "competitor_reaction_pct": round(competitor_reaction * 100, 2),
            "recommended_action": "increase_price" if vrp_drift > 5 else "monitor"
        }
    )


def generate_competitor_undercut(profile: ProductProfile, timestamp: datetime, depth: float = 0.12) -> TelemetryRecord:
    """Generate competitor undercutting scenario."""
    # Competitor drops price significantly
    undercut_pct = random.uniform(depth, depth * 1.3)
    lowest_competitor = round(profile.base_price * (1 - undercut_pct), 2)

    # Our price initially unchanged
    our_price = profile.base_price
    vrp_price = profile.vrp_price
    buybox_price = lowest_competitor

    # Velocity drops due to lost buybox
    velocity_drop = undercut_pct * abs(profile.elasticity) * 0.7
    current_velocity = max(0.1, profile.velocity * (1 - velocity_drop))

    vrp_drift = calculate_vrp_drift(vrp_price, our_price)
    margin_pct = calculate_margin(our_price, profile.cogs, profile.shipping, profile.fees)
    elasticity_score = calculate_elasticity_score(
        profile.base_price, our_price, profile.velocity, current_velocity, profile.elasticity
    )

    return TelemetryRecord(
        event_id=str(uuid.uuid4()),
        timestamp=timestamp.isoformat(),
        product_id=profile.product_id,
        sku=profile.sku,
        asin=profile.asin,
        category=profile.category,
        channel="amazon",
        event_type="competitor_undercut",
        scenario=ScenarioType.COMPETITOR_UNDERCUT.value,
        our_price=our_price,
        floor_price=profile.floor_price,
        vrp_price=vrp_price,
        buybox_price=buybox_price,
        lowest_competitor_price=lowest_competitor,
        competitor_count=profile.competitor_count + random.randint(1, 3),
        cogs=profile.cogs,
        shipping=profile.shipping,
        fees=profile.fees,
        min_margin=profile.min_margin,
        calculated_margin_pct=round(margin_pct, 2),
        vrp_drift_pct=round(vrp_drift, 2),
        vrp_threshold_pct=5.0,
        drift_detected=vrp_drift > 5.0,
        elasticity_score=round(elasticity_score, 3),
        elasticity_threshold=2.5,
        elasticity_breach=abs(elasticity_score) > 2.5,
        guardrail_status="warning",
        suppression_triggered=False,
        suppression_reason=None,
        metadata={
            "undercut_pct": round(undercut_pct * 100, 2),
            "buybox_lost": True,
            "velocity_impact_pct": round(velocity_drop * 100, 1),
            "recommended_action": "match_price" if lowest_competitor > profile.floor_price else "hold_floor"
        }
    )


def generate_margin_compression(profile: ProductProfile, timestamp: datetime, fee_increase: float = 0.03) -> TelemetryRecord:
    """Generate margin compression from fee increases."""
    # Fees increase (e.g., referral fee hike, FBA fee increase)
    increased_fees = round(profile.fees * (1 + fee_increase), 2)

    # Price stays same, margin compresses
    our_price = profile.base_price
    vrp_price = profile.vrp_price
    buybox_price = our_price
    lowest_competitor = round(our_price * random.uniform(0.98, 1.02), 2)

    margin_pct = calculate_margin(our_price, profile.cogs, profile.shipping, increased_fees)
    vrp_drift = calculate_vrp_drift(vrp_price, our_price)
    elasticity_score = profile.elasticity

    guardrail_status = "healthy"
    suppression_triggered = False
    suppression_reason = None

    if margin_pct < profile.min_margin:
        guardrail_status = "breach"
        suppression_triggered = True
        suppression_reason = "margin_breach"
    elif margin_pct < profile.min_margin * 1.2:
        guardrail_status = "warning"

    return TelemetryRecord(
        event_id=str(uuid.uuid4()),
        timestamp=timestamp.isoformat(),
        product_id=profile.product_id,
        sku=profile.sku,
        asin=profile.asin,
        category=profile.category,
        channel="amazon",
        event_type="fee_change",
        scenario=ScenarioType.MARGIN_COMPRESSION.value,
        our_price=our_price,
        floor_price=profile.floor_price,
        vrp_price=vrp_price,
        buybox_price=buybox_price,
        lowest_competitor_price=lowest_competitor,
        competitor_count=profile.competitor_count,
        cogs=profile.cogs,
        shipping=profile.shipping,
        fees=increased_fees,
        min_margin=profile.min_margin,
        calculated_margin_pct=round(margin_pct, 2),
        vrp_drift_pct=round(vrp_drift, 2),
        vrp_threshold_pct=5.0,
        drift_detected=vrp_drift > 5.0,
        elasticity_score=round(elasticity_score, 3),
        elasticity_threshold=2.5,
        elasticity_breach=abs(elasticity_score) > 2.5,
        guardrail_status=guardrail_status,
        suppression_triggered=suppression_triggered,
        suppression_reason=suppression_reason,
        metadata={
            "fee_increase_pct": round(fee_increase * 100, 2),
            "original_fees": profile.fees,
            "margin_compression_pct": round((profile.min_margin - margin_pct), 2) if margin_pct < profile.min_margin else 0
        }
    )


def generate_aggressive_competitor_undercut(profile: ProductProfile, timestamp: datetime, depth: float = 0.25) -> TelemetryRecord:
    """Generate aggressive competitor undercut - price BELOW absolute floor (base_cost * (1 + min_margin))."""
    # Absolute floor = total_cost * (1 + min_margin/100)
    total_cost = profile.cogs + profile.shipping + profile.fees
    absolute_floor = total_cost * (1 + profile.min_margin / 100.0)
    
    # Competitor prices significantly BELOW absolute floor
    undercut_pct = random.uniform(depth, depth * 1.5)
    lowest_competitor = round(absolute_floor * (1 - undercut_pct), 2)
    
    # Our price stays at base initially (lag)
    our_price = profile.base_price
    vrp_price = profile.vrp_price
    buybox_price = lowest_competitor
    
    # Severe velocity drop due to massive undercut
    velocity_drop = undercut_pct * abs(profile.elasticity) * 1.2
    current_velocity = max(0.1, profile.velocity * (1 - velocity_drop))
    
    vrp_drift = calculate_vrp_drift(vrp_price, our_price)
    margin_pct = calculate_margin(our_price, profile.cogs, profile.shipping, profile.fees)
    elasticity_score = calculate_elasticity_score(
        profile.base_price, our_price, profile.velocity, current_velocity, profile.elasticity
    )
    
    return TelemetryRecord(
        event_id=str(uuid.uuid4()),
        timestamp=timestamp.isoformat(),
        product_id=profile.product_id,
        sku=profile.sku,
        asin=profile.asin,
        category=profile.category,
        channel="amazon",
        event_type="aggressive_competitor_undercut",
        scenario=ScenarioType.AGGRESSIVE_COMPETITOR_UNDERCUT.value,
        our_price=our_price,
        floor_price=profile.floor_price,
        vrp_price=vrp_price,
        buybox_price=buybox_price,
        lowest_competitor_price=lowest_competitor,
        competitor_count=profile.competitor_count + random.randint(2, 5),
        cogs=profile.cogs,
        shipping=profile.shipping,
        fees=profile.fees,
        min_margin=profile.min_margin,
        calculated_margin_pct=round(margin_pct, 2),
        vrp_drift_pct=round(vrp_drift, 2),
        vrp_threshold_pct=5.0,
        drift_detected=vrp_drift > 5.0,
        elasticity_score=round(elasticity_score, 3),
        elasticity_threshold=2.5,
        elasticity_breach=abs(elasticity_score) > 2.5,
        guardrail_status="breach",
        suppression_triggered=False,
        suppression_reason=None,
        metadata={
            "undercut_pct": round(undercut_pct * 100, 2),
            "absolute_floor": round(absolute_floor, 2),
            "competitor_below_floor": lowest_competitor < absolute_floor,
            "buybox_lost": True,
            "velocity_impact_pct": round(velocity_drop * 100, 1),
            "recommended_action": "hold_floor_cannot_match"
        }
    )


def generate_vrp_drift_shock(profile: ProductProfile, timestamp: datetime, severity: float = 0.12) -> TelemetryRecord:
    """Generate Amazon VRP drift shock - reference price drops sharply (>10%), threatening deal eligibility."""
    # VRP drops sharply (>10%)
    vrp_drop = random.uniform(severity, severity * 1.8)
    vrp_price = round(profile.vrp_price * (1 - vrp_drop), 2)
    
    # Our price stays same initially (lag)
    our_price = profile.base_price
    
    # Competitors react aggressively
    competitor_reaction = random.uniform(0.7, 1.0) * vrp_drop
    lowest_competitor = round(our_price * (1 - competitor_reaction), 2)
    buybox_price = lowest_competitor
    
    vrp_drift = calculate_vrp_drift(vrp_price, our_price)
    margin_pct = calculate_margin(our_price, profile.cogs, profile.shipping, profile.fees)
    elasticity_score = profile.elasticity * 2.0  # highly elevated during shock
    
    return TelemetryRecord(
        event_id=str(uuid.uuid4()),
        timestamp=timestamp.isoformat(),
        product_id=profile.product_id,
        sku=profile.sku,
        asin=profile.asin,
        category=profile.category,
        channel="amazon",
        event_type="vrp_drift_shock",
        scenario=ScenarioType.VRP_DRIFT_SHOCK.value,
        our_price=our_price,
        floor_price=profile.floor_price,
        vrp_price=vrp_price,
        buybox_price=buybox_price,
        lowest_competitor_price=lowest_competitor,
        competitor_count=profile.competitor_count + random.randint(1, 3),
        cogs=profile.cogs,
        shipping=profile.shipping,
        fees=profile.fees,
        min_margin=profile.min_margin,
        calculated_margin_pct=round(margin_pct, 2),
        vrp_drift_pct=round(vrp_drift, 2),
        vrp_threshold_pct=5.0,
        drift_detected=True,
        elasticity_score=round(elasticity_score, 3),
        elasticity_threshold=2.5,
        elasticity_breach=abs(elasticity_score) > 2.5,
        guardrail_status="warning",
        suppression_triggered=False,
        suppression_reason=None,
        metadata={
            "vrp_drop_pct": round(vrp_drop * 100, 2),
            "competitor_reaction_pct": round(competitor_reaction * 100, 2),
            "deal_eligibility_threatened": vrp_drift > 10.0,
            "recommended_action": "immediate_price_review"
        }
    )


def generate_cost_inflation_breach(profile: ProductProfile, timestamp: datetime, shipping_spike: float = 0.40, packaging_spike: float = 0.30) -> TelemetryRecord:
    """Generate cost inflation breach - base shipping or packaging cost spike causing sudden margin breach."""
    # Cost spike: shipping and/or packaging
    cost_spike_type = random.choice(["shipping", "packaging", "both"])
    
    increased_shipping = profile.shipping
    increased_cogs = profile.cogs  # packaging folded into cogs
    
    if cost_spike_type in ("shipping", "both"):
        increased_shipping = round(profile.shipping * (1 + shipping_spike), 2)
    if cost_spike_type in ("packaging", "both"):
        increased_cogs = round(profile.cogs * (1 + packaging_spike), 2)
    
    # Price stays same, margin compresses severely
    our_price = profile.base_price
    vrp_price = profile.vrp_price
    buybox_price = our_price
    lowest_competitor = round(our_price * random.uniform(0.98, 1.02), 2)
    
    margin_pct = calculate_margin(our_price, increased_cogs, increased_shipping, profile.fees)
    vrp_drift = calculate_vrp_drift(vrp_price, our_price)
    elasticity_score = profile.elasticity
    
    guardrail_status = "healthy"
    suppression_triggered = False
    suppression_reason = None
    
    if margin_pct < profile.min_margin:
        guardrail_status = "breach"
        suppression_triggered = True
        suppression_reason = "cost_inflation_margin_breach"
    elif margin_pct < profile.min_margin * 1.15:
        guardrail_status = "warning"
    
    return TelemetryRecord(
        event_id=str(uuid.uuid4()),
        timestamp=timestamp.isoformat(),
        product_id=profile.product_id,
        sku=profile.sku,
        asin=profile.asin,
        category=profile.category,
        channel="amazon",
        event_type="cost_inflation_breach",
        scenario=ScenarioType.COST_INFLATION_BREACH.value,
        our_price=our_price,
        floor_price=profile.floor_price,
        vrp_price=vrp_price,
        buybox_price=buybox_price,
        lowest_competitor_price=lowest_competitor,
        competitor_count=profile.competitor_count,
        cogs=increased_cogs,
        shipping=increased_shipping,
        fees=profile.fees,
        min_margin=profile.min_margin,
        calculated_margin_pct=round(margin_pct, 2),
        vrp_drift_pct=round(vrp_drift, 2),
        vrp_threshold_pct=5.0,
        drift_detected=vrp_drift > 5.0,
        elasticity_score=round(elasticity_score, 3),
        elasticity_threshold=2.5,
        elasticity_breach=abs(elasticity_score) > 2.5,
        guardrail_status=guardrail_status,
        suppression_triggered=suppression_triggered,
        suppression_reason=suppression_reason,
        metadata={
            "cost_spike_type": cost_spike_type,
            "shipping_spike_pct": round(shipping_spike * 100, 2) if cost_spike_type in ("shipping", "both") else 0,
            "packaging_spike_pct": round(packaging_spike * 100, 2) if cost_spike_type in ("packaging", "both") else 0,
            "original_shipping": profile.shipping,
            "original_cogs": profile.cogs,
            "margin_compression_pct": round((profile.min_margin - margin_pct), 2) if margin_pct < profile.min_margin else 0,
            "recommended_action": "negotiate_costs_or_raise_price"
        }
    )


def generate_buybox_loss(profile: ProductProfile, timestamp: datetime) -> TelemetryRecord:
    """Generate buybox loss scenario."""
    # Competitor wins buybox at lower price
    competitor_price = round(profile.base_price * random.uniform(0.85, 0.95), 2)
    our_price = profile.base_price
    vrp_price = profile.vrp_price
    buybox_price = competitor_price
    lowest_competitor = competitor_price

    # Significant velocity drop
    velocity_drop = random.uniform(0.4, 0.7)
    current_velocity = max(0.1, profile.velocity * (1 - velocity_drop))

    vrp_drift = calculate_vrp_drift(vrp_price, our_price)
    margin_pct = calculate_margin(our_price, profile.cogs, profile.shipping, profile.fees)
    elasticity_score = calculate_elasticity_score(
        profile.base_price, our_price, profile.velocity, current_velocity, profile.elasticity
    )

    return TelemetryRecord(
        event_id=str(uuid.uuid4()),
        timestamp=timestamp.isoformat(),
        product_id=profile.product_id,
        sku=profile.sku,
        asin=profile.asin,
        category=profile.category,
        channel="amazon",
        event_type="buybox_lost",
        scenario=ScenarioType.BUYBOX_LOSS.value,
        our_price=our_price,
        floor_price=profile.floor_price,
        vrp_price=vrp_price,
        buybox_price=buybox_price,
        lowest_competitor_price=lowest_competitor,
        competitor_count=profile.competitor_count,
        cogs=profile.cogs,
        shipping=profile.shipping,
        fees=profile.fees,
        min_margin=profile.min_margin,
        calculated_margin_pct=round(margin_pct, 2),
        vrp_drift_pct=round(vrp_drift, 2),
        vrp_threshold_pct=5.0,
        drift_detected=vrp_drift > 5.0,
        elasticity_score=round(elasticity_score, 3),
        elasticity_threshold=2.5,
        elasticity_breach=abs(elasticity_score) > 2.5,
        guardrail_status="warning",
        suppression_triggered=False,
        suppression_reason=None,
        metadata={
            "buybox_lost": True,
            "competitor_buybox_price": competitor_price,
            "velocity_drop_pct": round(velocity_drop * 100, 1),
            "recommended_action": "investigate"
        }
    )


SCENARIO_GENERATORS = {
    ScenarioType.NORMAL: generate_normal_fluctuation,
    ScenarioType.VRP_DRIFT_DROP: generate_vrp_drift_drop,
    ScenarioType.COMPETITOR_UNDERCUT: generate_competitor_undercut,
    ScenarioType.MARGIN_COMPRESSION: generate_margin_compression,
    ScenarioType.BUYBOX_LOSS: generate_buybox_loss,
    # Phase 2: Targeted shock scenarios
    ScenarioType.AGGRESSIVE_COMPETITOR_UNDERCUT: generate_aggressive_competitor_undercut,
    ScenarioType.VRP_DRIFT_SHOCK: generate_vrp_drift_shock,
    ScenarioType.COST_INFLATION_BREACH: generate_cost_inflation_breach,
}


def generate_telemetry(
    num_records: int = 500,
    scenario_weights: Optional[Dict[ScenarioType, float]] = None,
    start_time: Optional[datetime] = None,
    interval_minutes: int = 15,
    seed: Optional[int] = None
) -> List[TelemetryRecord]:
    """
    Generate synthetic telemetry records.

    Args:
        num_records: Number of records to generate
        scenario_weights: Weight distribution for scenarios (defaults to realistic mix)
        start_time: Starting timestamp (defaults to now - num_records * interval)
        interval_minutes: Time interval between records
        seed: Random seed for reproducibility

    Returns:
        List of TelemetryRecord objects
    """
    if seed is not None:
        random.seed(seed)

    if scenario_weights is None:
        scenario_weights = {
            ScenarioType.NORMAL: 0.40,
            ScenarioType.VRP_DRIFT_DROP: 0.08,
            ScenarioType.COMPETITOR_UNDERCUT: 0.10,
            ScenarioType.MARGIN_COMPRESSION: 0.06,
            ScenarioType.BUYBOX_LOSS: 0.04,
            ScenarioType.PROMOTIONAL_SPIKE: 0.04,
            ScenarioType.STOCKOUT_COMPETITOR: 0.03,
            ScenarioType.ALGORITHMIC_REPRICING: 0.02,
            # Phase 2: Targeted shock scenarios
            ScenarioType.AGGRESSIVE_COMPETITOR_UNDERCUT: 0.08,
            ScenarioType.VRP_DRIFT_SHOCK: 0.08,
            ScenarioType.COST_INFLATION_BREACH: 0.07,
        }

    # Normalize weights
    total_weight = sum(scenario_weights.values())
    scenario_weights = {k: v / total_weight for k, v in scenario_weights.items()}

    if start_time is None:
        start_time = datetime.now() - timedelta(minutes=num_records * interval_minutes)

    # Create scenario list based on weights
    scenarios = []
    for scenario, weight in scenario_weights.items():
        count = max(1, int(num_records * weight))
        scenarios.extend([scenario] * count)
    random.shuffle(scenarios)

    records = []
    current_time = start_time

    for i in range(num_records):
        profile = random.choice(PRODUCT_PROFILES)
        scenario = scenarios[i % len(scenarios)]

        generator = SCENARIO_GENERATORS.get(scenario, generate_normal_fluctuation)
        record = generator(profile, current_time)
        records.append(record)

        current_time += timedelta(minutes=interval_minutes)

    return records


def write_telemetry_json(records: List[TelemetryRecord], output_path: Path) -> None:
    """Write telemetry records to JSON file."""
    data = [asdict(r) for r in records]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Written {len(records)} records to {output_path}")


def run_streaming_mode(args) -> int:
    """Run simulator in streaming mode, outputting batches continuously."""
    import time
    import sys
    
    batch_size = args.batch_size
    interval_ms = args.interval_ms
    total_records = args.records
    output_path = Path(args.output)
    
    # Generate scenario list
    scenario_weights = None
    if args.scenario:
        try:
            scenario = ScenarioType(args.scenario)
            scenario_weights = {scenario: 1.0}
        except ValueError:
            print(f"Unknown scenario: {args.scenario}")
            print(f"Available: {[s.value for s in ScenarioType]}")
            return 1

    start_time = None
    if args.start:
        try:
            start_time = datetime.fromisoformat(args.start)
        except ValueError:
            print(f"Invalid start time format. Use ISO format (YYYY-MM-DDTHH:MM:SS)")
            return 1

    # Prepare scenario list for consistent distribution
    scenario_weights = scenario_weights or {
        ScenarioType.NORMAL: 0.35,
        ScenarioType.VRP_DRIFT_DROP: 0.08,
        ScenarioType.COMPETITOR_UNDERCUT: 0.10,
        ScenarioType.MARGIN_COMPRESSION: 0.08,
        ScenarioType.BUYBOX_LOSS: 0.04,
        ScenarioType.PROMOTIONAL_SPIKE: 0.04,
        ScenarioType.STOCKOUT_COMPETITOR: 0.03,
        ScenarioType.ALGORITHMIC_REPRICING: 0.02,
        # Phase 2: Targeted shock scenarios
        ScenarioType.AGGRESSIVE_COMPETITOR_UNDERCUT: 0.08,
        ScenarioType.VRP_DRIFT_SHOCK: 0.08,
        ScenarioType.COST_INFLATION_BREACH: 0.10,
    }
    total_weight = sum(scenario_weights.values())
    scenario_weights = {k: v / total_weight for k, v in scenario_weights.items()}
    
    scenarios = []
    for scenario, weight in scenario_weights.items():
        count = max(1, int(total_records * weight))
        scenarios.extend([scenario] * count)
    random.shuffle(scenarios)
    
    # Ensure we have enough scenarios
    while len(scenarios) < total_records:
        scenarios.extend(random.choices(list(scenario_weights.keys()), 
                                         weights=list(scenario_weights.values()), 
                                         k=total_records - len(scenarios)))

    current_time = start_time or (datetime.now() - timedelta(minutes=total_records * args.interval))
    records_generated = 0
    all_records = []
    
    # Set random seed for reproducibility
    random.seed(args.seed)
    
    print(f"Streaming mode: {total_records} records, batch_size={batch_size}, interval={interval_ms}ms")
    print(f"Output: {output_path}")
    
    try:
        while records_generated < total_records:
            batch = []
            for _ in range(min(batch_size, total_records - records_generated)):
                profile = random.choice(PRODUCT_PROFILES)
                scenario = scenarios[records_generated % len(scenarios)]
                
                generator = SCENARIO_GENERATORS.get(scenario, generate_normal_fluctuation)
                record = generator(profile, current_time)
                batch.append(record)
                all_records.append(record)
                
                current_time += timedelta(minutes=args.interval)
                records_generated += 1
            
            # Output batch as JSON lines to stdout
            for record in batch:
                print(json.dumps(asdict(record), default=str), flush=True)
            
            # Also write accumulated records to output file
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump([asdict(r) for r in all_records], f, indent=2, default=str)
            
            if records_generated < total_records:
                time.sleep(interval_ms / 1000.0)
        
        print(f"\nCompleted: {records_generated} records written to {output_path}", file=sys.stderr)
        return 0
        
    except KeyboardInterrupt:
        print(f"\nInterrupted: {records_generated} records written to {output_path}", file=sys.stderr)
        return 0


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic pricing telemetry")
    parser.add_argument("--records", "-n", type=int, default=500, help="Number of records to generate")
    parser.add_argument("--output", "-o", type=str, default="tests/fixtures/telemetry_sample.json", help="Output JSON path")
    parser.add_argument("--interval", "-i", type=int, default=15, help="Interval in minutes between records")
    parser.add_argument("--seed", "-s", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--scenario", type=str, help="Single scenario to generate (overrides mix)")
    parser.add_argument("--start", type=str, help="Start time ISO format (default: now - records*interval)")
    # Streaming flags
    parser.add_argument("--stream", action="store_true", help="Enable streaming mode (output batches continuously)")
    parser.add_argument("--interval-ms", type=int, default=200, help="Interval in milliseconds between batches (streaming mode)")
    parser.add_argument("--batch-size", type=int, default=10, help="Number of records per batch (streaming mode)")

    args = parser.parse_args()

    # Parse scenario weights if single scenario specified
    scenario_weights = None
    if args.scenario:
        try:
            scenario = ScenarioType(args.scenario)
            scenario_weights = {scenario: 1.0}
        except ValueError:
            print(f"Unknown scenario: {args.scenario}")
            print(f"Available: {[s.value for s in ScenarioType]}")
            return 1

    # Parse start time
    start_time = None
    if args.start:
        try:
            start_time = datetime.fromisoformat(args.start)
        except ValueError:
            print(f"Invalid start time format. Use ISO format (YYYY-MM-DDTHH:MM:SS)")
            return 1

    if args.stream:
        return run_streaming_mode(args)

    records = generate_telemetry(
        num_records=args.records,
        scenario_weights=scenario_weights,
        start_time=start_time,
        interval_minutes=args.interval,
        seed=args.seed
    )

    output_path = Path(args.output)
    write_telemetry_json(records, output_path)

    # Print summary
    scenario_counts = {}
    for r in records:
        scenario_counts[r.scenario] = scenario_counts.get(r.scenario, 0) + 1

    print("\nScenario distribution:")
    for scenario, count in sorted(scenario_counts.items()):
        print(f"  {scenario}: {count} ({count/len(records)*100:.1f}%)")

    guardrail_counts = {}
    for r in records:
        guardrail_counts[r.guardrail_status] = guardrail_counts.get(r.guardrail_status, 0) + 1

    print("\nGuardrail status distribution:")
    for status, count in sorted(guardrail_counts.items()):
        print(f"  {status}: {count} ({count/len(records)*100:.1f}%)")

    suppression_count = sum(1 for r in records if r.suppression_triggered)
    print(f"\nSuppressions triggered: {suppression_count} ({suppression_count/len(records)*100:.1f}%)")

    return 0


if __name__ == "__main__":
    exit(main())