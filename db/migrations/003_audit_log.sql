-- Migration 003: Audit Log Tables
-- Creates: price_change_log, guardrail_overrides, system_events
-- Run order: 3rd (after 002_vrp_tables.sql)

-- Price Change Log: immutable record of all price changes
CREATE TABLE IF NOT EXISTS price_change_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    asin VARCHAR(20),
    channel VARCHAR(30) NOT NULL CHECK (channel IN ('amazon', 'ebay', 'walmart', 'shopify', 'internal')),
    change_type VARCHAR(30) NOT NULL CHECK (change_type IN (
        'floor_price_update',
        'vrp_adjustment',
        'elasticity_adjustment',
        'manual_override',
        'suppression_triggered',
        'suppression_released',
        'scheduled_reprice',
        'competitor_response'
    )),
    old_price DECIMAL(12,4),
    new_price DECIMAL(12,4) NOT NULL,
    floor_price DECIMAL(12,4),
    vrp_price DECIMAL(12,4),
    margin_pct_before DECIMAL(6,3),
    margin_pct_after DECIMAL(6,3),
    elasticity_score DECIMAL(6,3),
    trigger_event_id UUID, -- references vrp_drift_events.id or suppression_queue.id
    triggered_by VARCHAR(50), -- 'system', 'user:{id}', 'api:{source}'
    metadata JSONB DEFAULT '{}', -- flexible context data
    executed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Immutable: no UPDATE/DELETE allowed on price_change_log
CREATE INDEX IF NOT EXISTS idx_price_change_log_product_id ON price_change_log(product_id);
CREATE INDEX IF NOT EXISTS idx_price_change_log_channel ON price_change_log(channel);
CREATE INDEX IF NOT EXISTS idx_price_change_log_type ON price_change_log(change_type);
CREATE INDEX IF NOT EXISTS idx_price_change_log_executed_at ON price_change_log(executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_change_log_triggered_by ON price_change_log(triggered_by);

-- Guardrail Overrides: manual interventions on automated decisions
CREATE TABLE IF NOT EXISTS guardrail_overrides (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    asin VARCHAR(20),
    override_type VARCHAR(30) NOT NULL CHECK (override_type IN (
        'floor_price_override',
        'vrp_threshold_override',
        'suppression_release',
        'margin_override',
        'elasticity_override',
        'emergency_reprice'
    )),
    original_decision VARCHAR(50) NOT NULL, -- what the system would have done
    override_value DECIMAL(12,4), -- the manually set value
    original_value DECIMAL(12,4), -- what the system calculated
    justification TEXT NOT NULL,
    requested_by VARCHAR(100) NOT NULL, -- user identifier
    approved_by VARCHAR(100), -- approver (if required)
    expires_at TIMESTAMPTZ, -- temporary overrides
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    deactivated_at TIMESTAMPTZ,
    deactivated_by VARCHAR(100)
);

CREATE INDEX IF NOT EXISTS idx_guardrail_overrides_product_id ON guardrail_overrides(product_id);
CREATE INDEX IF NOT EXISTS idx_guardrail_overrides_type ON guardrail_overrides(override_type);
CREATE INDEX IF NOT EXISTS idx_guardrail_overrides_active ON guardrail_overrides(is_active);
CREATE INDEX IF NOT EXISTS idx_guardrail_overrides_created_at ON guardrail_overrides(created_at DESC);

-- System Events: operational events for debugging and compliance
CREATE TABLE IF NOT EXISTS system_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(50) NOT NULL CHECK (event_type IN (
        'migration_applied',
        'seed_data_loaded',
        'scraper_run_started',
        'scraper_run_completed',
        'scraper_run_failed',
        'model_training_started',
        'model_training_completed',
        'model_deployed',
        'alert_triggered',
        'notification_sent',
        'api_error',
        'config_changed',
        'maintenance_window'
    )),
    severity VARCHAR(20) DEFAULT 'info' CHECK (severity IN ('debug', 'info', 'warning', 'error', 'critical')),
    component VARCHAR(50), -- 'ingestion', 'engine', 'telemetry', 'dashboard', 'scheduler'
    message TEXT NOT NULL,
    details JSONB DEFAULT '{}',
    correlation_id UUID, -- for tracing related events
    user_id VARCHAR(100), -- if user-initiated
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_system_events_type ON system_events(event_type);
CREATE INDEX IF NOT EXISTS idx_system_events_severity ON system_events(severity);
CREATE INDEX IF NOT EXISTS idx_system_events_component ON system_events(component);
CREATE INDEX IF NOT EXISTS idx_system_events_created_at ON system_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_events_correlation ON system_events(correlation_id);

-- Function to prevent updates/deletes on audit tables
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit tables are immutable - updates and deletes are not allowed';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Apply immutability triggers
CREATE TRIGGER prevent_price_change_log_modification
    BEFORE UPDATE OR DELETE ON price_change_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();

CREATE TRIGGER prevent_guardrail_overrides_modification
    BEFORE UPDATE OR DELETE ON guardrail_overrides
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();

CREATE TRIGGER prevent_system_events_modification
    BEFORE UPDATE OR DELETE ON system_events
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification();

-- Grant read-only access to audit tables for reporting roles (adjust as needed)
-- GRANT SELECT ON price_change_log, guardrail_overrides, system_events TO reporting_role;