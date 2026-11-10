-- Mock Seed Data for Pricing Guardrail Engine
-- Run after migrations 001, 002, 003
-- Provides realistic products, costs, shipping rates, and initial VRP data

-- ============================================================
-- PRODUCTS (10 sample products across categories)
-- ============================================================
INSERT INTO products (sku, name, category, brand, asin, is_active) VALUES
    ('ELEC-001', 'Wireless Bluetooth Headphones Pro', 'Electronics', 'AudioTech', 'B08N5WRWNW', TRUE),
    ('ELEC-002', 'USB-C Hub 7-in-1 Adapter', 'Electronics', 'ConnectPro', 'B09KJRN3HH', TRUE),
    ('HOME-001', 'Memory Foam Pillow Queen Set', 'Home & Kitchen', 'SleepWell', 'B07VV4KQZM', TRUE),
    ('HOME-002', 'Stainless Steel Water Bottle 32oz', 'Home & Kitchen', 'HydroFlow', 'B08XJ7K9L2', TRUE),
    ('SPRT-001', 'Resistance Bands Set 5-Level', 'Sports & Outdoors', 'FitGear', 'B07Z4JQJQJ', TRUE),
    ('SPRT-002', 'Yoga Mat Premium Non-Slip', 'Sports & Outdoors', 'ZenFlex', 'B08Y7K3L4M', TRUE),
    ('BEAU-001', 'Vitamin C Serum 30ml', 'Beauty', 'GlowLab', 'B09M2N4P6Q', TRUE),
    ('BEAU-002', 'Retinol Night Cream 50ml', 'Beauty', 'AgeDefy', 'B08K3L5M7N', TRUE),
    ('TOYS-001', 'STEM Building Blocks 200pcs', 'Toys & Games', 'BrainBuild', 'B07P8R9S2T', TRUE),
    ('TOYS-002', 'Remote Control Drone HD Camera', 'Toys & Games', 'SkyHigh', 'B09V2W4X6Y', TRUE)
ON CONFLICT (sku) DO UPDATE SET
    name = EXCLUDED.name,
    category = EXCLUDED.category,
    brand = EXCLUDED.brand,
    asin = EXCLUDED.asin,
    is_active = EXCLUDED.is_active,
    updated_at = NOW();

-- ============================================================
-- PRODUCT COSTS (COGS breakdown per product)
-- ============================================================
WITH product_ids AS (
    SELECT id, sku FROM products WHERE sku IN (
        'ELEC-001','ELEC-002','HOME-001','HOME-002',
        'SPRT-001','SPRT-002','BEAU-001','BEAU-002',
        'TOYS-001','TOYS-002'
    )
)
INSERT INTO product_costs (product_id, cogs_per_unit, packaging_cost, fulfillment_cost, effective_from, source)
SELECT
    pi.id,
    CASE pi.sku
        WHEN 'ELEC-001' THEN 28.50
        WHEN 'ELEC-002' THEN 12.75
        WHEN 'HOME-001' THEN 8.90
        WHEN 'HOME-002' THEN 5.40
        WHEN 'SPRT-001' THEN 6.20
        WHEN 'SPRT-002' THEN 9.80
        WHEN 'BEAU-001' THEN 4.50
        WHEN 'BEAU-002' THEN 7.30
        WHEN 'TOYS-001' THEN 11.40
        WHEN 'TOYS-002' THEN 22.60
    END as cogs_per_unit,
    CASE pi.sku
        WHEN 'ELEC-001' THEN 1.20
        WHEN 'ELEC-002' THEN 0.50
        WHEN 'HOME-001' THEN 0.80
        WHEN 'HOME-002' THEN 0.40
        WHEN 'SPRT-001' THEN 0.30
        WHEN 'SPRT-002' THEN 0.60
        WHEN 'BEAU-001' THEN 0.35
        WHEN 'BEAU-002' THEN 0.45
        WHEN 'TOYS-001' THEN 0.90
        WHEN 'TOYS-002' THEN 1.50
    END as packaging_cost,
    CASE pi.sku
        WHEN 'ELEC-001' THEN 3.50
        WHEN 'ELEC-002' THEN 2.10
        WHEN 'HOME-001' THEN 2.80
        WHEN 'HOME-002' THEN 1.90
        WHEN 'SPRT-001' THEN 1.50
        WHEN 'SPRT-002' THEN 2.20
        WHEN 'BEAU-001' THEN 1.20
        WHEN 'BEAU-002' THEN 1.40
        WHEN 'TOYS-001' THEN 2.00
        WHEN 'TOYS-002' THEN 3.80
    END as fulfillment_cost,
    '2024-01-01'::DATE as effective_from,
    'mock_seed' as source
FROM product_ids pi
ON CONFLICT DO NOTHING;

