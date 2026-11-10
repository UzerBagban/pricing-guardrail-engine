"""
Automated Deal Suppression Engine
Generates and manages suppression events when target prices violate margin guardrails.
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime, timedelta
import uuid
import json

from src.engine.guardrail import (
    PriceEvaluationInput,
    PriceEvaluationResult,
    GuardrailStatus,
    RecommendedAction,
    VRPGuardrailEngine,
)


class SuppressionReason(str, Enum):
    BELOW_FLOOR_PRICE = "below_floor_price"
    MARGIN_BREACH = "margin_breach"
    VRP_DRIFT_CRITICAL = "vrp_drift_critical"
    ELASTICITY_BREACH = "elasticity_breach"
    BUYBOX_LOSS = "buybox_loss"
    MANUAL_OVERRIDE = "manual_override"
    POLICY_VIOLATION = "policy_violation"


class SuppressionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    OVERRIDDEN = "overridden"


class SuppressionScope(str, Enum):
    PRODUCT = "product"
    CATEGORY = "category"
    CHANNEL = "channel"
    GLOBAL = "global"


@dataclass
class SuppressionEvent:
    """Represents a deal suppression event."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    product_id: str = ""
    sku: str = ""
    asin: str = ""
    channel: str = "amazon"

    # Trigger details
    reason: SuppressionReason = SuppressionReason.BELOW_FLOOR_PRICE
    trigger_price: Decimal = Decimal("0")
    floor_price: Decimal = Decimal("0")
    vrp_price: Optional[Decimal] = None
    vrp_drift_pct: Optional[Decimal] = None
    margin_pct: Decimal = Decimal("0")
    min_margin_pct: Decimal = Decimal("0")

    # Scope
    scope: SuppressionScope = SuppressionScope.PRODUCT
    scope_value: Optional[str] = None  # e.g., category name

    # Status lifecycle
    status: SuppressionStatus = SuppressionStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    activated_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    # Override tracking
    overridden_by: Optional[str] = None
    override_reason: Optional[str] = None
    override_expires_at: Optional[datetime] = None

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    evaluation_snapshot: Optional[Dict[str, Any]] = None  # Full guardrail result

    def activate(self) -> None:
        """Mark suppression as active."""
        self.status = SuppressionStatus.ACTIVE
        self.activated_at = datetime.now()
        if not self.expires_at:
            # Default 24-hour expiry
            self.expires_at = self.activated_at + timedelta(hours=24)

    def release(self, released_by: str = "system", reason: str = "auto_release") -> None:
        """Release the suppression."""
        self.status = SuppressionStatus.RELEASED
        self.released_at = datetime.now()
        self.metadata["released_by"] = released_by
        self.metadata["release_reason"] = reason

    def override(self, authorized_by: str, reason: str, expires_hours: int = 4) -> None:
        """Override suppression with authorization."""
        self.status = SuppressionStatus.OVERRIDDEN
        self.overridden_by = authorized_by
        self.override_reason = reason
        self.override_expires_at = datetime.now() + timedelta(hours=expires_hours)

    def check_expiry(self) -> bool:
        """Check if suppression has expired. Returns True if expired."""
        if self.expires_at and datetime.now() > self.expires_at:
            if self.status in (SuppressionStatus.ACTIVE, SuppressionStatus.PENDING):
                self.status = SuppressionStatus.EXPIRED
                return True
        if self.override_expires_at and datetime.now() > self.override_expires_at:
            if self.status == SuppressionStatus.OVERRIDDEN:
                self.status = SuppressionStatus.ACTIVE  # Revert to active
                return True
        return False

    def is_active(self) -> bool:
        """Check if suppression is currently blocking deals."""
        self.check_expiry()
        # OVERRIDDEN means deal can proceed, so not "active" blocking
        return self.status in (SuppressionStatus.ACTIVE, SuppressionStatus.PENDING)

    def to_dict(self) -> dict:
        result = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Decimal):
                result[k] = float(v)
            elif isinstance(v, datetime):
                result[k] = v.isoformat() if v else None
            elif isinstance(v, Enum):
                result[k] = v.value
            elif v is None:
                result[k] = None
            else:
                result[k] = v
        return result


