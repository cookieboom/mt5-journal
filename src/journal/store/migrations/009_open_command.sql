-- Migration 009: trade_commands accepts an 'open' command (M9 extension).
--
-- Three changes SQLite cannot make in place — a CHECK constraint cannot be
-- ALTERed and `position_id` is NOT NULL — so the table is rebuilt:
--
--   * `kind` CHECK gains 'open'. An open is the first command in this project
--     that creates a position rather than acting on one.
--   * `position_id` becomes nullable. An open has no position until the broker
--     answers; a sentinel 0 would collide with the audit-log queries that join
--     on it.
--   * three new columns. For an 'open' the symbol and direction cannot be read
--     off a position row, so they live here; `price_ref` records the price the
--     human sized against (evidence, and the re-validation fallback when the
--     bridge cannot supply a fresh tick). All three stay NULL for every other
--     kind, where the position row remains the source of truth.
--
-- This table is the audit log of real orders. The copy is column-explicit so a
-- future column added to one side cannot silently shift the data.
--
-- The runner wraps this file in BEGIN/COMMIT (store/db.py:migrate), so either
-- the whole rebuild lands or none of it does.

CREATE TABLE trade_commands_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_login   INTEGER NOT NULL,
    position_id     INTEGER,                -- NULL for 'open' (no position yet)
    kind            TEXT NOT NULL CHECK (kind IN
                        ('modify_sltp','close','close_partial','add_volume','open')),
    symbol          TEXT,                   -- 'open' only; verbatim MT5 symbol (rule 11)
    direction       TEXT CHECK (direction IN ('buy','sell')),  -- 'open' only
    price_ref       REAL,                   -- 'open' only; price the human sized against
    sl              REAL,
    tp              REAL,
    volume          REAL,
    requested_msc   INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                        ('pending','claimed','sent','done','failed','rejected')),
    claimed_msc     INTEGER,
    completed_msc   INTEGER,
    retcode         INTEGER,
    result_deal     INTEGER,
    result_order    INTEGER,
    result_volume   REAL,
    result_price    REAL,
    broker_comment  TEXT,
    error           TEXT,
    raw_json        TEXT
);

INSERT INTO trade_commands_new
    (id, account_login, position_id, kind, sl, tp, volume, requested_msc,
     status, claimed_msc, completed_msc, retcode, result_deal, result_order,
     result_volume, result_price, broker_comment, error, raw_json)
SELECT
     id, account_login, position_id, kind, sl, tp, volume, requested_msc,
     status, claimed_msc, completed_msc, retcode, result_deal, result_order,
     result_volume, result_price, broker_comment, error, raw_json
FROM trade_commands;

DROP TABLE trade_commands;
ALTER TABLE trade_commands_new RENAME TO trade_commands;

CREATE INDEX IF NOT EXISTS ix_cmd_pending  ON trade_commands (account_login, status, id);
CREATE INDEX IF NOT EXISTS ix_cmd_position ON trade_commands (account_login, position_id, id);
