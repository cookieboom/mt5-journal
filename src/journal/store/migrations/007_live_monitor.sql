-- Migration 007 — live monitor tables (Spec C).
--
-- Brings a v6 database forward to v7. ADDITIVE only: three new tables, no
-- existing table touched. The same DDL lives in schema.sql for fresh databases;
-- the two must stay byte-identical (tests/test_migrations.py::test_migrated_db_matches_a_fresh_db).
--
-- All three are CACHE / EPHEMERAL: `journal rebuild` never touches them and the
-- app is correct if they are empty (the forming bar is re-fetched next cycle).
-- All *_msc are epoch ms. beat_msc/updated_msc/expires_msc/requested_msc are true
-- UTC; live_candles.time_msc is broker server time (bar open), like `candles`.

-- Single-row liveness beacon. `journal live` overwrites beat_msc every cycle.
CREATE TABLE IF NOT EXISTS live_heartbeat (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    beat_msc INTEGER NOT NULL
);

-- Demand-driven watch registry. Web upserts (with a TTL); `journal live` reads
-- the still-active rows each cycle and fetches their forming bar.
CREATE TABLE IF NOT EXISTS live_watches (
    symbol        TEXT    NOT NULL,
    timeframe     TEXT    NOT NULL,
    expires_msc   INTEGER NOT NULL,       -- active while expires_msc > now
    requested_msc INTEGER NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);

-- At most one FORMING bar per (symbol, timeframe). Overwritten freely — NOT part
-- of the candles append-only contract. Column types mirror `candles` exactly.
CREATE TABLE IF NOT EXISTS live_candles (
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    time_msc    INTEGER NOT NULL,         -- bar OPEN time (bucket start), server time
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    tick_volume INTEGER,
    spread      INTEGER,
    real_volume INTEGER,
    updated_msc INTEGER NOT NULL,         -- true UTC of last overwrite
    PRIMARY KEY (symbol, timeframe)
) WITHOUT ROWID;