@dataclass
class SuppressionRule:
    """Configuration for suppression behavior."""
    # Triggers that activate suppression
    suppress_on_below_floor: bool = True
    suppress_on_margin_breach: bool = True
    suppress_on_vrp_drift_critical: bool = True
    suppress_on_elasticity_breach: bool = False  # Usually warning only
    suppress_on_buybox_loss: bool = False  # Usually warning only

    # Thresholds
    vrp_drift_critical_pct: Decimal = Decimal("10.0")  # >10% VRP drop = critical
    elasticity_critical_threshold: Decimal = Decimal("5.0")

    # Behavior
    default_duration_hours: int = 24
    max_duration_hours: int = 168  # 1 week max
    require_manual_release: bool = False
    allow_auto_release: bool = True

    # Auto-release conditions
    auto_release_on_vrp_recovery: bool = True
    auto_release_on_price_correction: bool = True
    vrp_recovery_threshold_pct: Decimal = Decimal("2.0")  # Within 2% of VRP

    # Notifications
    notify_on_suppression: bool = True
    notify_on_release: bool = True
    notify_channels: List[str] = field(default_factory=lambda: ["slack", "email"])


class SuppressionEngine:
    """
    Manages suppression events lifecycle.
    """

    def __init__(
        self,
        rule: Optional[SuppressionRule] = None,
        guardrail_engine: Optional[VRPGuardrailEngine] = None
    ):
        self.rule = rule or SuppressionRule()
        self.guardrail_engine = guardrail_engine or VRPGuardrailEngine()
        self.suppressions: Dict[str, SuppressionEvent] = {}

    def evaluate_and_suppress(self, input_data: PriceEvaluationInput) -> List[SuppressionEvent]:
        """
        Evaluate price through guardrail and create suppression events if triggered.
        Returns list of new suppression events created.
        """
        # Run guardrail evaluation
        result = self.guardrail_engine.evaluate(input_data)

        new_suppressions = []

        # Check each suppression trigger condition
        if self.rule.suppress_on_below_floor and "below_floor_price" in result.violations:
            event = self._create_suppression(
                input_data=input_data,
                result=result,
                reason=SuppressionReason.BELOW_FLOOR_PRICE,
            )
            new_suppressions.append(event)

        if self.rule.suppress_on_margin_breach and "margin_below_minimum" in result.violations:
            event = self._create_suppression(
                input_data=input_data,
                result=result,
                reason=SuppressionReason.MARGIN_BREACH,
            )
            new_suppressions.append(event)

        if self.rule.suppress_on_vrp_drift_critical and result.vrp_drift_detected:
            if result.vrp_drift_pct and result.vrp_drift_pct < -self.rule.vrp_drift_critical_pct:
                event = self._create_suppression(
                    input_data=input_data,
                    result=result,
                    reason=SuppressionReason.VRP_DRIFT_CRITICAL,
                )
                new_suppressions.append(event)

        if self.rule.suppress_on_elasticity_breach and result.elasticity_breach:
            if result.elasticity_score and result.elasticity_score > self.rule.elasticity_critical_threshold:
                event = self._create_suppression(
                    input_data=input_data,
                    result=result,
                    reason=SuppressionReason.ELASTICITY_BREACH,
                )
                new_suppressions.append(event)

        if self.rule.suppress_on_buybox_loss and "buybox_lost" in result.violations:
            event = self._create_suppression(
                input_data=input_data,
                result=result,
                reason=SuppressionReason.BUYBOX_LOSS,
            )
            new_suppressions.append(event)

        return new_suppressions

    def _create_suppression(
        self,
        input_data: PriceEvaluationInput,
        result: PriceEvaluationResult,
        reason: SuppressionReason
    ) -> SuppressionEvent:
        """Create a new suppression event."""
        event = SuppressionEvent(
            product_id=input_data.product_id,
            sku=input_data.sku,
            asin=input_data.asin,
            channel=input_data.channel,
            reason=reason,
            trigger_price=input_data.proposed_price or input_data.our_current_price,
            floor_price=result.floor_price,
            vrp_price=input_data.vrp_price,
            vrp_drift_pct=result.vrp_drift_pct,
            margin_pct=result.calculated_margin_pct,
            min_margin_pct=input_data.min_margin_pct,
            scope=SuppressionScope.PRODUCT,
            scope_value=input_data.sku,
            expires_at=datetime.now() + timedelta(hours=self.rule.default_duration_hours),
            evaluation_snapshot=result.to_dict(),
            metadata={
                "guardrail_status": result.status.value,
                "recommended_action": result.recommended_action.value,
                "recommended_price": float(result.recommended_price) if result.recommended_price else None,
            }
        )

        event.activate()
        self.suppressions[event.id] = event
        return event

    def get_active_suppressions(self, product_id: Optional[str] = None) -> List[SuppressionEvent]:
        """Get all currently active suppressions, optionally filtered by product."""
        active = []
        for event in self.suppressions.values():
            event.check_expiry()
            if event.is_active():
                if product_id is None or event.product_id == product_id:
                    active.append(event)
        return active

    def get_suppression(self, suppression_id: str) -> Optional[SuppressionEvent]:
        """Get a specific suppression by ID."""
        event = self.suppressions.get(suppression_id)
        if event:
            event.check_expiry()
        return event

    def release_suppression(
        self,
        suppression_id: str,
        released_by: str = "system",
        reason: str = "manual_release"
    ) -> bool:
        """Release a specific suppression."""
        event = self.get_suppression(suppression_id)
        if event and event.is_active():
            event.release(released_by, reason)
            return True
        return False

    def override_suppression(
        self,
        suppression_id: str,
        authorized_by: str,
        reason: str,
        expires_hours: int = 4
    ) -> bool:
        """Override a suppression with authorization."""
        event = self.get_suppression(suppression_id)
        if event and event.is_active():
            # Enforce max override duration
            expires_hours = min(expires_hours, self.rule.max_duration_hours)
            event.override(authorized_by, reason, expires_hours)
            return True
        return False

    def check_auto_release_conditions(self, current_vrp: Optional[Decimal] = None) -> List[SuppressionEvent]:
        """
        Check all active suppressions for auto-release conditions.
        Returns list of suppressions that were auto-released.
        """
        released = []

        for event in self.suppressions.values():
            if not event.is_active():
                continue

            # Check VRP recovery
            if self.rule.auto_release_on_vrp_recovery and event.vrp_price and current_vrp:
                if event.vrp_drift_pct and event.vrp_drift_pct < 0:
                    # Was negative drift, check if recovered
                    current_drift = (
                        (current_vrp - event.trigger_price) / event.trigger_price * Decimal("100")
                    ).quantize(Decimal("0.01"))
                    if current_drift > -self.rule.vrp_recovery_threshold_pct:
                        event.release("system", "vrp_recovered")
                        released.append(event)

            # Check price correction
            if self.rule.auto_release_on_price_correction:
                # Would need current price to check
                pass  # Implemented when price feed available

        return released

    def get_suppression_summary(self) -> Dict[str, Any]:
        """Get summary statistics of suppressions."""
        active = self.get_active_suppressions()

        by_reason = {}
        by_status = {}
        for event in self.suppressions.values():
            by_reason[event.reason.value] = by_reason.get(event.reason.value, 0) + 1
            by_status[event.status.value] = by_status.get(event.status.value, 0) + 1

        return {
            "total": len(self.suppressions),
            "active": len(active),
            "by_reason": by_reason,
            "by_status": by_status,
            "active_products": len(set(e.product_id for e in active)),
        }


