-- Migration 002: Amazon VRP Tables
-- Creates: vrp_snapshots, vrp_drift_events, suppression_queue
-- Run order: 2nd (after 001_init.sql)

-- VRP Snapshots: historical Amazon Validated Reference Price data
CREATE TABLE IF NOT EXISTS vrp_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    asin VARCHAR(20) NOT NULL,
    marketplace_id VARCHAR(20) DEFAULT 'ATVPDKIKX0DER', -- US marketplace
    vrp_price DECIMAL(12,4) NOT NULL CHECK (vrp_price >= 0),
    current_buybox_price DECIMAL(12,4),
    our_current_price DECIMAL(12,4),
    price_delta_pct DECIMAL(8,4), -- (vrp - our_price) / our_price * 100
    competitor_count INT DEFAULT 0,
    lowest_competitor_price DECIMAL(12,4),
    data_source VARCHAR(30) DEFAULT 'scraper', -- 'scraper', 'api', 'manual'
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vrp_snapshots_product_id ON vrp_snapshots(product_id);
CREATE INDEX IF NOT EXISTS idx_vrp_snapshots_asin ON vrp_snapshots(asin);
CREATE INDEX IF NOT EXISTS idx_vrp_snapshots_scraped_at ON vrp_snapshots(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_vrp_snapshots_price_delta ON vrp_snapshots(price_delta_pct);

-- VRP Drift Events: detected significant price movements
CREATE TABLE IF NOT EXISTS vrp_drift_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    asin VARCHAR(20) NOT NULL,
    drift_type VARCHAR(30) NOT NULL CHECK (drift_type IN ('vrp_drop', 'vrp_spike', 'buybox_loss', 'margin_breach')),
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    previous_vrp DECIMAL(12,4),
    current_vrp DECIMAL(12,4),
    drift_pct DECIMAL(8,4) NOT NULL,
    threshold_pct DECIMAL(5,2) NOT NULL,
    our_price_at_event DECIMAL(12,4),
    floor_price_at_event DECIMAL(12,4),
    recommended_action VARCHAR(50), -- 'hold', 'increase_price', 'decrease_price', 'investigate', 'suppress'
    status VARCHAR(20) DEFAULT 'open' CHECK (status IN ('open', 'acknowledged', 'resolved', 'suppressed')),
    acknowledged_by UUID, -- user ID
    acknowledged_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vrp_drift_events_product_id ON vrp_drift_events(product_id);
CREATE INDEX IF NOT EXISTS idx_vrp_drift_events_type ON vrp_drift_events(drift_type);
CREATE INDEX IF NOT EXISTS idx_vrp_drift_events_severity ON vrp_drift_events(severity);
CREATE INDEX IF NOT EXISTS idx_vrp_drift_events_status ON vrp_drift_events(status);
CREATE INDEX IF NOT EXISTS idx_vrp_drift_events_created_at ON vrp_drift_events(created_at DESC);

-- Suppression Queue: deals/prices suppressed due to guardrail violations
CREATE TABLE IF NOT EXISTS suppression_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    asin VARCHAR(20) NOT NULL,
    channel VARCHAR(30) NOT NULL CHECK (channel IN ('amazon', 'ebay', 'walmart', 'shopify', 'all')),
    suppression_reason VARCHAR(50) NOT NULL CHECK (suppression_reason IN (
        'below_floor_price',
        'vrp_violation',
        'margin_breach',
        'elasticity_breach',
        'manual_override'
    )),
    target_price DECIMAL(12,4) NOT NULL,
    floor_price DECIMAL(12,4) NOT NULL,
    vrp_price DECIMAL(12,4),
    margin_pct DECIMAL(6,3),
    elasticity_score DECIMAL(6,3),
    severity VARCHAR(20) DEFAULT 'medium' CHECK (severity IN ('low', 'medium', 'high', 'critical')),
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'suppressed', 'released', 'overridden')),
    suppressed_at TIMESTAMPTZ DEFAULT NOW(),
    released_at TIMESTAMPTZ,
    released_by UUID, -- user ID who overrode
    override_reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_suppression_queue_product_id ON suppression_queue(product_id);
CREATE INDEX IF NOT EXISTS idx_suppression_queue_status ON suppression_queue(status);
CREATE INDEX IF NOT EXISTS idx_suppression_queue_reason ON suppression_queue(suppression_reason);
CREATE INDEX IF NOT EXISTS idx_suppression_queue_channel ON suppression_queue(channel);
CREATE INDEX IF NOT EXISTS idx_suppression_queue_suppressed_at ON suppression_queue(suppressed_at DESC);

-- Updated_at triggers for VRP tables
CREATE TRIGGER update_vrp_snapshots_updated_at BEFORE UPDATE ON vrp_snapshots
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_vrp_drift_events_updated_at BEFORE UPDATE ON vrp_drift_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_suppression_queue_updated_at BEFORE UPDATE ON suppression_queue
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();