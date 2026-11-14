# GEMINI.md - Workspace Rules for Google Gemini & Antigravity

Welcome to the **Pricing Guardrail Engine** repository.

## Mission & Architecture
High-throughput automated pricing governance and real-time margin defense engine for multi-channel e-commerce sellers.
- **Fundamental Invariant:** `new_price >= floor_price`.
- **Break-Even Floor Formula:**
  $$\text{Floor Price} = \frac{\text{COGS} + \text{Packaging} + \text{Fulfillment} + \text{Shipping} + \text{Min Margin}}{1 - \text{Referral Fee Rate}}$$
- **Zero-ETL Live Connectors:** Direct SQL & REST connectors stream live client pricing in memory without local database writes.
- **7-Day ML Forecaster:** `GradientBoostingClassifier` trained on 52,750+ telemetry events predicting 7-day floor breach risks.
- **Immutable Audit Trail:** PostgreSQL trigger-enforced append-only logs (`price_change_log`, `telemetry_raw`).

## Development Invariants
1. **Financial Precision:** Always use Python `Decimal`. Never use floating-point for pricing math.
2. **Security:** Use parameterized queries (`%s`). Do not attempt `UPDATE` or `DELETE` on trigger-protected audit tables.
3. **Database Health:** Always release acquired connections back to `ThreadedConnectionPool`.
4. **Validation:** Ensure all 119 tests pass before completing changes: `python -m pytest tests/ -q`.

## Skills & Documentation
This project includes a native Agent Skill at:
- `.agents/skills/pricing-guardrail-engine/SKILL.md`
- `skills/pricing-guardrail-engine/SKILL.md`

Refer to the skill references for in-depth architecture, cost floor formulas, zero-ETL connector guides, and benchmark specifications.
