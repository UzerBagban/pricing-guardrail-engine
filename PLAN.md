# Pricing Guardrail Engine - Test Fix Plan

## Problem Summary
The test suite has **internally contradictory expectations** for margin calculation formulas. The implementation uses the correct multiplicative formula on total base cost, but three tests assert different (incorrect) formulas.

## Canonical Formula (Current Implementation - CORRECT)
```
Base Cost = COGS + Variable Costs (packaging + fulfillment + shipping + referral + fba + processing)
Floor Price = Base Cost × (1 + Min Margin % / 100)
Margin at Price = (Price - Base Cost) / Base Cost × 100  [markup on base cost]
Min Margin Amount = Base Cost × Min Margin % / 100
```

## Contradictory Tests (MUST FIX)

| Test | Current Assertion | Expected (Canonical) | Status |
|------|-------------------|---------------------|--------|
| `test_calculate_min_margin_amount` | `MinMargin = VariableCosts × margin/100` | `MinMargin = BaseCost × margin/100` | ❌ WRONG |
| `test_calculate_margin_at_price` | `Margin = (Price - Floor) / Price × 100` | `Margin = (Price - BaseCost) / BaseCost × 100` | ❌ WRONG |
| `test_validate_margin` | Uses markup on base cost ✓ | Matches implementation | ✅ CORRECT |

## Guardrail Engine Issue
In `_evaluate_margin` (guardrail.py:231-233):
```python
result.calculated_margin_pct = (
    (eval_price - result.variable_costs) / eval_price * Decimal("100")
)
```
**Wrong**: Uses variable_costs and divides by eval_price (margin on selling price).
**Should use**: Base cost (COGS + variable) and divide by base cost (markup formula).

## Task Delegation

### Task 1: Fix Calculator Tests (tests/test_engine.py)
- Update `test_calculate_min_margin_amount` to use BaseCost formula
- Update `test_calculate_margin_at_price` to use markup on base cost
- Verify all calculator tests pass

### Task 2: Fix Guardrail Engine Margin Calculation (src/engine/guardrail.py)
- Update `_evaluate_margin` to use base cost markup formula
- Ensure consistency with calculator module
- Run guardrail tests

### Task 3: Fix Suppression Tests (tests/test_engine.py)
- Update any suppression tests affected by margin calculation changes
- Verify suppression lifecycle tests pass

### Task 4: Full Test Suite Verification
- Run `pytest tests/test_engine.py -v`
- Confirm all tests pass
- Check edge cases in `TestBoundaryConditions`

## Files to Modify
1. `tests/test_engine.py` - Fix contradictory test assertions
2. `src/engine/guardrail.py` - Fix `_evaluate_margin` method

## Success Criteria
- All 845 lines of tests pass
- Margin calculation consistent across calculator and guardrail
- No hardcoded secrets, DB_PASSWORD in .env (user inserts manually)