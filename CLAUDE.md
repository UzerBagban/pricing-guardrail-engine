# Claude Code Project Guide: Pricing Guardrail Engine

This document provides instructions for Claude Code when working in the `pricing-guardrail-engine` repository.

## Project Summary
Enterprise automated pricing governance and real-time margin defense engine for multi-channel e-commerce sellers (Amazon, Walmart, Shopify, Target+).
- Invariant: `new_price >= floor_price`.
- Core formula: `Floor = [ COGS + Packaging + Fulfillment + Shipping + Target$ ] / (1 - ReferralRate%)`.
- Precision: All pricing/cost math **must strictly use `decimal.Decimal`**. Never use `float`.
- Database: PostgreSQL with append-only immutable audit logs enforced by triggers (`trg_protect_*`). Zero UPDATE/DELETE on audit tables.
- Zero-ETL Connectors: Connects directly to external client SQL (PostgreSQL, MySQL, SQLite) and REST/ERP APIs, evaluating in-memory (`< 0.15 ms/SKU`) without data migration.

## Essential Commands

### Environment & Run
- Start API Server: `python -m src.api.app` (FastAPI at `http://127.0.0.1:8000`, docs at `/docs`)
- Run Autonomous Daemon: `python -m src.engine.main`
- Retrain ML Radar: `python -c "from src.engine.predictive import load_or_train_model; load_or_train_model(force_retrain=True)"`
- Ingest 50,000+ Events: `python scripts/populate_large_dataset.py`

### Testing (119 Pytest Tests, 100% Pass Rate)
- Run all tests: `python -m pytest tests/ -q`
- Run core engine tests: `python -m pytest tests/test_engine.py -v`
- Run connector tests: `python -m pytest tests/test_connectors.py -v`
- Run API tests: `python -m pytest tests/test_api.py -v`
- Run ML tests: `python -m pytest tests/test_predictive.py -v`
- Run security/immutability tests: `python -m pytest tests/test_security_audit.py -v`

## Codebase Architecture
- `src/engine/calculator.py`: Break-even floor price calculator (`CostBreakdown`, `FloorCalculator`).
- `src/engine/guardrail.py`: Sub-millisecond guardrail evaluator (`APPROVED`, `WARNING`, `BLOCKED`, `SUPPRESSED`).
- `src/connectors/`: Zero-ETL direct SQL & REST client data connectors and schema mapping.
- `src/engine/predictive.py`: 7-day ML breach risk radar (`GradientBoostingClassifier`).
- `src/engine/adjuster.py`: Price clamping and repricing rules.
- `src/engine/suppression.py`: Deal suppression queue with exponential backoff.
- `src/ingestion/db.py`: Thread-safe PostgreSQL connection pool (`ThreadedConnectionPool`).
- `src/api/app.py`: Enterprise FastAPI REST service.

## Critical Development Guidelines
1. **Never use standard floats for money:** Always `from decimal import Decimal, ROUND_HALF_UP`.
2. **Never format raw SQL queries:** Always use `%s` parameterized tuples via `psycopg2`.
3. **Never attempt UPDATE or DELETE on audit tables:** `price_change_log` and `telemetry_raw` are trigger-protected.
4. **Always return DB connections:** Always use `try...finally: release_db_connection(conn)`.
5. For deep technical references, consult the skill at `.agents/skills/pricing-guardrail-engine/SKILL.md`.
