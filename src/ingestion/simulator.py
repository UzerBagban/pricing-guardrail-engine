"""
Streaming Telemetry Generator & Shock Scenarios

Generates realistic telemetry events for the pricing guardrail pipeline.
Supports continuous streaming, batch generation, and configurable shock scenarios.
"""
import argparse
import json
import random
import time
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Iterator, Optional
from pathlib import Path

# Product catalog matching the test fixtures
PRODUCT_CATALOG = [
    {
        "product_id": "prod-elec-001",
        "sku": "ELEC-001",
        "asin": "B08N5WRWNW",
        "category": "Electronics",
        "base_price": 79.83,
        "floor_price": 45.20,
        "cogs": 28.50,
        "shipping": 3.50,
        "fees": 8.00,
        "min_margin": 12.0,
        "vrp_price": 79.83,
        "competitor_count": 12,
        "velocity": 15.0,
        "elasticity": 1.1,
    },
    {
        "product_id": "prod-sprt-002",
        "sku": "SPRT-002",
        "asin": "B08Y7K3L4M",
        "category": "Sports & Outdoors",
        "base_price": 29.99,
        "floor_price": 17.00,
        "cogs": 9.80,
        "shipping": 2.20,
        "fees": 3.50,
        "min_margin": 4.5,
        "vrp_price": 29.99,
        "competitor_count": 10,
        "velocity": 22.0,
        "elasticity": 0.9,
    },
    {
        "product_id": "prod-elec-002",
        "sku": "ELEC-002",
        "asin": "B09KJRN3HH",
        "category": "Electronics",
        "base_price": 25.05,
        "floor_price": 18.35,
        "cogs": 12.75,
        "shipping": 2.10,
        "fees": 3.00,
        "min_margin": 5.0,
        "vrp_price": 25.05,
        "competitor_count": 8,
        "velocity": 45.0,
        "elasticity": 0.9,
    },
    {
        "product_id": "prod-beau-001",
        "sku": "BEAU-001",
        "asin": "B09M2N4P6Q",
        "category": "Beauty",
        "base_price": 29.36,
        "floor_price": 13.75,
        "cogs": 4.50,
        "shipping": 1.20,
        "fees": 3.20,
        "min_margin": 5.85,
        "vrp_price": 29.36,
        "competitor_count": 9,
        "velocity": 34.0,
        "elasticity": 0.7,
    },
    {
        "product_id": "prod-home-002",
        "sku": "HOME-002",
        "asin": "B08XJ7K9L2",
        "category": "Home & Kitchen",
        "base_price": 42.50,
        "floor_price": 22.00,
        "cogs": 15.00,
        "shipping": 4.00,
        "fees": 5.50,
        "min_margin": 8.0,
        "vrp_price": 42.50,
        "competitor_count": 7,
        "velocity": 28.0,
        "elasticity": 1.0,
    },
    {
        "product_id": "prod-toy-001",
        "sku": "TOY-001",
        "asin": "B07XK9L2M3",
        "category": "Toys & Games",
        "base_price": 19.99,
        "floor_price": 11.00,
        "cogs": 6.50,
        "shipping": 1.80,
        "fees": 2.50,
        "min_margin": 6.0,
        "vrp_price": 19.99,
        "competitor_count": 15,
        "velocity": 50.0,
        "elasticity": 1.3,
    },
]

CHANNELS = ["amazon"]


class ScenarioType(Enum):
    """Telemetry scenario types."""
    NORMAL = "normal"
    COMPETITOR_UNDERCUT = "competitor_undercut"
    MARGIN_COMPRESSION = "margin_compression"
    VRP_DRIFT_DROP = "vrp_drift_drop"
    BUYBOX_LOSS = "buybox_loss"


class EventType(Enum):
    """Telemetry event types."""
    PRICE_UPDATE = "price_update"
    COMPETITOR_UNDERCUT = "competitor_undercut"
    FEE_CHANGE = "fee_change"
    VRP_DRIFT_ALERT = "vrp_drift_alert"
    BUYBOX_LOST = "buybox_lost"


@dataclass
class ShockConfig:
    """Configuration for a shock scenario injection."""
    scenario: ScenarioType
    probability: float = 0.1  # Chance per event
    duration_events: int = 5  # How many consecutive events
    severity: float = 1.0  # Multiplier for impact


