# Pricing Guardrail Engine

Autonomous marketplace pricing guardrail & margin defense engine for multi-channel sellers.

## Architecture

- **Data Ingestion**: COGS, shipping, fees, VRP data collection
- **Pricing Engine**: Floor calculation, elasticity modeling, VRP protection
- **Decision & Execution**: Auto-adjustment, channel integrations
- **Dashboard**: Executive metrics, trends, alerts, memos

## Tech Stack

- Python (orchestration)
- PostgreSQL (database)
- scikit-learn / PyTorch (ML models)
- cron (scheduling)
- SMTP / Slack (notifications)
- HTML + JavaScript / Chart.js (dashboard)

## Project Structure

```
├── config/              # Configuration files
├── db/migrations/       # Database migration scripts
├── src/
│   ├── engine/          # Core pricing engine
│   ├── ingestion/       # Data ingestion modules
│   └── telemetry/       # Synthetic telemetry simulator
├── tests/               # Unit and integration tests
└── docs/                # Documentation
```

## Getting Started

1. Copy `.env.example` to `.env` and fill in your credentials
2. Install dependencies: `pip install -r requirements.txt`
3. Run database migrations: `python -m db.migrations.run`
4. Start the engine: `python -m src.engine.main`

## Phases

- **Phase 1** (Weeks 1-3): Foundation & Floor Price Engine
- **Phase 2** (Weeks 4-6): VRP Protection & Elasticity
- **Phase 3** (Weeks 7-9): Automated Decision & Execution
- **Phase 4** (Weeks 10-12): Executive Dashboard & Memos
- **Phase 5** (Weeks 13-14): Production Hardening
- **Phase 6** (Week 15+): Go-Live & Monitoring