-- ============================================================
-- SHIPPING PROFILES (carrier rates)
-- ============================================================
INSERT INTO shipping_profiles (name, carrier, service_level, region, weight_min_kg, weight_max_kg, base_rate, per_kg_rate, min_charge, is_active) VALUES
    ('UPS Ground Domestic', 'ups', 'ground', 'US', 0, 1, 8.50, 0.50, 7.00, TRUE),
    ('UPS Ground Domestic 1-5kg', 'ups', 'ground', 'US', 1, 5, 8.50, 0.85, 7.00, TRUE),
    ('UPS Ground Domestic 5-10kg', 'ups', 'ground', 'US', 5, 10, 12.00, 1.20, 10.00, TRUE),
    ('UPS Express Domestic', 'ups', 'express', 'US', 0, 1, 22.00, 1.50, 20.00, TRUE),
    ('FedEx Ground Domestic', 'fedex', 'ground', 'US', 0, 1, 8.25, 0.48, 6.75, TRUE),
    ('FedEx Ground Domestic 1-5kg', 'fedex', 'ground', 'US', 1, 5, 8.25, 0.82, 6.75, TRUE),
    ('FedEx Express Domestic', 'fedex', 'express', 'US', 0, 1, 21.50, 1.45, 19.50, TRUE),
    ('DHL eCommerce Domestic', 'dhl', 'ground', 'US', 0, 2, 7.90, 0.45, 6.50, TRUE),
    ('USPS Priority Mail', 'usps', 'priority', 'US', 0, 1, 9.65, 0.60, 8.00, TRUE),
    ('UPS International Standard', 'ups', 'standard', 'INTL', 0, 0.5, 18.00, 4.50, 16.00, TRUE),
    ('FedEx International Connect', 'fedex', 'connect', 'INTL', 0, 0.5, 17.50, 4.25, 15.50, TRUE),
    ('DHL Express Worldwide', 'dhl', 'express', 'INTL', 0, 0.5, 28.00, 6.00, 25.00, TRUE)
ON CONFLICT DO NOTHING;

-- ============================================================
-- PRICING RULES (guardrail configurations)
-- ============================================================
INSERT INTO pricing_rules (name, scope_type, scope_value, min_margin_pct, max_premium_pct, vrp_drift_threshold_pct, elasticity_threshold, suppression_enabled, auto_adjust_enabled, priority, is_active) VALUES
    ('Global Default', 'global', NULL, 15.00, 30.00, 5.00, 2.5, TRUE, FALSE, 100, TRUE),
    ('Electronics Category', 'category', 'Electronics', 20.00, 25.00, 3.00, 2.0, TRUE, TRUE, 50, TRUE),
    ('Beauty Category', 'category', 'Beauty', 25.00, 40.00, 5.00, 3.0, TRUE, FALSE, 50, TRUE),
    ('Home & Kitchen Category', 'category', 'Home & Kitchen', 18.00, 35.00, 4.00, 2.5, TRUE, FALSE, 50, TRUE),
    ('Sports & Outdoors Category', 'category', 'Sports & Outdoors', 22.00, 30.00, 4.00, 2.2, TRUE, TRUE, 50, TRUE),
    ('Toys & Games Category', 'category', 'Toys & Games', 15.00, 50.00, 6.00, 3.5, TRUE, FALSE, 50, TRUE),
    ('High-Value Electronics (ELEC-001)', 'product', 'ELEC-001', 25.00, 20.00, 2.00, 1.5, TRUE, TRUE, 10, TRUE),
    ('Promotional Override (TOYS-001)', 'product', 'TOYS-001', 10.00, 60.00, 8.00, 4.0, FALSE, FALSE, 20, TRUE)
ON CONFLICT DO NOTHING;