@dataclass
class SimulatorConfig:
    """Configuration for the telemetry simulator."""
    products: list[dict] = field(default_factory=lambda: PRODUCT_CATALOG)
    channels: list[str] = field(default_factory=lambda: CHANNELS)
    base_interval_ms: int = 1000  # Base interval between events
    interval_jitter_pct: float = 0.3  # ±30% jitter
    shock_configs: list[ShockConfig] = field(default_factory=list)
    seed: Optional[int] = None
    
    def __post_init__(self):
        if self.seed is not None:
            random.seed(self.seed)
        if not self.shock_configs:
            # Default shock configurations
            self.shock_configs = [
                ShockConfig(ScenarioType.COMPETITOR_UNDERCUT, probability=0.08, duration_events=3, severity=1.2),
                ShockConfig(ScenarioType.MARGIN_COMPRESSION, probability=0.05, duration_events=4, severity=1.0),
                ShockConfig(ScenarioType.VRP_DRIFT_DROP, probability=0.06, duration_events=3, severity=1.5),
                ShockConfig(ScenarioType.BUYBOX_LOSS, probability=0.04, duration_events=2, severity=1.3),
            ]


class TelemetrySimulator:
    """
    Generates streaming telemetry events with configurable shock scenarios.
    
    Produces events matching the TelemetryPayload schema expected by
    the ingestion evaluator.
    """
    
    def __init__(self, config: Optional[SimulatorConfig] = None):
        self.config = config or SimulatorConfig()
        self._shock_state: dict[str, dict] = {}  # Tracks active shocks per product
        self._event_counter = 0
        self._product_indices = {p["product_id"]: i for i, p in enumerate(self.config.products)}
        
    def _get_product(self, product_id: str) -> dict:
        """Get product catalog entry by ID."""
        idx = self._product_indices.get(product_id)
        if idx is not None:
            return self.config.products[idx]
        return self.config.products[0]
    
    def _apply_shock(self, product_id: str, base_event: dict) -> dict:
        """Apply active shock scenarios to an event."""
        if product_id not in self._shock_state:
            return base_event
            
        shock = self._shock_state[product_id]
        scenario = shock["scenario"]
        remaining = shock["remaining"]
        severity = shock["severity"]
        
        if remaining <= 0:
            del self._shock_state[product_id]
            return base_event
            
        shock["remaining"] -= 1
        event = base_event.copy()
        
        if scenario == ScenarioType.COMPETITOR_UNDERCUT:
            # Competitor undercuts our price significantly
            undercut_pct = random.uniform(10, 25) * severity / 100
            event["lowest_competitor_price"] = round(event["our_price"] * (1 - undercut_pct), 2)
            event["buybox_price"] = event["lowest_competitor_price"]
            event["event_type"] = EventType.COMPETITOR_UNDERCUT.value
            event["scenario"] = ScenarioType.COMPETITOR_UNDERCUT.value
            event["guardrail_status"] = "warning"
            event["metadata"] = event.get("metadata", {})
            event["metadata"]["undercut_pct"] = round(undercut_pct * 100, 2)
            event["metadata"]["buybox_lost"] = True
            event["metadata"]["recommended_action"] = "match_price"
            
        elif scenario == ScenarioType.MARGIN_COMPRESSION:
            # Fees increase or COGS rise, compressing margins
            fee_increase = random.uniform(15, 40) * severity / 100
            event["fees"] = round(event["fees"] * (1 + fee_increase), 2)
            # Recalculate margin
            revenue = event["our_price"] - event["fees"] - event["shipping"]
            margin_pct = (revenue - event["cogs"]) / event["our_price"] * 100
            event["calculated_margin_pct"] = round(margin_pct, 2)
            event["event_type"] = EventType.FEE_CHANGE.value
            event["scenario"] = ScenarioType.MARGIN_COMPRESSION.value
            if margin_pct < event["min_margin"]:
                event["guardrail_status"] = "critical"
            elif margin_pct < event["min_margin"] * 1.5:
                event["guardrail_status"] = "warning"
            event["metadata"] = event.get("metadata", {})
            event["metadata"]["fee_increase_pct"] = round(fee_increase * 100, 2)
            
        elif scenario == ScenarioType.VRP_DRIFT_DROP:
            # VRP drops significantly below our price
            drift_pct = random.uniform(8, 20) * severity / 100
            event["vrp_price"] = round(event["our_price"] * (1 - drift_pct), 2)
            event["vrp_drift_pct"] = round(drift_pct * 100, 2)
            event["drift_detected"] = event["vrp_drift_pct"] > event["vrp_threshold_pct"]
            event["event_type"] = EventType.VRP_DRIFT_ALERT.value
            event["scenario"] = ScenarioType.VRP_DRIFT_DROP.value
            if event["drift_detected"]:
                event["guardrail_status"] = "critical"
            event["metadata"] = event.get("metadata", {})
            event["metadata"]["vrp_drop_pct"] = round(drift_pct * 100, 2)
            
        elif scenario == ScenarioType.BUYBOX_LOSS:
            # Lose buybox to competitor
            loss_pct = random.uniform(5, 15) * severity / 100
            event["buybox_price"] = round(event["our_price"] * (1 - loss_pct), 2)
            event["lowest_competitor_price"] = event["buybox_price"]
            event["event_type"] = EventType.BUYBOX_LOST.value
            event["scenario"] = ScenarioType.BUYBOX_LOSS.value
            event["guardrail_status"] = "warning"
            event["metadata"] = event.get("metadata", {})
            event["metadata"]["buybox_lost"] = True
            event["metadata"]["buybox_loss_pct"] = round(loss_pct * 100, 2)
            event["metadata"]["velocity_impact_pct"] = round(random.uniform(10, 30), 1)
            
        return event
    
    def _maybe_trigger_shock(self, product_id: str) -> None:
        """Randomly trigger a new shock scenario for a product."""
        if product_id in self._shock_state:
            return  # Already in shock
            
        for shock_config in self.config.shock_configs:
            if random.random() < shock_config.probability:
                self._shock_state[product_id] = {
                    "scenario": shock_config.scenario,
                    "remaining": shock_config.duration_events,
                    "severity": shock_config.severity,
                }
                break
    
    def _generate_base_event(self, product: dict, timestamp: datetime) -> dict:
        """Generate a base telemetry event for a product."""
        # Small price drift over time
        price_drift = random.uniform(-0.5, 0.5) / 100
        our_price = round(product["base_price"] * (1 + price_drift), 2)
        
        # Competitor price around our price
        competitor_offset = random.uniform(-3, 3) / 100
        lowest_competitor_price = round(our_price * (1 + competitor_offset), 2)
        
        # Buybox price (usually matches lowest competitor or our price)
        buybox_price = min(our_price, lowest_competitor_price)
        
        # Calculate margin
        revenue = our_price - product["fees"] - product["shipping"]
        margin_pct = round((revenue - product["cogs"]) / our_price * 100, 2)
        
        # VRP drift
        vrp_drift_pct = round(random.uniform(-2, 3), 2)
        drift_detected = abs(vrp_drift_pct) > product.get("vrp_threshold_pct", 5.0)
        
        # Elasticity
        elasticity_score = round(random.uniform(0.5, 1.5), 3)
        elasticity_breach = elasticity_score > product.get("elasticity_threshold", 2.5)
        
        # Guardrail status
        if drift_detected or elasticity_breach or margin_pct < product["min_margin"]:
            guardrail_status = "critical" if (drift_detected or margin_pct < product["min_margin"]) else "warning"
        else:
            guardrail_status = "healthy"
        
        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": timestamp.isoformat(),
            "product_id": product["product_id"],
            "sku": product["sku"],
            "asin": product["asin"],
            "category": product["category"],
            "channel": random.choice(self.config.channels),
            "event_type": EventType.PRICE_UPDATE.value,
            "scenario": ScenarioType.NORMAL.value,
            "our_price": our_price,
            "floor_price": product["floor_price"],
            "vrp_price": product["vrp_price"],
            "buybox_price": buybox_price,
            "lowest_competitor_price": lowest_competitor_price,
            "competitor_count": product["competitor_count"],
            "cogs": product["cogs"],
            "shipping": product["shipping"],
            "fees": product["fees"],
            "min_margin": product["min_margin"],
            "calculated_margin_pct": margin_pct,
            "vrp_drift_pct": vrp_drift_pct,
            "vrp_threshold_pct": product.get("vrp_threshold_pct", 5.0),
            "drift_detected": drift_detected,
            "elasticity_score": elasticity_score,
            "elasticity_threshold": product.get("elasticity_threshold", 2.5),
            "elasticity_breach": elasticity_breach,
            "guardrail_status": guardrail_status,
            "suppression_triggered": False,
            "suppression_reason": None,
            "metadata": {
                "velocity": round(product["velocity"] * random.uniform(0.8, 1.2), 1),
                "price_change_pct": round(price_drift * 100, 2),
            },
        }
        
        return event
    
    def generate_event(self, product_id: Optional[str] = None) -> dict:
        """Generate a single telemetry event."""
        self._event_counter += 1
        
        # Select product
        if product_id:
            product = self._get_product(product_id)
        else:
            product = random.choice(self.config.products)
        
        # Check for shock triggers
        self._maybe_trigger_shock(product["product_id"])
        
        # Generate base event
        timestamp = datetime.utcnow()
        event = self._generate_base_event(product, timestamp)
        
        # Apply any active shock
        event = self._apply_shock(product["product_id"], event)
        
        return event
    
    def generate_batch(self, count: int, product_id: Optional[str] = None) -> list[dict]:
        """Generate a batch of telemetry events."""
        events = []
        base_time = datetime.utcnow()
        
        for i in range(count):
            # Stagger timestamps
            timestamp = base_time + timedelta(milliseconds=i * self.config.base_interval_ms)
            
            if product_id:
                product = self._get_product(product_id)
            else:
                product = random.choice(self.config.products)
            
            self._maybe_trigger_shock(product["product_id"])
            
            event = self._generate_base_event(product, timestamp)
            event = self._apply_shock(product["product_id"], event)
            events.append(event)
            
        return events
    
    def stream(self, max_events: Optional[int] = None, interval_ms: Optional[int] = None) -> Iterator[dict]:
        """
        Stream telemetry events continuously.
        
        Args:
            max_events: Maximum events to generate (None = infinite)
            interval_ms: Override base interval (ms)
            
        Yields:
            Telemetry event dicts
        """
        interval = interval_ms or self.config.base_interval_ms
        count = 0
        
        while max_events is None or count < max_events:
            event = self.generate_event()
            yield event
            count += 1
            
            # Add jitter
            jitter = interval * self.config.interval_jitter_pct
            actual_interval = interval + random.uniform(-jitter, jitter)
            time.sleep(max(0, actual_interval) / 1000.0)
    
    def write_jsonl(self, filepath: Path, count: int, product_id: Optional[str] = None) -> None:
        """Write events to a JSONL file."""
        events = self.generate_batch(count, product_id)
        with open(filepath, "w") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")
    
    def write_json(self, filepath: Path, count: int, product_id: Optional[str] = None) -> None:
        """Write events to a JSON array file."""
        events = self.generate_batch(count, product_id)
        with open(filepath, "w") as f:
            json.dump(events, f, indent=2)


