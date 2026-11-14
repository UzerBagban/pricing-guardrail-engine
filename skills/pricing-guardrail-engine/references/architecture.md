# System Architecture & Component Interaction

## Architecture Overview

The Pricing Guardrail Engine operates as a decoupled, multi-tiered pipeline:

```
[Inbound Market Telemetry] ───┐
                              ▼
[External Client DB / REST] ──► [Schema Normalization & Type Coercion]
                                         │
                                         ▼
                            [Decimal Cost Floor Calculator]
                                         │
                                         ▼
                            [Sub-Millisecond Guardrail Engine]
                                         │
                 ┌───────────────────────┼───────────────────────┐
                 ▼                       ▼                       ▼
            [APPROVED]          [WARNING (TIGHT BUFFER)]    [BLOCKED (BREACH)]
                 │                       │                       │
                 ▼                       ▼                       ▼
          [Price Clamping]       [Alert Dispatcher]     [Suppression Queue]
                 │                       │                       │
                 └───────────────────────┼───────────────────────┘
                                         ▼
                         [Immutable PostgreSQL Audit Log]
                                         │
                                         ▼
                     [7-Day ML Forecaster & Executive API]
```

## Core Modules & Responsibilities

### 1. `src/engine/calculator.py`
- Implements `CostBreakdown` and `FloorCalculator`.
- Computes landed base cost: $\text{BaseCost} = \text{COGS} + \sum \text{VariableCosts}$.
- Calculates exact break-even floor price incorporating platform commission percentages.
- Calculates target margin markup over landed base cost.

### 2. `src/engine/guardrail.py`
- Implements `GuardrailEngine` and `PriceEvaluationInput`.
- Deterministic decision logic:
  - `APPROVED`: `proposed_price >= floor_price + buffer ($2.00)`.
  - `WARNING`: `floor_price <= proposed_price < floor_price + buffer`.
  - `BLOCKED`: `proposed_price < floor_price`.
  - `SUPPRESSED`: Catastrophic competitor race-to-the-bottom or severe negative margin.
- Sub-millisecond latency: `< 0.15 ms` per SKU.

### 3. `src/engine/adjuster.py` & `src/engine/suppression.py`
- **Adjuster:** If an algorithmic repricing rule attempts to drop below floor, clamps the price to `max(calculated_price, floor_price)`.
- **Suppression:** Places distressed listings into a dead-letter deal suppression queue with exponential backoff recovery.

### 4. `src/engine/predictive.py`
- Scikit-learn `GradientBoostingClassifier` trained on 52,750+ real-world events.
- Evaluates 6 key features: `floor_buffer_pct`, `competitor_gap_pct`, `vrp_drift_pct`, `elasticity_score`, `competitor_count`, `calculated_margin_pct`.
- Outputs risk tiers (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`) with recommended preventative actions.

### 5. `src/connectors/`
- Zero-ETL federated connectors streaming external client databases and REST feeds into memory without local DB writes.

### 6. `src/api/app.py`
- FastAPI web service exposing REST analytics, live simulation sandbox, executive memos, and connector management.
