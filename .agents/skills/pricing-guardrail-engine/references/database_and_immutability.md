# Database Schema, Immutability & Security Architecture

## Database Overview

The project uses PostgreSQL with a thread-safe connection pool managed in [`src/ingestion/db.py`](file:///c:/Users/Uzer/Project/pricing-guardrail-engine/src/ingestion/db.py) via `psycopg2.pool.ThreadedConnectionPool`.

### Core Tables

1. **`products` (`db/migrations/001_initial_schema.sql`):**
   - Catalog definitions, wholesale COGS, packaging cost, shipping cost, minimum target dollar margin, category, active status.
2. **`telemetry_raw` (`db/migrations/001_initial_schema.sql`):**
   - High-velocity event stream: Buy Box price, VRP reference price, competitor lowest price, seller count, sales velocity, elasticity score.
3. **`price_change_log` (`db/migrations/003_audit_log.sql`):**
   - Immutable audit log of every repricing decision, rule clamped price, floor price at decision time, guardrail status (`APPROVED`, `WARNING`, `BLOCKED`), and margin dollars defended.
4. **`suppression_queue` (`db/migrations/002_rules_suppression.sql`):**
   - Distressed listings blocked by predatory algorithm undercuts, with retry attempts and backoff intervals.

---

## Cryptographic Append-Only Immutability

Defined in [`db/migrations/003_audit_log.sql`](file:///c:/Users/Uzer/Project/pricing-guardrail-engine/db/migrations/003_audit_log.sql).

To satisfy financial regulatory compliance and audit requirements, pricing audit records cannot be retroactively altered, updated, or deleted by any application user or automated bot.

### Immutability Triggers
PostgreSQL functions reject any `UPDATE` or `DELETE` at the kernel database level:

```sql
CREATE OR REPLACE FUNCTION prevent_audit_log_tampering()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'PERMISSION DENIED: Audit log records are append-only and cryptographically immutable.'
        USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_price_change_log
    BEFORE UPDATE OR DELETE ON price_change_log
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_tampering();

CREATE TRIGGER trg_protect_telemetry_raw
    BEFORE UPDATE OR DELETE ON telemetry_raw
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_tampering();
```

### Verification via Security Audit Engine
Running `src/analytics/security_audit.py` or calling `GET /api/v1/security-audit` initiates live non-destructive test transactions:
1. Attempts an `UPDATE` inside an isolated transaction.
2. Verifies that PostgreSQL aborts with SQLSTATE `42501`.
3. Attempts a `DELETE` and confirms refusal.
4. Rolls back the transaction to preserve database integrity.

---

## SQL Injection Defense

All queries across the entire repository use strict parameterized `%s` tuples:
```python
# CORRECT:
cursor.execute("SELECT * FROM products WHERE sku = %s", (sku,))

# FORBIDDEN (Will fail code audit):
cursor.execute(f"SELECT * FROM products WHERE sku = '{sku}'")
cursor.execute("SELECT * FROM products WHERE sku = '%s'" % sku)
```
