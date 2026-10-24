# ADR-005: PostgreSQL canonical store

Accepted. SQLAlchemy and Alembic provide scoped relational persistence and explicit migrations. Collector rejects SQLite URLs. `(project_id,event_id)` is idempotent. Batch commit precedes receipt. Money uses NUMERIC(24,12), computed with Decimal. PostgreSQL queries aggregate scoped observations; no local laptop database is involved. Future entities arrive in forward migrations rather than unused placeholder tables.
