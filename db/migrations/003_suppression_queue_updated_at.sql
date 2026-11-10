-- 003: Add updated_at to the suppression/VRP tables.
-- Repairs the BEFORE UPDATE triggers copied from 001_init.sql
-- (update_updated_at_column() assigns NEW.updated_at = NOW(), but the
-- suppression_queue / vrp_snapshots / vrp_drift_events tables created in
-- 002_vrp_tables.sql never had an updated_at column, so ANY UPDATE on
-- those tables fails at runtime with PL/pgSQL: record "new" has no field
-- "updated_at").
--
-- Apply to the live DB ONLY with DBA/owner authorisation:
--   psql "$DATABASE_URL" -f db/migrations/003_suppression_queue_updated_at.sql
-- NOTE: Do NOT rewrite 002_vrp_tables.sql (already applied); this 003 is
-- the corrective delta.

ALTER TABLE suppression_queue ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE vrp_snapshots   ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE vrp_drift_events ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();
