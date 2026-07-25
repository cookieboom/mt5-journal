-- Migration 005 — app_prefs (Chart Phase C preference persistence).
--
-- Brings a v4 database forward to v5. ADDITIVE only: one new table, no existing
-- table touched. The same DDL lives in schema.sql for fresh databases; the two
-- must stay in lockstep (tests/test_migrations.py::test_migrated_db_matches_a_fresh_db).
--
-- Durable app config (chart settings under key 'chart'). NOT a chart cache and
-- NOT derived from raw — `journal rebuild` never touches it. No bridge.
CREATE TABLE IF NOT EXISTS app_prefs (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_ms INTEGER NOT NULL
);
