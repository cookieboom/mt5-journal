-- Migration 004 — Phase B: constrain candle_requests.status.
--
-- Brings a v3 database forward to v4. ADDITIVE in spirit (no data lost): adds a
-- CHECK enum to candle_requests.status, mirroring trade_commands. SQLite can't
-- ALTER a column to add a CHECK, so rebuild the table and copy rows across. The
-- same constrained DDL lives in schema.sql for fresh DBs; the two must stay in
-- lockstep (tests/test_migrations.py::test_migrated_db_matches_a_fresh_db).
--
-- The runner supplies BEGIN/COMMIT around this file; do not add transaction
-- control here.

CREATE TABLE candle_requests_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    from_msc      INTEGER NOT NULL,
    to_msc        INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                      ('pending','claimed','done','failed')),
    requested_msc INTEGER NOT NULL,
    claimed_msc   INTEGER,
    completed_msc INTEGER,
    bars_written  INTEGER,
    error         TEXT
);

INSERT INTO candle_requests_new
    (id, symbol, timeframe, from_msc, to_msc, status, requested_msc,
     claimed_msc, completed_msc, bars_written, error)
SELECT
    id, symbol, timeframe, from_msc, to_msc, status, requested_msc,
    claimed_msc, completed_msc, bars_written, error
FROM candle_requests;

DROP TABLE candle_requests;
ALTER TABLE candle_requests_new RENAME TO candle_requests;