def create_suppression_from_evaluation(
    result: PriceEvaluationResult,
    input_data: PriceEvaluationInput,
    reason: SuppressionReason,
    rule: Optional[SuppressionRule] = None
) -> SuppressionEvent:
    """Create suppression directly from evaluation result (for external integration)."""
    engine = SuppressionEngine(rule)
    event = engine._create_suppression(input_data, result, reason)
    return event


def is_price_suppressed(
    product_id: str,
    sku: str,
    engine: Optional[SuppressionEngine] = None
) -> tuple[bool, Optional[SuppressionEvent]]:
    """
    Check if a product/sku has an active suppression.
    Returns (is_suppressed, suppression_event).
    """
    if engine is None:
        engine = SuppressionEngine()

    active = engine.get_active_suppressions(product_id)
    for event in active:
        # Match by SKU for SKU-level suppressions, or by scope_value for PRODUCT scope
        if event.sku == sku:
            return True, event
        if event.scope == SuppressionScope.PRODUCT and event.scope_value == sku:
            return True, event
    return False, None


# Integration with guardrail for end-to-end flow
def evaluate_and_enforce(
    input_data: PriceEvaluationInput,
    suppression_rule: Optional[SuppressionRule] = None,
    guardrail_rule=None,  # PricingRule
    suppression_engine: Optional[SuppressionEngine] = None
) -> tuple[PriceEvaluationResult, List[SuppressionEvent], SuppressionEngine]:
    """
    Complete evaluation pipeline: guardrail evaluation + suppression enforcement.
    Returns (evaluation_result, suppression_events, suppression_engine).
    """
    guardrail_engine = VRPGuardrailEngine(guardrail_rule)
    if suppression_engine is None:
        suppression_engine = SuppressionEngine(suppression_rule, guardrail_engine)
    else:
        suppression_engine.guardrail_engine = guardrail_engine
        suppression_engine.rule = suppression_rule or suppression_engine.rule

    result = guardrail_engine.evaluate(input_data)
    suppressions = suppression_engine.evaluate_and_suppress(input_data)

    return result, suppressions, suppression_engine