def create_default_config(seed: Optional[int] = None) -> SimulatorConfig:
    """Create a default simulator configuration."""
    return SimulatorConfig(seed=seed)


def main():
    parser = argparse.ArgumentParser(description="Streaming Telemetry Generator & Shock Scenarios")
    parser.add_argument("--count", "-n", type=int, default=100, help="Number of events to generate")
    parser.add_argument("--output", "-o", type=str, help="Output file path (JSON or JSONL)")
    parser.add_argument("--format", "-f", choices=["json", "jsonl"], default="json", help="Output format")
    parser.add_argument("--product", "-p", type=str, help="Generate events for specific product ID")
    parser.add_argument("--stream", "-s", action="store_true", help="Stream continuously to stdout")
    parser.add_argument("--interval", "-i", type=int, default=1000, help="Stream interval in ms")
    parser.add_argument("--seed", type=int, help="Random seed for reproducibility")
    parser.add_argument("--shock-prob", type=float, default=0.1, help="Shock trigger probability per event")
    parser.add_argument("--list-products", action="store_true", help="List available products and exit")
    
    args = parser.parse_args()
    
    if args.list_products:
        print("Available products:")
        for prod in PRODUCT_CATALOG:
            print(f"  {prod['product_id']} (SKU: {prod['sku']}) - {prod['category']} - Base: ${prod['base_price']}")
        return
    
    config = SimulatorConfig(seed=args.seed)
    # Override shock probability if provided
    if args.shock_prob != 0.1:
        for sc in config.shock_configs:
            sc.probability = args.shock_prob
    
    simulator = TelemetrySimulator(config)
    
    if args.stream:
        # Stream to stdout
        try:
            for event in simulator.stream(max_events=args.count if args.count > 0 else None, interval_ms=args.interval):
                print(json.dumps(event))
        except KeyboardInterrupt:
            print("\nStreaming stopped.", file=sys.stderr)
    elif args.output:
        # Write to file
        output_path = Path(args.output)
        if args.format == "jsonl":
            simulator.write_jsonl(output_path, args.count, args.product)
        else:
            simulator.write_json(output_path, args.count, args.product)
        print(f"Generated {args.count} events -> {output_path}")
    else:
        # Print to stdout as JSON array
        events = simulator.generate_batch(args.count, args.product)
        print(json.dumps(events, indent=2))


if __name__ == "__main__":
    import sys
    main()