-- ============================================================
-- INITIAL VRP SNAPSHOTS (baseline data)
-- ============================================================
WITH product_data AS (
    SELECT id, sku, asin FROM products WHERE is_active = TRUE
)
INSERT INTO vrp_snapshots (product_id, asin, marketplace_id, vrp_price, current_buybox_price, our_current_price, price_delta_pct, competitor_count, lowest_competitor_price, data_source, scraped_at)
SELECT
    pd.id,
    pd.asin,
    'ATVPDKIKX0DER',
    CASE pd.sku
        WHEN 'ELEC-001' THEN 79.99
        WHEN 'ELEC-002' THEN 24.99
        WHEN 'HOME-001' THEN 34.99
        WHEN 'HOME-002' THEN 18.99
        WHEN 'SPRT-001' THEN 22.99
        WHEN 'SPRT-002' THEN 29.99
        WHEN 'BEAU-001' THEN 28.99
        WHEN 'BEAU-002' THEN 39.99
        WHEN 'TOYS-001' THEN 34.99
        WHEN 'TOYS-002' THEN 89.99
    END as vrp_price,
    CASE pd.sku
        WHEN 'ELEC-001' THEN 77.50
        WHEN 'ELEC-002' THEN 23.99
        WHEN 'HOME-001' THEN 33.50
        WHEN 'HOME-002' THEN 17.99
        WHEN 'SPRT-001' THEN 21.99
        WHEN 'SPRT-002' THEN 28.50
        WHEN 'BEAU-001' THEN 27.99
        WHEN 'BEAU-002' THEN 38.50
        WHEN 'TOYS-001' THEN 32.99
        WHEN 'TOYS-002' THEN 84.99
    END as current_buybox_price,
    CASE pd.sku
        WHEN 'ELEC-001' THEN 79.99
        WHEN 'ELEC-002' THEN 24.99
        WHEN 'HOME-001' THEN 34.99
        WHEN 'HOME-002' THEN 18.99
        WHEN 'SPRT-001' THEN 22.99
        WHEN 'SPRT-002' THEN 29.99
        WHEN 'BEAU-001' THEN 28.99
        WHEN 'BEAU-002' THEN 39.99
        WHEN 'TOYS-001' THEN 34.99
        WHEN 'TOYS-002' THEN 89.99
    END as our_current_price,
    ROUND((
        CASE pd.sku
            WHEN 'ELEC-001' THEN (79.99 - 79.99) / 79.99 * 100
            WHEN 'ELEC-002' THEN (24.99 - 24.99) / 24.99 * 100
            WHEN 'HOME-001' THEN (34.99 - 34.99) / 34.99 * 100
            WHEN 'HOME-002' THEN (18.99 - 18.99) / 18.99 * 100
            WHEN 'SPRT-001' THEN (22.99 - 22.99) / 22.99 * 100
            WHEN 'SPRT-002' THEN (29.99 - 29.99) / 29.99 * 100
            WHEN 'BEAU-001' THEN (28.99 - 28.99) / 28.99 * 100
            WHEN 'BEAU-002' THEN (39.99 - 39.99) / 39.99 * 100
            WHEN 'TOYS-001' THEN (34.99 - 34.99) / 34.99 * 100
            WHEN 'TOYS-002' THEN (89.99 - 89.99) / 89.99 * 100
        END
    )::numeric, 4) as price_delta_pct,
    CASE pd.sku
        WHEN 'ELEC-001' THEN 12
        WHEN 'ELEC-002' THEN 8
        WHEN 'HOME-001' THEN 15
        WHEN 'HOME-002' THEN 20
        WHEN 'SPRT-001' THEN 10
        WHEN 'SPRT-002' THEN 7
        WHEN 'BEAU-001' THEN 9
        WHEN 'BEAU-002' THEN 6
        WHEN 'TOYS-001' THEN 18
        WHEN 'TOYS-002' THEN 5
    END as competitor_count,
    CASE pd.sku
        WHEN 'ELEC-001' THEN 75.99
        WHEN 'ELEC-002' THEN 22.99
        WHEN 'HOME-001' THEN 31.99
        WHEN 'HOME-002' THEN 16.99
        WHEN 'SPRT-001' THEN 19.99
        WHEN 'SPRT-002' THEN 26.99
        WHEN 'BEAU-001' THEN 25.99
        WHEN 'BEAU-002' THEN 35.99
        WHEN 'TOYS-001' THEN 29.99
        WHEN 'TOYS-002' THEN 79.99
    END as lowest_competitor_price,
    'mock_seed' as data_source,
    NOW() - INTERVAL '1 hour' as scraped_at
FROM product_data pd
ON CONFLICT DO NOTHING;

-- ============================================================
-- INITIAL SYSTEM EVENTS (audit trail)
-- ============================================================
INSERT INTO system_events (event_type, severity, component, message, details, correlation_id) VALUES
    ('migration_applied', 'info', 'migration', 'Applied migration 001_init.sql', '{"migration": "001_init.sql"}', gen_random_uuid()),
    ('migration_applied', 'info', 'migration', 'Applied migration 002_vrp_tables.sql', '{"migration": "002_vrp_tables.sql"}', gen_random_uuid()),
    ('migration_applied', 'info', 'migration', 'Applied migration 003_audit_log.sql', '{"migration": "003_audit_log.sql"}', gen_random_uuid()),
    ('seed_data_loaded', 'info', 'migration', 'Loaded mock seed data for 10 products', '{"product_count": 10, "seed_type": "mock"}', gen_random_uuid()),
    ('scraper_run_completed', 'info', 'ingestion', 'Initial VRP baseline snapshots created', '{"snapshot_count": 10, "source": "mock_seed"}', gen_random_uuid())
ON CONFLICT DO NOTHING;