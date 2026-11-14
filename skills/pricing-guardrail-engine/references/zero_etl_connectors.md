# Zero-ETL Federated Company Connectors

## Why Zero-ETL Matters

Enterprise multi-channel brands maintain their own pricing, ERP, and catalog data on their own infrastructure. Requiring them to migrate or replicate their historical tables into our local database causes:
1. **Massive Disk Write Amplification:** Duplicating millions of rows stresses local I/O.
2. **Cloud Storage Overhead:** Multiplied storage costs for redundant data.
3. **Data Drift:** Lagging batch sync schedules lead to repricing decisions based on stale data.
4. **Security Friction:** Transferring and storing client catalog databases raises compliance hurdles.

The **Zero-ETL Live Connectors** solve this by connecting directly to the client's external database or REST feed on-demand, evaluating guardrails entirely in volatile memory (`< 0.15 ms/SKU`), and dispatching decisions back without writing duplicate records to local database tables.

---

## Connector Architecture Components

### 1. `BaseDataConnector` (`src/connectors/base.py`)
Abstract base class defining:
- `stream_live_telemetry(limit: Optional[int]) -> Iterator[NormalizedTelemetry]`
- `test_connection() -> bool`
- `dispatch_decision(decision: Dict[str, Any]) -> bool`

### 2. `NormalizedTelemetry` (`src/connectors/base.py`)
Canonical dataclass ensuring arbitrary external client schemas are coerced into exact `Decimal` engine types:
- `sku: str`
- `our_price: Decimal`
- `cogs: Decimal`
- `shipping_cost: Decimal`
- `packaging_cost: Decimal`
- `fulfillment_cost: Decimal`
- `referral_rate_pct: Decimal`
- `min_margin_amount: Decimal`
- `vrp_price: Optional[Decimal]`
- `buybox_price: Optional[Decimal]`
- `lowest_competitor_price: Optional[Decimal]`
- `category: str`

### 3. `SchemaMapping` (`src/connectors/base.py`)
Dynamic schema translation layer:
- `field_map`: Maps client field names to canonical names (e.g. `{"item_id": "sku", "cost": "cogs"}`).
- `value_transforms`: String formulas for unit scaling (e.g. `{"cost": "cogs / 100"}` to convert cents to dollars).

### 4. `DirectSQLConnector` (`src/connectors/sql_connector.py`)
- Executes streaming read-only queries against external databases (PostgreSQL, SQLite, MySQL).
- Yields normalized telemetry events one by one via generator to maintain minimal memory footprint.

### 5. `DirectRESTConnector` (`src/connectors/rest_connector.py`)
- Polls external client REST/ERP endpoints with Bearer tokens or API keys.
- Dispatches guardrail decisions to client webhook URLs.

### 6. `FederatedConnectorManager` (`src/connectors/manager.py`)
- Thread-safe connector registry (`connector_registry`).
- Evaluates inbound batches in memory, returning `FederatedEvaluationSummary` containing:
  - `total_evaluated`
  - `approved`, `warnings`, `blocked`
  - `total_defended_dollars`
  - `avg_latency_per_sku_ms`

---

## REST Endpoints for Connectors
- `POST /api/v1/connectors/register` - Register a client SQL or REST connector.
- `GET /api/v1/connectors` - List all registered client connectors.
- `POST /api/v1/connectors/test` - Test external connectivity.
- `POST /api/v1/connectors/evaluate-live` - Stream live client data and run in-memory evaluation.
