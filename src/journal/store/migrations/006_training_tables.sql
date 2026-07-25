-- Migration 006 — training/replay tables (Chart Phase D).
--
-- Brings a v5 database forward to v6. ADDITIVE only: two new tables, no existing
-- table touched. The same DDL lives in schema.sql for fresh databases; the two
-- must stay byte-identical (tests/test_migrations.py::test_migrated_db_matches_a_fresh_db).
--
-- Durable training data. NOT a chart cache and NOT derived from raw — `journal
-- rebuild` never touches it (it rebuilds only `trades`). No bridge. Money is USC.
-- All *_msc are epoch ms, server-UTC. sl/tp: 0 = none set (rule 4).
CREATE TABLE IF NOT EXISTS training_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    symbol_base     TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    range_start_msc INTEGER NOT NULL,
    range_end_msc   INTEGER NOT NULL,
    cursor_msc      INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'ended')),
    created_at_msc  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS training_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL
                            REFERENCES training_sessions(id) ON DELETE CASCADE,
    direction           TEXT NOT NULL CHECK (direction IN ('buy', 'sell')),
    volume              REAL NOT NULL,
    decision_msc        INTEGER NOT NULL,
    entry_msc           INTEGER,
    entry_price         REAL,
    sl                  REAL NOT NULL DEFAULT 0,   -- 0 = none set (rule 4)
    tp                  REAL NOT NULL DEFAULT 0,   -- 0 = none set (rule 4)
    close_requested_msc INTEGER,                   -- NULL = no manual close pending
    exit_msc            INTEGER,
    exit_price          REAL,
    exit_reason         TEXT CHECK (exit_reason IN ('tp','sl','manual','eod')),
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','open','closed')),
    net_profit          REAL,                      -- USC, signed; NULL until resolved
    r_multiple          REAL,                      -- NULL if no SL
    mae                 REAL,
    mfe                 REAL,
    mae_r               REAL,
    mfe_r               REAL,
    created_at_msc      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_training_positions_session
    ON training_positions (session_id);
CREATE INDEX IF NOT EXISTS idx_training_positions_status
    ON training_positions (status);
