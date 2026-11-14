# Cost Floor Mathematics & Financial Guardrails

## The Break-Even Floor Price Equation

To defend profit margins against marketplace platform commission structures and variable logistics fees, the engine computes the exact minimum price required to achieve the merchant's target net dollar margin:

$$\text{Floor Price} = \frac{\text{COGS} + \text{Packaging} + \text{Fulfillment} + \text{Shipping} + \text{Min Target Margin}}{1 - \text{Referral Fee Rate}}$$

### Why Standard Floating-Point Arithmetic Is Prohibited
In IEEE-754 binary floating-point representation:
```python
0.1 + 0.2 == 0.30000000000000004  # True
Decimal("0.1") + Decimal("0.2") == Decimal("0.3")  # True
```
In high-volume e-commerce repricing (100,000+ operations/day), tiny floating-point rounding errors compound into thousands of dollars in lost margin or false guardrail trigger rejections.
**All pricing math in this codebase must strictly use `decimal.Decimal`.**

## Step-by-Step Mathematical Definitions

1. **Total Variable Costs ($TVC$):**
   $$TVC = \text{Packaging} + \text{Fulfillment} + \text{Shipping} + \text{FBA Fee} + \text{Payment Processing Fee}$$

2. **Base Cost ($BC$):**
   $$BC = \text{COGS} + TVC$$

3. **Break-Even Floor Price ($FP$):**
   $$FP = \frac{BC + \text{Min Target Dollar Margin}}{1 - r}$$
   Where $r$ is the category referral fee rate expressed as a decimal (e.g., $0.15$ for 15%).

4. **Margin Markup Percentage at Given Price ($P$):**
   $$\text{Margin \%} = \frac{P - BC}{BC} \times 100$$
   *At floor price with target margin $M$, $\text{Margin \%} = \frac{M}{BC} \times 100$.*

## Category Commission Rates Table

| Category | Referral Fee Rate | Minimum Referral Fee |
| :--- | :--- | :--- |
| Electronics | 8.0% (over $100) / 15.0% | $0.30 |
| Home & Kitchen | 15.0% | $0.30 |
| Beauty | 8.0% (under $10) / 15.0% | $0.30 |
| Sports & Outdoors | 15.0% | $0.30 |
| Toys & Games | 15.0% | $0.30 |
| Pet Supplies | 15.0% | $0.30 |
| Health & Household | 8.0% (under $10) / 15.0% | $0.30 |
| Office Products | 15.0% | $0.30 |
