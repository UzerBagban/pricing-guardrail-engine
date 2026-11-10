"""
Floor Price Calculator
Core pricing engine: FloorPrice = COGS + Shipping + VariableFees + MinMargin
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from enum import Enum


class CostComponent(str, Enum):
    COGS = "cogs"
    PACKAGING = "packaging"
    FULFILLMENT = "fulfillment"
    SHIPPING = "shipping"
    REFERRAL_FEE = "referral_fee"
    FBA_FEE = "fba_fee"
    PROCESSING_FEE = "processing_fee"
    MIN_MARGIN = "min_margin"


@dataclass
class CostBreakdown:
    """Detailed cost breakdown for floor price calculation."""
    cogs_per_unit: Decimal
    packaging_cost: Decimal = Decimal("0")
    fulfillment_cost: Decimal = Decimal("0")
    shipping_cost: Decimal = Decimal("0")
    referral_fee: Decimal = Decimal("0")
    fba_fee: Decimal = Decimal("0")
    processing_fee: Decimal = Decimal("0")
    min_margin_amount: Decimal = Decimal("0")

    def total_variable_costs(self) -> Decimal:
        """Sum of all variable costs EXCLUDING COGS (packaging, fulfillment, shipping, fees)."""
        return (
            self.packaging_cost
            + self.fulfillment_cost
            + self.shipping_cost
            + self.referral_fee
            + self.fba_fee
            + self.processing_fee
        )

    def base_cost(self) -> Decimal:
        """Base cost = COGS + VariableCosts (all costs before margin)."""
        return self.cogs_per_unit + self.total_variable_costs()

    def floor_price(self) -> Decimal:
        """Calculate floor price: BaseCost + MinMargin (where MinMargin = BaseCost * margin_pct/100)."""
        return (self.base_cost() + self.min_margin_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def margin_at_price(self, price: Decimal) -> Decimal:
        """Calculate margin percentage at a given price (markup on base cost).
        
        Margin% = (Price - BaseCost) / BaseCost * 100
        This represents the markup on base cost at the given price.
        At floor price, margin = min_margin_pct exactly.
        """
        if price <= 0:
            return Decimal("0")
        base_cost = self.base_cost()
        if base_cost == 0:
            return Decimal("0")
        return ((price - base_cost) / base_cost * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def to_dict(self) -> dict:
        return {k: float(v) for k, v in self.__dict__.items()}


@dataclass
class PricingRule:
    """Guardrail configuration from pricing_rules table."""
    min_margin_pct: Decimal
    max_premium_pct: Optional[Decimal] = None
    vrp_drift_threshold_pct: Decimal = Decimal("5.0")
    elasticity_threshold: Decimal = Decimal("2.5")
    suppression_enabled: bool = True
    auto_adjust_enabled: bool = False


def calculate_min_margin_amount(
    base_cost: Decimal,
    min_margin_pct: Decimal
) -> Decimal:
    """
    Calculate minimum margin amount in currency units.
    Formula: MinMargin = BaseCost * MinMarginPct / 100
    
    Where BaseCost = COGS + VariableCosts (all costs before margin).
    FloorPrice = BaseCost + MinMargin
    """
    if min_margin_pct <= 0:
        return Decimal("0")
    
    return (base_cost * min_margin_pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_floor_price(
    cogs_per_unit: Decimal,
    packaging_cost: Decimal,
    fulfillment_cost: Decimal,
    shipping_cost: Decimal,
    referral_fee: Decimal,
    fba_fee: Decimal,
    processing_fee: Decimal,
    min_margin_pct: Decimal
) -> CostBreakdown:
    """
    Main floor price calculation.

    FloorPrice = BaseCost + MinMargin
    Where BaseCost = COGS + VariableCosts
    And MinMargin = BaseCost * MinMarginPct / 100
    """
    variable_costs = (
        packaging_cost
        + fulfillment_cost
        + shipping_cost
        + referral_fee
        + fba_fee
        + processing_fee
    )
    
    base_cost = cogs_per_unit + variable_costs
    min_margin_amount = calculate_min_margin_amount(base_cost, min_margin_pct)

    return CostBreakdown(
        cogs_per_unit=cogs_per_unit,
        packaging_cost=packaging_cost,
        fulfillment_cost=fulfillment_cost,
        shipping_cost=shipping_cost,
        referral_fee=referral_fee,
        fba_fee=fba_fee,
        processing_fee=processing_fee,
        min_margin_amount=min_margin_amount,
    )


def calculate_shipping_cost(
    weight_kg: Decimal,
    base_rate: Decimal,
    per_kg_rate: Decimal,
    min_charge: Decimal
) -> Decimal:
    """Calculate shipping cost based on weight tier."""
    if weight_kg <= 0:
        return Decimal("0")
    cost = base_rate + (weight_kg * per_kg_rate)
    return max(cost, min_charge).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_referral_fee(price: Decimal, category_rate_pct: Decimal) -> Decimal:
    """Calculate marketplace referral fee."""
    if price <= 0 or category_rate_pct <= 0:
        return Decimal("0")
    return (price * category_rate_pct / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_fba_fee(
    weight_kg: Decimal,
    size_tier: str,  # 'standard', 'oversize', 'special'
    is_media: bool = False
) -> Decimal:
    """
    Calculate FBA fulfillment fee (simplified Amazon-like structure).
    In production, this would use actual fee tables.
    """
    fee_tables = {
        "standard": {
            (0, Decimal("0.5")): Decimal("3.50"),
            (Decimal("0.5"), Decimal("1")): Decimal("4.50"),
            (Decimal("1"), Decimal("2")): Decimal("5.50"),
        },
        "oversize": {
            (0, Decimal("2")): Decimal("8.00"),
            (Decimal("2"), Decimal("5")): Decimal("12.00"),
            (Decimal("5"), Decimal("10")): Decimal("18.00"),
        },
    }

    table = fee_tables.get(size_tier, fee_tables["standard"])
    for (min_w, max_w), fee in table.items():
        if min_w <= weight_kg < max_w:
            return fee
    # Default to highest tier
    return max(table.values())


def calculate_processing_fee(price: Decimal, fixed_fee: Decimal = Decimal("0.30"), pct_fee: Decimal = Decimal("2.9")) -> Decimal:
    """Calculate payment processing fee (e.g., Stripe: 2.9% + $0.30)."""
    if price <= 0:
        return Decimal("0")
    return (fixed_fee + price * pct_fee / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_applicable_rule(
    rules: list[PricingRule],
    product_sku: Optional[str] = None,
    product_category: Optional[str] = None
) -> PricingRule:
    """
    Select the highest priority (lowest number) active rule matching the product.
    Priority order: product > category > global
    """
    # Sort by priority (ascending = higher priority first)
    sorted_rules = sorted(rules, key=lambda r: r.min_margin_pct)

    # Check product-specific rules first
    if product_sku:
        for rule in sorted_rules:
            if rule.min_margin_pct > 0:  # Placeholder for scope matching
                pass  # In real impl, check scope_type == 'product' and scope_value == sku

    # For now, return the most restrictive (highest min_margin) as fallback
    return max(rules, key=lambda r: r.min_margin_pct)


# Convenience function for simple calculations
def quick_floor_price(
    cogs: float,
    shipping: float,
    fees: float,
    min_margin_pct: float
) -> float:
    """
    Quick floor price calculation with minimal inputs.
    FloorPrice = (COGS + Shipping + Fees) * (1 + min_margin_pct / 100)
    Where base = COGS + Shipping + Fees
    """
    cogs_d = Decimal(str(cogs))
    shipping_d = Decimal(str(shipping))
    fees_d = Decimal(str(fees))
    margin_pct_d = Decimal(str(min_margin_pct))

    base_cost = cogs_d + shipping_d + fees_d
    min_margin = calculate_min_margin_amount(base_cost, margin_pct_d)
    floor = base_cost + min_margin

    return float(floor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


if __name__ == "__main__":
    # Demo / quick test
    print("=== Floor Price Calculator Demo ===\n")

    # Example: Electronics product
    breakdown = calculate_floor_price(
        cogs_per_unit=Decimal("28.50"),
        packaging_cost=Decimal("1.20"),
        fulfillment_cost=Decimal("3.50"),
        shipping_cost=Decimal("4.20"),
        referral_fee=Decimal("8.00"),
        fba_fee=Decimal("4.50"),
        processing_fee=Decimal("2.50"),
        min_margin_pct=Decimal("20.00"),
    )

    print(f"Cost Breakdown:")
    for k, v in breakdown.to_dict().items():
        print(f"  {k}: ${v:.2f}")
    print(f"\nTotal Variable Costs: ${float(breakdown.total_variable_costs()):.2f}")
    print(f"Min Margin Amount: ${float(breakdown.min_margin_amount):.2f}")
    print(f"FLOOR PRICE: ${float(breakdown.floor_price()):.2f}")

    # Test margin at different prices
    floor = breakdown.floor_price()
    for price in [floor, floor * Decimal("1.1"), floor * Decimal("1.25")]:
        margin = breakdown.margin_at_price(price)
        print(f"  Price ${price:.2f} -> Margin {margin:.2f}%")

    print("\n=== Quick Calc ===")
    print(f"Quick floor (COGS=25, Ship=3, Fees=5, Margin=15%): ${quick_floor_price(25, 3, 5, 15):.2f}")