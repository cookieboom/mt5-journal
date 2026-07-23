-- Migration 002 — M9 live positions + trade commands.
--
-- Brings a v1 database (M0–M8) forward to v2. Everything here is ADDITIVE:
-- no table is dropped, no column is redefined, no row is rewritten. A migration
-- that destroys data has no business existing in a project whose entire premise
-- is that the broker deletes history and this journal does not (Trap 16).
--
-- The same DDL exists in schema.sql for freshly-created databases. The two must
-- stay in lockstep — tests/test_migrations.py::test_migrated_db_matches_a_fresh_db
-- compares every table and column of both paths and fails if they drift.

-- ---------------------------------------------------------------- live state

-- CURRENT open positions, mirrored from positions_get() by `journal live`.
-- Deliberately NOT history: one row per open position, REPLACEd wholesale each
-- cycle, deleted when the position closes. The append-only history of how a
-- position's SL/TP moved is `sl_tp_snapshots` (M4) and stays there.
--
-- The web reads this table instead of the bridge — that is what keeps CLAUDE.md
-- rules 1 and 12 literally true inside `web/` (see docs/plans/m9-*.md §0.4).
--
-- Money columns (profit, swap) are in accounts.currency = USC on this account.
-- `observed_msc` is poller wall-clock TRUE UTC; `open_time_msc` is broker SERVER
-- time. They are different clocks — never compare or subtract them (Trap 7).
CREATE TABLE IF NOT EXISTS open_positions (
    account_login  INTEGER NOT NULL,
    position_id    INTEGER NOT NULL,
    symbol         TEXT NOT NULL,           -- verbatim from MT5, e.g. 'XAUUSDc'
    symbol_base    TEXT NOT NULL,           -- normalised, e.g. 'XAUUSD'
    direction      TEXT CHECK (direction IN ('buy','sell')),
    volume         REAL,
    open_price     REAL,
    price_current  REAL,
    sl             REAL,                    -- 0.0 = none set, NULL = unknown (rule 4)
    tp             REAL,
    profit         REAL,                    -- FLOATING, not realized. USC.
    swap           REAL,
    magic          INTEGER,
    open_time_msc  INTEGER,                 -- broker SERVER time
    observed_msc   INTEGER NOT NULL,        -- true UTC, when we saw this
    PRIMARY KEY (account_login, position_id)
);

-- ------------------------------------------------------------ trade commands

-- The intent queue. The web INSERTs a 'pending' row and never talks to the
-- bridge; the single `journal live` process claims it, sends it, and writes the
-- outcome back. This is also the audit log: what was asked for, when, and what
-- the broker actually said.
--
-- Append-only in the way that matters: the INTENT columns (kind, sl, tp, volume)
-- are written once at insert and NEVER updated. Only the lifecycle columns
-- (status, *_msc, and the result_* / retcode / error group) move afterwards.
-- Rewriting an intent after the fact would make the audit log a fiction.
--
-- status lifecycle:
--   pending  -> claimed -> sent -> done | failed
--   pending  -> rejected                      (validation refused it; never sent)
-- A 'sent' row whose process died stays failed with an explanatory error and is
-- NEVER auto-retried — an order that may have reached the broker must not be
-- re-sent by a machine.
--
-- sl/tp follow rule 4 exactly, and the distinction is load-bearing here:
--   NULL = leave this level untouched
--   0.0  = clear this level
CREATE TABLE IF NOT EXISTS trade_commands (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_login   INTEGER NOT NULL,
    position_id     INTEGER NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN
                        ('modify_sltp','close','close_partial','add_volume')),

    -- intent, write-once
    sl              REAL,
    tp              REAL,
    volume          REAL,
    requested_msc   INTEGER NOT NULL,       -- true UTC

    -- lifecycle
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                        ('pending','claimed','sent','done','failed','rejected')),
    claimed_msc     INTEGER,
    completed_msc   INTEGER,

    -- what the broker said. NULL until it has said something (rule 4).
    retcode         INTEGER,
    result_deal     INTEGER,
    result_order    INTEGER,
    result_volume   REAL,                   -- ACTUAL filled volume; a partial
                                            -- fill is not the requested volume
    result_price    REAL,
    broker_comment  TEXT,
    error           TEXT,                   -- our own failure/rejection reason
    raw_json        TEXT                    -- full result dump, forward-compat
);

CREATE INDEX IF NOT EXISTS ix_cmd_pending  ON trade_commands (account_login, status, id);
CREATE INDEX IF NOT EXISTS ix_cmd_position ON trade_commands (account_login, position_id, id);

-- ------------------------------------------------- symbol_specs: order limits

-- Without these, validating a lot size or an SL distance is impossible from
-- stored data — which is why Phase 3 cannot run before this migration.
-- All nullable ON PURPOSE: a spec fetched before M9 has never seen these values,
-- and rule 4 says that is UNKNOWN, not zero. `domain/commands.py` REJECTS a
-- command whose spec is unknown rather than assuming a permissive default.
ALTER TABLE symbol_specs ADD COLUMN volume_min   REAL;
ALTER TABLE symbol_specs ADD COLUMN volume_max   REAL;
ALTER TABLE symbol_specs ADD COLUMN volume_step  REAL;
ALTER TABLE symbol_specs ADD COLUMN stops_level  INTEGER;  -- min SL/TP distance, in points
ALTER TABLE symbol_specs ADD COLUMN freeze_level INTEGER;  -- distance where modification is frozen
ALTER TABLE symbol_specs ADD COLUMN trade_mode   INTEGER;  -- 0=disabled .. 4=full
ALTER TABLE symbol_specs ADD COLUMN filling_mode INTEGER;  -- broker's allowed fill modes, bitmask
