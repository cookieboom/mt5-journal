-- Migration 003 — Phase A smart candle store.
--
-- Brings a v2 database forward to v3. ADDITIVE only: two new tables, no existing
-- table touched. The same DDL lives in schema.sql for fresh databases; the two
-- must stay in lockstep (tests/test_migrations.py::test_migrated_db_matches_a_fresh_db).

-- Which [from_msc, to_msc] ranges have actually been FETCHED, per (symbol,
-- timeframe). This is how "empty because market closed" (fetched, no bars) is
-- told apart from "empty because never fetched" (must fetch). Ranges are merged
-- on insert into a minimal disjoint set. Bar-open ms, server time. Inclusive.
CREATE TABLE IF NOT EXISTS candle_coverage (
    symbol     TEXT NOT NULL,
    timeframe  TEXT NOT NULL,
    from_msc   INTEGER NOT NULL,
    to_msc     INTEGER NOT NULL,
    PRIMARY KEY (symbol, timeframe, from_msc)
);

-- The on-demand fill queue. The web INSERTs a 'pending' row and never talks to
-- the bridge; `journal live` claims and fulfils it. UNLIKE trade_commands this
-- is idempotent and retry-safe: an orphaned 'claimed' row is re-queued, never
-- failed. No money, no position — refetching candles is always safe.
CREATE TABLE IF NOT EXISTS candle_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    from_msc      INTEGER NOT NULL,
    to_msc        INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|claimed|done|failed
    requested_msc INTEGER NOT NULL,
    claimed_msc   INTEGER,
    completed_msc INTEGER,
    bars_written  INTEGER,
    error         TEXT
);
