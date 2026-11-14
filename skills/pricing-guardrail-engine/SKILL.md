---
name: pricing-guardrail-engine
description: >-
  Expert engineering and operational guide for the Autonomous Marketplace Pricing Guardrail & Margin Defense Engine.
  Use this skill whenever an AI agent (Claude, Codex, Gemini, Antigravity) needs to understand project architecture,
  run setup and migrations, execute tests, evaluate pricing guardrails, debug connectors, or modify engine algorithms.
---

# Autonomous Marketplace Pricing Guardrail & Margin Defense Engine

Welcome to the **Pricing Guardrail Engine** codebase. This skill equips any AI coding agent (Claude Code, OpenAI Codex, Google Gemini, Antigravity, Cursor) with full technical context, architectural constraints, setup runbooks, and coding standards for this repository.

---

## Quick Orientation: What This Project Does

This system is an enterprise-grade automated pricing governance and real-time margin defense engine for multi-channel e-commerce sellers (Amazon, Walmart, Shopify, Target+).

* **The Core Mathematical Invariant:**
  $$\text{new\_price} \ge \text{floor\_price}$$
  No selling price is ever allowed to fall below the dynamic landed cost plus minimum required margin.
* **Key Capabilities:**
  1. **Dynamic Cost Floor Calculation (`src/engine/calculator.py`):** Calculates exact break-even floor price using arbitrary-precision `Decimal` arithmetic.
  2. **Sub-Millisecond Guardrail Evaluation (`src/engine/guardrail.py`):** Evaluates inbound price proposals in `< 0.15 ms` into deterministic states (`APPROVED`, `WARNING`, `BLOCKED`, `SUPPRESSED`).
  3. **Zero-ETL Federated Connectors (`src/connectors/`):** Connects directly to external client databases (PostgreSQL, MySQL, SQLite) and REST/ERP APIs, evaluating guardrails in-memory without data migration or local DB writes.
  4. **7-Day Predictive Breach Radar (`src/engine/predictive.py`):** `GradientBoostingClassifier` trained on 52,750+ telemetry events predicting 7-day floor breach risks with explainable attribution.
  5. **Cryptographic DB Immutability (`src/analytics/security_audit.py`, `db/migrations/`):** Trigger-enforced append-only audit tables rejecting `UPDATE` and `DELETE` at the PostgreSQL kernel level.
  6. **Decoupled REST API & Executive UI (`src/api/app.py`, `dashboard.html`):** FastAPI service serving analytics, simulation sandbox, and KPI metrics.

---

## Directory Structure & Code Map

```
pricing-guardrail-engine/
├── .agents/
│   └── skills/
│       └── pricing-guardrail-engine/  # This skill directory
│           ├── SKILL.md               # Main AI onboarding guide
│           └── references/            # Deep dive architectural references
│               ├── architecture.md
│               ├── cost_floor_math.md
│               ├── zero_etl_connectors.md
│               ├── database_and_immutability.md
│               ├── machine_learning_radar.md
│               └── testing_and_benchmarks.md
├── src/
│   ├── api/
│   │   └── app.py                     # FastAPI REST service & lifecycle management
│   ├── connectors/                    # Zero-ETL direct SQL & REST company data connectors
│   │   ├── base.py                    # Base connector, NormalizedTelemetry & SchemaMapping
│   │   ├── manager.py                 # Federated connector registry & live in-memory evaluator
│   │   ├── rest_connector.py          # Direct client REST/ERP API connector
│   │   └── sql_connector.py           # Direct client SQL (PostgreSQL, SQLite, MySQL) connector
│   ├── engine/
│   │   ├── adjuster.py                # Price adjustment rules & floor clamping
│   │   ├── calculator.py              # Decimal break-even floor price calculator
│   │   ├── guardrail.py               # Core sub-millisecond invariant evaluator
│   │   ├── main.py                    # Autonomous daemon runner
│   │   ├── orchestrator.py            # Batch pipeline orchestrator
│   │   ├── predictive.py              # ML 7-day breach risk forecaster
│   │   └── suppression.py             # Deal suppression queue & recovery
│   ├── ingestion/
│   │   ├── db.py                      # Thread-safe PostgreSQL connection pool & queries
│   │   ├── evaluator.py               # Telemetry stream evaluator
│   │   ├── notifier.py                # Webhook, Slack & SMTP alert dispatcher
│   │   └── validator.py               # Strict schema & type input validation
│   ├── analytics/
│   │   ├── benchmark.py               # Latency & concurrency benchmarking engine
│   │   ├── dashboard.py               # Static dashboard data compiler
│   │   ├── memo_generator.py          # Strategic executive markdown & HTML briefing memos
│   │   └── security_audit.py          # Immutability verification & compliance engine
│   └── telemetry/
│       └── simulator.py               # Multi-scenario market telemetry generator (50,000+ events)
├── tests/                             # 119 pytest automated tests (100% pass rate)
│   ├── test_engine.py                 # Core math & guardrail tests
│   ├── test_connectors.py             # Zero-ETL connectors & schema mapping tests
│   ├── test_api.py                    # FastAPI REST endpoint tests
│   ├── test_predictive.py             # ML model training & inference tests
│   ├── test_security_audit.py         # Immutability triggers & SQL injection tests
│   ├── test_performance_benchmarks.py # Concurrency & latency SLA verification
│   └── test_playwright_ui.py          # End-to-end browser UI tests
├── db/
│   ├── migrations/                    # SQL schema definitions and trigger scripts
│   └── seeds/                         # Initial reference catalog seeds
├── docs/                              # Executive memos, model graphs, evaluation reports
├── dashboard.html                     # Responsive dark-mode executive UI
├── PLAN.md                            # Comprehensive engineering specification
└── README.md                          # Public GitHub documentation
```