if __name__ == "__main__":
    print("=== Suppression Engine Demo ===\n")

    # Setup
    suppression_rule = SuppressionRule(
        suppress_on_below_floor=True,
        suppress_on_margin_breach=True,
        suppress_on_vrp_drift_critical=True,
        vrp_drift_critical_pct=Decimal("10.0"),
        default_duration_hours=24,
    )

    engine = SuppressionEngine(suppression_rule)

    # Input that triggers suppression (below floor)
    input_data = PriceEvaluationInput(
        product_id="prod-elec-001",
        sku="ELEC-001",
        asin="B08N5WRWNW",
        category="Electronics",
        our_current_price=Decimal("40.00"),
        cogs_per_unit=Decimal("28.50"),
        packaging_cost=Decimal("1.20"),
        fulfillment_cost=Decimal("3.50"),
        shipping_cost=Decimal("4.20"),
        referral_fee=Decimal("8.00"),
        fba_fee=Decimal("4.50"),
        processing_fee=Decimal("2.50"),
        min_margin_pct=Decimal("20.0"),
        vrp_price=Decimal("79.99"),
        buybox_price=Decimal("75.00"),
        lowest_competitor_price=Decimal("72.00"),
    )

    print("Input: Price $40.00 (Floor ~$55)")
    result, suppressions, engine = evaluate_and_enforce(input_data, suppression_rule)

    print(f"\nGuardrail Result:")
    print(f"  Floor: ${result.floor_price:.2f}")
    print(f"  Margin: {result.calculated_margin_pct:.2f}%")
    print(f"  Status: {result.status.value}")
    print(f"  Violations: {result.violations}")
    print(f"  Recommended: {result.recommended_action.value} @ ${result.recommended_price:.2f}")

    print(f"\nSuppressions Created: {len(suppressions)}")
    for s in suppressions:
        print(f"  ID: {s.id[:8]}...")
        print(f"  Reason: {s.reason.value}")
        print(f"  Status: {s.status.value}")
        print(f"  Expires: {s.expires_at}")

    # Check active suppressions
    print(f"\nActive Suppressions: {len(engine.get_active_suppressions())}")
    print(f"Summary: {engine.get_suppression_summary()}")

    # Test override
    if suppressions:
        s = suppressions[0]
        print(f"\nOverriding suppression {s.id[:8]}...")
        engine.override_suppression(s.id, "admin_user", "Promotional event approved", 4)
        print(f"New status: {s.status.value}")
        print(f"Override expires: {s.override_expires_at}")

        # Test release
        print(f"\nReleasing suppression...")
        engine.release_suppression(s.id, "admin_user", "Promotion ended")
        print(f"New status: {s.status.value}")
    else:
        print("\nNo suppressions to test override/release")