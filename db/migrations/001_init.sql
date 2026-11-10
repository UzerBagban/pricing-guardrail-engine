-- Migration 001: Core Tables
-- Creates: products, product_costs, shipping_profiles, pricing_rules
-- Run order: 1st

-- Products table: core product catalog
CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(500) NOT NULL,
    category VARCHAR(100),
    brand VARCHAR(100),
    asin VARCHAR(20), -- Amazon Standard Identification Number
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);
CREATE INDEX IF NOT EXISTS idx_products_asin ON products(asin);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_active ON products(is_active);

-- Product Costs table: COGS breakdown per product
CREATE TABLE IF NOT EXISTS product_costs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    cogs_per_unit DECIMAL(12,4) NOT NULL CHECK (cogs_per_unit >= 0),
    packaging_cost DECIMAL(12,4) DEFAULT 0 CHECK (packaging_cost >= 0),
    fulfillment_cost DECIMAL(12,4) DEFAULT 0 CHECK (fulfillment_cost >= 0),
    effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
    effective_to DATE,
    source VARCHAR(50) DEFAULT 'manual', -- 'erp', 'manual', 'calculated'
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT chk_effective_dates CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

CREATE INDEX IF NOT EXISTS idx_product_costs_product_id ON product_costs(product_id);
CREATE INDEX IF NOT EXISTS idx_product_costs_effective ON product_costs(effective_from, effective_to);

-- Shipping Profiles: carrier rates per product/region
CREATE TABLE IF NOT EXISTS shipping_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    carrier VARCHAR(50) NOT NULL, -- 'ups', 'fedex', 'dhl', 'usps', 'custom'
    service_level VARCHAR(50), -- 'ground', 'express', 'overnight'
    region VARCHAR(100), -- 'US', 'EU', 'APAC', 'domestic', 'international'
    weight_min_kg DECIMAL(8,3) DEFAULT 0,
    weight_max_kg DECIMAL(8,3),
    base_rate DECIMAL(10,4) NOT NULL CHECK (base_rate >= 0),
    per_kg_rate DECIMAL(10,4) DEFAULT 0 CHECK (per_kg_rate >= 0),
    min_charge DECIMAL(10,4) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shipping_profiles_carrier ON shipping_profiles(carrier);
CREATE INDEX IF NOT EXISTS idx_shipping_profiles_region ON shipping_profiles(region);
CREATE INDEX IF NOT EXISTS idx_shipping_profiles_active ON shipping_profiles(is_active);

-- Pricing Rules: guardrail configurations per category/product
CREATE TABLE IF NOT EXISTS pricing_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    scope_type VARCHAR(20) NOT NULL CHECK (scope_type IN ('global', 'category', 'product')),
    scope_value VARCHAR(100), -- category name or product SKU
    min_margin_pct DECIMAL(5,2) NOT NULL CHECK (min_margin_pct >= 0 AND min_margin_pct <= 100),
    max_premium_pct DECIMAL(5,2) CHECK (max_premium_pct IS NULL OR max_premium_pct >= 0),
    vrp_drift_threshold_pct DECIMAL(5,2) DEFAULT 5.0 CHECK (vrp_drift_threshold_pct >= 0),
    elasticity_threshold DECIMAL(6,3) DEFAULT 2.5 CHECK (elasticity_threshold >= 0),
    suppression_enabled BOOLEAN DEFAULT TRUE,
    auto_adjust_enabled BOOLEAN DEFAULT FALSE,
    priority INT DEFAULT 100, -- lower = higher priority
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pricing_rules_scope ON pricing_rules(scope_type, scope_value);
CREATE INDEX IF NOT EXISTS idx_pricing_rules_active ON pricing_rules(is_active);
CREATE INDEX IF NOT EXISTS idx_pricing_rules_priority ON pricing_rules(priority);

-- Updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply updated_at triggers
CREATE TRIGGER update_products_updated_at BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_product_costs_updated_at BEFORE UPDATE ON product_costs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_shipping_profiles_updated_at BEFORE UPDATE ON shipping_profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_pricing_rules_updated_at BEFORE UPDATE ON pricing_rules
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();