---

## Critical Rules for AI Agents

1. **Floating-Point Arithmetic Prohibited in Pricing Math:**
   - **Never** use Python standard `float` for currency, pricing, fees, margins, or cost calculations.
   - **Always** use Python's arbitrary-precision `decimal.Decimal` with explicit rounding:
     ```python
     from decimal import Decimal, ROUND_HALF_UP
     result = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
     ```
2. **Database Immutability Invariant:**
   - Audit tables (`price_change_log`, `telemetry_raw`) are protected by PostgreSQL triggers (`trg_protect_*`).
   - **Never** attempt to execute `UPDATE` or `DELETE` queries on these tables.
   - Any query to modify these records will abort the transaction with PostgreSQL error `42501` (`insufficient_privilege`).
3. **Zero SQL Injection Policy:**
   - **Never** construct SQL queries using f-strings, `%` string interpolation, or `.format()`.
   - **Always** use `%s` parameterized queries passed as a tuple/list to `cursor.execute(sql, (param1, param2))`.
4. **Thread-Safe Database Pooling:**
   - Always acquire database connections from `src.ingestion.db.get_db_connection()` within a `try...finally` block to return connections back to the pool via `release_db_connection(conn)`.
5. **Preserve Invariant In All New Features:**
   - Any repricing decision must satisfy `proposed_price >= floor_price`. If violated, it must be either clamped to the floor or blocked.

---

## Environment & Setup Runbook

### 1. Python Environment
- Python version: 3.11+ (Python 3.14 compatible).
- Install dependencies:
  ```bash
  pip install -r requirements.txt
  ```

### 2. Environment Variables (`.env`)
Copy `.env.example` to `.env`:
```ini
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/pricing_guardrail
HOST=127.0.0.1
PORT=8000
DB_POOL_MIN=2
DB_POOL_MAX=20
```

### 3. Verify Database Connection
```bash
python -c "from src.ingestion.db import get_connection; conn = get_connection(); print('DB Ready:', bool(conn))"
```

---

## Common Development Commands

### Running the Web Server & API
```bash
python -m src.api.app
# Server runs at http://127.0.0.1:8000
# OpenAPI Docs at http://127.0.0.1:8000/docs
```

### Running the Autonomous Orchestrator Daemon
```bash
python -m src.engine.main
```

### Populating the 50,000+ Event Telemetry Dataset
```bash
python scripts/populate_large_dataset.py
```

### Retraining the 7-Day ML Breach Predictor
```bash
python -c "from src.engine.predictive import load_or_train_model; load_or_train_model(force_retrain=True)"
```

### Running the Test Suite (119 Tests)
```bash
# Run all tests:
python -m pytest tests/ -q

# Run fast targeted test suites:
python -m pytest tests/test_engine.py -v       # Engine pricing & guardrails
python -m pytest tests/test_connectors.py -v   # Zero-ETL connectors
python -m pytest tests/test_api.py -v          # FastAPI REST endpoints
python -m pytest tests/test_predictive.py -v   # Machine learning tests
python -m pytest tests/test_security_audit.py -v # Immutability & SQL injection
```

---

## Reference Guides in this Skill

For in-depth explanations, read the linked markdown references in the `references/` directory:
- [System Architecture & Pipelines](./references/architecture.md)
- [Exact Cost Floor Mathematics](./references/cost_floor_math.md)
- [Zero-ETL Federated Connectors](./references/zero_etl_connectors.md)
- [Database Schema & Cryptographic Immutability](./references/database_and_immutability.md)
- [7-Day ML Breach Risk Radar](./references/machine_learning_radar.md)
- [Testing SLAs, Benchmarks & Concurrency](./references/testing_and_benchmarks.md)
