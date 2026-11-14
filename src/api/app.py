"""
FastAPI REST API & Enterprise Web Service for Pricing Guardrail Engine.

Provides decoupled REST endpoints and interactive UI/UX serving:
- Health & readiness probes
- Real-time KPI metrics & status distributions
- Telemetry event query & filtering
- 7-Day ML predictive floor breach radar
- Real-time simulation sandbox endpoint
- Security compliance & database immutability status
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from src.analytics.dashboard import get_full_dashboard_data, render_html_dashboard
from src.analytics.security_audit import run_security_compliance_audit
from src.connectors import default_registry, DirectSQLConnector, DirectRESTConnector, SchemaMapping
from src.engine.calculator import CostBreakdown
from src.engine.guardrail import PriceEvaluationInput, VRPGuardrailEngine
from src.engine.predictive import predict_sku_risk, load_or_train_model
from src.ingestion.db import test_connection

# Initialize FastAPI app
app = FastAPI(
    title="Autonomous Marketplace Pricing Guardrail & Margin Defense API",
    description="Enterprise API providing real-time pricing guardrails, sub-millisecond invariant checks, and ML breach forecasts.",
    version="1.3.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Enable CORS for decoupled frontends with secure origin management
cors_origins_env = os.getenv("CORS_ORIGINS", "*")
configured_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
# When wildcard origin is used, credentials must be disabled per CORS specification
allow_credentials = False if configured_origins == ["*"] else True

app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_origins,
    allow_credentials=allow_credentials,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ============================================================================
# Pydantic Schemas
# ============================================================================

class SimulationRequest(BaseModel):
    sku: Optional[str] = Field(default="SIM-SKU-001", description="SKU identifier")
    category: Optional[str] = Field(default="Electronics", description="Product category")
    cogs: float = Field(..., ge=0.01, description="Cost of Goods Sold per unit ($)")
    packaging_cost: float = Field(default=0.0, ge=0.0, description="Packaging cost ($)")
    fulfillment_cost: float = Field(default=0.0, ge=0.0, description="Fulfillment handling cost ($)")
    shipping_cost: float = Field(..., ge=0.0, description="Carrier shipping fee ($)")
    referral_rate_pct: float = Field(default=15.0, ge=0.0, le=50.0, description="Marketplace referral commission (%)")
    min_margin_amount: float = Field(..., ge=0.0, description="Required minimum target dollar margin ($)")
    proposed_price: float = Field(..., ge=0.01, description="Proposed selling price ($)")
    vrp_price: Optional[float] = Field(default=None, description="Amazon Validated Reference Price ($)")
    buybox_price: Optional[float] = Field(default=None, description="Current Buy Box price ($)")
    lowest_competitor_price: Optional[float] = Field(default=None, description="Lowest external competitor price ($)")


class SimulationResponse(BaseModel):
    floor_price: float
    proposed_price: float
    net_margin: float
    margin_pct: float
    floor_buffer: float
    verdict: str
    action: str
    explanation: str
    is_safe: bool
    evaluation_latency_ms: float


class HealthResponse(BaseModel):
    status: str
    version: str
    database_connected: bool
    timestamp: str


class ConnectorRegistrationRequest(BaseModel):
    tenant_id: str = Field(..., description="Unique client identifier, e.g. company_acme")
    name: str = Field(..., description="Company or store display name")
    connector_type: str = Field("sql", description="'sql' or 'rest'")
    connection_url: Optional[str] = Field(None, description="Database URL (postgresql://...) or REST API endpoint")
    telemetry_table: Optional[str] = Field("pricing_feed", description="Table name for SQL connector")
    auth_header: Optional[str] = Field(None, description="Authorization header for REST connector")
    callback_webhook: Optional[str] = Field(None, description="Webhook URL for pushing guardrail decisions")
    field_mappings: Optional[Dict[str, str]] = Field(None, description="Mapping from client column names to standard fields")


class LiveEvaluationRequest(BaseModel):
    tenant_id: str = Field(..., description="Tenant ID of the registered connector")
    limit: int = Field(50, ge=1, le=500, description="Number of live items to stream and evaluate")
    push_decisions: bool = Field(False, description="Whether to dispatch verdicts to the client callback")


# ============================================================================
# Core API Endpoints
# ============================================================================

@app.get("/health", response_model=HealthResponse, tags=["System"])
def health_check() -> HealthResponse:
    """Readiness and health probe."""
    db_ok = test_connection()
    return HealthResponse(
        status="healthy" if db_ok else "degraded",
        version="1.3.0",
        database_connected=db_ok,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/v1/kpis", tags=["Analytics"])
def get_kpis() -> Dict[str, Any]:
    """Retrieve executive KPI counters and margin defense metrics."""
    data = get_full_dashboard_data()
    return {
        "kpis": data.get("kpis", {}),
        "status_distribution": data.get("status_distribution", {}),
        "generated_at": data.get("generated_at"),
    }


@app.get("/api/v1/dataset", tags=["Analytics"])
def get_complete_dataset() -> Dict[str, Any]:
    """Retrieve full structured analytics dataset for UI consumption."""
    return get_full_dashboard_data()


@app.get("/api/v1/events", tags=["Telemetry"])
def list_events(
    status: Optional[str] = Query(None, description="Filter by guardrail_status (healthy, warning, breach, suppressed)"),
    category: Optional[str] = Query(None, description="Filter by category"),
    search: Optional[str] = Query(None, description="Search SKU or product name"),
    limit: int = Query(50, ge=1, le=250),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """List and filter catalog events."""
    data = get_full_dashboard_data()
    rows = data.get("sku_rows", [])

    if status:
        rows = [r for r in rows if str(r.get("guardrail_status", "")).lower() == status.lower()]
    if category:
        rows = [r for r in rows if str(r.get("category", "")).lower() == category.lower()]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in str(r.get("sku", "")).lower() or s in str(r.get("name", "")).lower()]

    total = len(rows)
    paginated = rows[offset : offset + limit]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": paginated,
    }


@app.get("/api/v1/memos", tags=["Diagnostics"])
def get_diagnostic_memos() -> List[Dict[str, Any]]:
    """Retrieve automated executive audit memos."""
    data = get_full_dashboard_data()
    return data.get("diagnostic_memos", [])


@app.get("/api/v1/forecast", tags=["Machine Learning"])
def get_breach_forecast() -> List[Dict[str, Any]]:
    """Retrieve 7-day predictive floor breach risk radar for top catalog SKUs."""
    data = get_full_dashboard_data()
    rows = data.get("sku_rows", [])
    forecasts = []
    seen = set()

    for r in rows:
        sku = r.get("sku")
        if not sku or sku in seen:
            continue
        seen.add(sku)
        pred = r.get("predicted_risk")
        if pred:
            forecasts.append(pred)

    return sorted(forecasts, key=lambda x: x.get("breach_probability_7d", 0.0), reverse=True)


@app.get("/api/v1/model-evaluation", tags=["Machine Learning"])
def get_model_evaluation_metrics() -> Dict[str, Any]:
    """Retrieve machine learning model quality report, ROC-AUC, 80/20 train/test split, and confusion matrix."""
    report_path = os.path.join("docs", "models", "model_evaluation_report.json")
    if os.path.exists(report_path):
        import json
        with open(report_path, "r", encoding="utf-8") as f:
            return json.load(f)
    load_or_train_model()
    if os.path.exists(report_path):
        import json
        with open(report_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"status": "trained", "model_type": "GradientBoostingClassifier"}


@app.post("/api/v1/simulate", response_model=SimulationResponse, tags=["Guardrail Engine"])
def simulate_guardrail(req: SimulationRequest) -> SimulationResponse:
    """Simulate real-time guardrail evaluation and sub-millisecond invariant enforcement."""
    import time

    t0 = time.perf_counter()

    cogs_dec = Decimal(str(req.cogs))
    pkg_dec = Decimal(str(req.packaging_cost))
    ful_dec = Decimal(str(req.fulfillment_cost))
    ship_dec = Decimal(str(req.shipping_cost))
    min_margin_dec = Decimal(str(req.min_margin_amount))
    proposed_dec = Decimal(str(req.proposed_price))
    referral_rate_dec = Decimal(str(req.referral_rate_pct)) / Decimal("100.0")

    # Accurate Floor Price Formula
    # Floor = (COGS + Packaging + Fulfillment + Shipping + MinMargin) / (1 - ReferralRate)
    cb = CostBreakdown(
        cogs_per_unit=cogs_dec,
        packaging_cost=pkg_dec,
        fulfillment_cost=ful_dec,
        shipping_cost=ship_dec,
        referral_fee=proposed_dec * referral_rate_dec,
        min_margin_amount=min_margin_dec,
    )
    floor_price = cb.floor_price()

    # VRP Guardrail Evaluation
    engine = VRPGuardrailEngine()
    inp = PriceEvaluationInput(
        product_id=req.sku or "SIM-PROD",
        sku=req.sku or "SIM-SKU",
        asin="B0SIMULATOR",
        category=req.category or "General",
        our_current_price=proposed_dec,
        proposed_price=proposed_dec,
        cogs_per_unit=cogs_dec,
        shipping_cost=ship_dec,
        vrp_price=Decimal(str(req.vrp_price)) if req.vrp_price is not None else None,
        buybox_price=Decimal(str(req.buybox_price)) if req.buybox_price is not None else None,
        lowest_competitor_price=Decimal(str(req.lowest_competitor_price)) if req.lowest_competitor_price is not None else None,
    )
    result = engine.evaluate(inp)

    total_time = (time.perf_counter() - t0) * 1000.0

    referral_amt = proposed_dec * referral_rate_dec
    total_cost = cogs_dec + pkg_dec + ful_dec + ship_dec + referral_amt
    net_margin = proposed_dec - total_cost
    margin_pct = (net_margin / proposed_dec) * Decimal("100.0") if proposed_dec > 0 else Decimal("0.0")
    buffer = proposed_dec - floor_price

    is_safe = proposed_dec >= floor_price
    if not is_safe:
        verdict = "BLOCKED (FLOOR BREACH)"
        action = "HALT_REPRICING"
        explanation = f"Proposed price ${proposed_dec:.2f} breaches cost floor ${floor_price:.2f}. Engine will block execution to defend margin."
    elif buffer < Decimal("2.00"):
        verdict = "WARNING (TIGHT BUFFER)"
        action = "MONITOR_PRICE_VELOCITY"
        explanation = f"Price is safe but within ${buffer:.2f} of floor. Downward competitor cuts risk an imminent breach."
    else:
        verdict = "APPROVED"
        action = result.recommended_action.value if hasattr(result.recommended_action, "value") else str(result.recommended_action)
        explanation = f"Price approved with healthy ${buffer:.2f} safety buffer above floor price."

    return SimulationResponse(
        floor_price=round(float(floor_price), 2),
        proposed_price=round(float(proposed_dec), 2),
        net_margin=round(float(net_margin), 2),
        margin_pct=round(float(margin_pct), 2),
        floor_buffer=round(float(buffer), 2),
        verdict=verdict,
        action=action,
        explanation=explanation,
        is_safe=is_safe,
        evaluation_latency_ms=round(total_time, 4),
    )


@app.get("/api/v1/security-audit", tags=["Security & Compliance"])
def get_security_audit_report() -> Dict[str, Any]:
    """Retrieve cryptographic database tamper-proofing and compliance audit status."""
    return run_security_compliance_audit().to_dict()


# ============================================================================
# Zero-ETL Federated Company Data Connectors
# ============================================================================

@app.post("/api/v1/connectors/register", tags=["Federated Connectors"])
def register_external_connector(req: ConnectorRegistrationRequest) -> Dict[str, Any]:
    """Register an external company direct-connect data source (SQL or REST API)."""
    mapping = SchemaMapping(field_map=req.field_mappings) if req.field_mappings else SchemaMapping()

    if req.connector_type.lower() == "sql":
        if not req.connection_url:
            raise HTTPException(status_code=400, detail="connection_url required for SQL connector")
        connector = DirectSQLConnector(
            tenant_id=req.tenant_id,
            name=req.name,
            connection_url=req.connection_url,
            telemetry_table=req.telemetry_table or "pricing_feed",
            schema_mapping=mapping,
            callback_webhook=req.callback_webhook,
        )
    elif req.connector_type.lower() == "rest":
        if not req.connection_url:
            raise HTTPException(status_code=400, detail="connection_url (feed_url) required for REST connector")
        connector = DirectRESTConnector(
            tenant_id=req.tenant_id,
            name=req.name,
            feed_url=req.connection_url,
            auth_header=req.auth_header,
            callback_webhook=req.callback_webhook,
            schema_mapping=mapping,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported connector type: {req.connector_type}")

    default_registry.register_connector(connector)
    return {
        "status": "registered",
        "tenant_id": req.tenant_id,
        "name": req.name,
        "connector_type": req.connector_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/connectors", tags=["Federated Connectors"])
def list_external_connectors() -> List[Dict[str, Any]]:
    """List all registered external company connectors."""
    return default_registry.list_connectors()


@app.post("/api/v1/connectors/test", tags=["Federated Connectors"])
def test_external_connector(tenant_id: str = Query(..., description="Tenant ID to test")) -> Dict[str, Any]:
    """Test read access and connectivity to an external company data source."""
    connector = default_registry.get_connector(tenant_id)
    if not connector:
        raise HTTPException(status_code=404, detail=f"No connector found for tenant '{tenant_id}'")
    success, message = connector.test_connection()
    return {
        "tenant_id": tenant_id,
        "connected": success,
        "message": message,
        "tested_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/v1/connectors/evaluate-live", tags=["Federated Connectors"])
def evaluate_live_external_data(req: LiveEvaluationRequest) -> Dict[str, Any]:
    """
    Run sub-millisecond pricing guardrail evaluation directly against external client data.
    
    Zero-ETL: Reads live pricing events in-memory, enforces floor invariants, and returns
    or pushes approval/blocking verdicts without saving data to internal tables.
    """
    try:
        return default_registry.evaluate_live_feed(
            tenant_id=req.tenant_id,
            limit=req.limit,
            push_decisions=req.push_decisions,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tenant '{req.tenant_id}' not found in connector registry")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Live evaluation failed: {str(exc)}")


# ============================================================================
# Modern UI Root Route
# ============================================================================

@app.get("/", response_class=HTMLResponse, tags=["User Interface"])
def index() -> str:
    """Serve the modernized executive dashboard."""
    data = get_full_dashboard_data()
    return render_html_dashboard(data)


def main():
    """Run API server via CLI."""
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting Pricing Guardrail Web Service on http://{host}:{port}")
    uvicorn.run("src.api.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
