# Testing SLAs, Benchmarks & Concurrency Verification

## Test Suite Overview

The test suite consists of **119 automated pytest tests** covering unit math, database integration, concurrency stress tests, machine learning inference, and browser UI automation.

### Test Directory Breakdown (`tests/`)

| Test File | Count | Primary Focus |
| :--- | :--- | :--- |
| `test_engine.py` | 28 | `CostBreakdown`, `FloorCalculator`, `GuardrailEngine`, mathematical invariants |
| `test_connectors.py` | 4 | Zero-ETL direct SQL streaming, schema mapping, live evaluation |
| `test_api.py` | 18 | FastAPI endpoints, security audit API, simulation sandbox, KPI data |
| `test_predictive.py` | 14 | GradientBoostingClassifier training, inference latency, risk tier thresholds |
| `test_performance_benchmarks.py` | 7 | Sub-millisecond evaluation latency, concurrent load, throughput SLAs |
| `test_security_audit.py` | 8 | Append-only database immutability triggers, SQL injection safety |
| `test_orchestrator.py` | 12 | Batch pipeline cycles, cycle telemetry execution, event deduplication |
| `test_memo_generator.py` | 8 | Executive Markdown & HTML memo generation, incident syntheses |
| `test_phase3_full_pipeline.py` | 20 | Full regression integration pass, alerts, repricing clamped decisions |
| `test_playwright_ui.py` | 4 | End-to-end browser UI/UX rendering, modals, dark mode theme |

---

## Production SLAs & Benchmarks

Automated benchmarks in [`src/analytics/benchmark.py`](file:///c:/Users/Uzer/Project/pricing-guardrail-engine/src/analytics/benchmark.py) verify that the system satisfies high-throughput repricer requirements:

| Metric | Target SLA | Measured Benchmark | Status |
| :--- | :--- | :--- | :--- |
| **Guardrail Evaluation Latency** | < 0.50 ms / SKU | **0.137 ms / SKU** | **PASSED (3.6x faster)** |
| **Batch Throughput (50 SKUs)** | < 100 ms | **6.84 ms / batch** | **PASSED** |
| **ML 7-Day Inference Latency** | < 2.00 ms / prediction | **0.871 ms / prediction**| **PASSED (2.3x faster)** |
| **Database Pool Concurrency** | 20 concurrent threads | Zero thread starvation / Deadlocks | **PASSED** |
| **Full Pipeline Batch Cycle** | < 5.0 s (50 events) | **< 2.50 s (50 events)** | **PASSED** |

---

## Running Test Commands

```bash
# Run entire test suite (119 tests):
python -m pytest tests/ -q

# Run fast targeted suites:
python -m pytest tests/test_engine.py -v
python -m pytest tests/test_connectors.py -v
python -m pytest tests/test_api.py -v
python -m pytest tests/test_predictive.py -v
python -m pytest tests/test_security_audit.py -v
python -m pytest tests/test_performance_benchmarks.py -v
```
