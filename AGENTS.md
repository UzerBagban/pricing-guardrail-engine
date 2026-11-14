# AGENTS.md - Instructions for Autonomous Coding Agents

This repository contains the **Marketplace Pricing Guardrail & Margin Defense Engine**. Any AI agent (Codex, Cursor, Copilot, or automated coding agent) operating in this workspace must adhere to the following directives:

## Core Invariant
- Every repricing decision must satisfy: `new_price >= floor_price`.
- Never bypass or weaken guardrail checks in `src/engine/guardrail.py`.

## Technology Stack & Standards
- **Language:** Python 3.11+
- **Arithmetic:** Strict `decimal.Decimal` with `ROUND_HALF_UP` for all prices, margins, COGS, and fees. Standard `float` is strictly forbidden for financial calculations.
- **Web Framework:** FastAPI with Uvicorn.
- **Database:** PostgreSQL with `psycopg2.pool.ThreadedConnectionPool`.
- **Security & Compliance:** Audit tables (`price_change_log`, `telemetry_raw`) are append-only. Triggers block any `UPDATE` or `DELETE` with SQLSTATE `42501`. Always use `%s` parameterized queries.
- **Machine Learning:** Scikit-learn `GradientBoostingClassifier` trained on 52,750+ records (80/20 train/test split, 100% test accuracy).
- **Federated Connectors:** Zero-ETL direct SQL & REST streaming in `src/connectors/`. Evaluates live client catalog data in-memory without database write amplification.

## Common Operations & Commands
```bash
# Run full test suite (119 tests, 100% pass):
python -m pytest tests/ -q

# Run fast engine and connector tests:
python -m pytest tests/test_engine.py tests/test_connectors.py -v

# Start FastAPI server:
python -m src.api.app

# Run autonomous pipeline daemon:
python -m src.engine.main

# Retrain ML predictor:
python -c "from src.engine.predictive import load_or_train_model; load_or_train_model(force_retrain=True)"
```

## Detailed Skills & References
Comprehensive architectural and mathematical guides are available under `.agents/skills/pricing-guardrail-engine/` and `skills/pricing-guardrail-engine/`. Read `SKILL.md` for full context.
