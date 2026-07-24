-- mt5-journal schema (SQLite)
-- Move to src/journal/store/schema.sql
--
-- Design rules (see CLAUDE.md):
--   * _raw tables mirror MT5 exactly, append-only, never edited.
--   * trades is DERIVED: `journal rebuild` may DELETE and re-INSERT all of it.
--   * All *_msc columns are epoch MILLISECONDS as returned by MT5, i.e. BROKER
--     SERVER TIME. True UTC = *_msc - (server_utc_offset_s * 1000). See
--     docs/mt5-deal-model.md trap 7.
--   * NULL = unknown. 0 = confirmed none. They are not the same.
--   * Every money column is in accounts.currency. On this account that is USC
--     (US cents), NOT dollars. Never hardcode '$' anywhere.
--   * `symbol` is verbatim from MT5 ('XAUUSDc'); `symbol_base` is normalised
--     ('XAUUSD'). Query MT5 with the former, GROUP BY the latter.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- meta

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    login           INTEGER PRIMARY KEY,
    broker          TEXT,
    server          TEXT,
    currency        TEXT NOT NULL,
    leverage        INTEGER,
    margin_mode     INTEGER,            -- 0=netting 1=exchange 2=hedging
    is_demo         INTEGER,            -- 0/1
    balance         REAL,               -- SNAPSHOT at last sync; the balance-invariant half (§6). Money = accounts.currency (USC).
    equity          REAL,               -- SNAPSHOT at last sync; balance + floating P&L of open positions.
    first_seen_at   INTEGER NOT NULL,
    last_synced_at  INTEGER
);

CREATE TABLE IF NOT EXISTS sync_state (
    account_login        INTEGER NOT NULL,
    stream               TEXT NOT NULL,    -- 'deals' | 'orders' | 'candles'
    last_synced_msc      INTEGER,          -- watermark, server time
    server_utc_offset_s  INTEGER,          -- MEASURED, not assumed. Changes with broker DST.
    measured_at          INTEGER,
    PRIMARY KEY (account_login, stream)
);

-- ---------------------------------------------------------------- raw (source of truth)

CREATE TABLE IF NOT EXISTS deals_raw (
    account_login  INTEGER NOT NULL,
    ticket         INTEGER NOT NULL,
    order_ticket   INTEGER,               -- MT5 field name is `order` (reserved word in SQL)
    position_id    INTEGER NOT NULL,      -- 0 for balance/credit/charge deals
    symbol         TEXT,                  -- '' for non-trade deals
    type           INTEGER NOT NULL,      -- DEAL_TYPE_*
    entry          INTEGER NOT NULL,      -- 0=IN 1=OUT 2=INOUT 3=OUT_BY
    reason         INTEGER,               -- DEAL_REASON_* (4=SL, 5=TP, 6=stopout)
    magic          INTEGER,
    volume         REAL,
    price          REAL,
    commission     REAL DEFAULT 0,
    swap           REAL DEFAULT 0,
    profit         REAL DEFAULT 0,
    fee            REAL DEFAULT 0,
    time_msc       INTEGER NOT NULL,
    comment        TEXT,
    external_id    TEXT,
    raw_json       TEXT NOT NULL,         -- full _asdict() dump; forward-compat when MT5 adds fields
    ingested_at    INTEGER NOT NULL,
    PRIMARY KEY (account_login, ticket)   -- tickets are unique per account only (trap 10)
);

CREATE INDEX IF NOT EXISTS ix_deals_position ON deals_raw (account_login, position_id);
CREATE INDEX IF NOT EXISTS ix_deals_time     ON deals_raw (account_login, time_msc);
CREATE INDEX IF NOT EXISTS ix_deals_order    ON deals_raw (account_login, order_ticket);

CREATE TABLE IF NOT EXISTS orders_raw (
    account_login    INTEGER NOT NULL,
    ticket           INTEGER NOT NULL,
    position_id      INTEGER,
    position_by_id   INTEGER,
    symbol           TEXT,
    type             INTEGER NOT NULL,
    state            INTEGER,
    reason           INTEGER,
    magic            INTEGER,
    volume_initial   REAL,
    volume_current   REAL,
    price_open       REAL,
    sl               REAL,                -- 0.0 means "not set on this order", NOT "unknown"
    tp               REAL,
    price_stoplimit  REAL,
    time_setup_msc   INTEGER,
    time_done_msc    INTEGER,
    comment          TEXT,
    external_id      TEXT,
    raw_json         TEXT NOT NULL,
    ingested_at      INTEGER NOT NULL,
    PRIMARY KEY (account_login, ticket)
);

CREATE INDEX IF NOT EXISTS ix_orders_position ON orders_raw (account_login, position_id);

-- Written by the M4 poller only. This is how sl_initial becomes knowable
-- for trades where the SL was set AFTER entry (trap 6).
CREATE TABLE IF NOT EXISTS sl_tp_snapshots (
    account_login  INTEGER NOT NULL,
    position_id    INTEGER NOT NULL,
    observed_msc   INTEGER NOT NULL,
    sl             REAL,
    tp             REAL,
    volume         REAL,
    PRIMARY KEY (account_login, position_id, observed_msc)
);

CREATE INDEX IF NOT EXISTS ix_sltp_pos ON sl_tp_snapshots (account_login, position_id, observed_msc);

-- ---------------------------------------------------------------- market data

-- Central candle store. Trades reference a WINDOW of this; they do not snapshot
-- their own candles. Dedupes across trades on the same symbol/day.
CREATE TABLE IF NOT EXISTS candles (
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,          -- 'M1','M5','M15','H1','H4','D1'
    time_msc      INTEGER NOT NULL,       -- bar OPEN time, server time
    open          REAL NOT NULL,
    high          REAL NOT NULL,
    low           REAL NOT NULL,
    close         REAL NOT NULL,
    tick_volume   INTEGER,
    spread        INTEGER,
    real_volume   INTEGER,
    PRIMARY KEY (symbol, timeframe, time_msc)
) WITHOUT ROWID;

-- Needed for risk_amount. Do not compute risk from price distance alone (trap 11).
-- The volume_*/stops_level group (M9) is what makes a trade command validatable;
-- all of it is nullable because a spec fetched before M9 has never seen those
-- values, and NULL = unknown, not zero. `domain/commands.py` REJECTS a command
-- whose spec is unknown rather than assuming a permissive default.
CREATE TABLE IF NOT EXISTS symbol_specs (
    symbol            TEXT PRIMARY KEY,   -- verbatim, e.g. 'XAUUSDc'
    symbol_base       TEXT NOT NULL,      -- normalised, e.g. 'XAUUSD'
    digits            INTEGER,
    point             REAL,
    tick_size         REAL,               -- trade_tick_size
    tick_value        REAL,               -- trade_tick_value, in ACCOUNT currency
    contract_size     REAL,
    currency_profit   TEXT,
    fetched_at        INTEGER NOT NULL,   -- refetch weekly; brokers change these
    volume_min        REAL,               -- M9 (migration 002)
    volume_max        REAL,
    volume_step       REAL,
    stops_level       INTEGER,            -- min SL/TP distance, in points
    freeze_level      INTEGER,            -- distance where modification is frozen
    trade_mode        INTEGER,            -- 0=disabled .. 4=full
    filling_mode      INTEGER             -- broker's allowed fill modes, bitmask
);

-- Which [from_msc, to_msc] ranges have actually been FETCHED, per (symbol,
-- timeframe). This is how "empty because market closed" (fetched, no bars) is
-- told apart from "empty because never fetched" (must fetch). Ranges are merged
-- on insert into a minimal disjoint set. Bar-open ms, server time. Inclusive.
-- Kept byte-identical with migrations/003_candle_store.sql (Phase A).
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

-- ---------------------------------------------------------------- derived (rebuildable)

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_login   INTEGER NOT NULL,
    position_id     INTEGER NOT NULL,
    segment         INTEGER NOT NULL DEFAULT 0,  -- reserved; always 0 on hedging accounts (trap 4)

    symbol          TEXT NOT NULL,        -- verbatim from MT5, e.g. 'XAUUSDc'. Use for MT5 calls.
    symbol_base     TEXT NOT NULL,        -- normalised, e.g. 'XAUUSD'. GROUP BY this in analytics. (trap 12)
    direction       TEXT NOT NULL CHECK (direction IN ('buy','sell')),
    status          TEXT NOT NULL CHECK (status IN ('closed','open','partially_open')),

    open_time_msc   INTEGER NOT NULL,
    close_time_msc  INTEGER,              -- NULL while open
    duration_s      INTEGER,

    volume          REAL NOT NULL,        -- total IN volume
    open_price      REAL NOT NULL,        -- VWAP of IN deals (trap 2)
    close_price     REAL,                 -- VWAP of OUT deals (trap 3)

    sl_initial      REAL,                 -- NULL = unknown. NEVER coerce to 0. (trap 6)
    tp_initial      REAL,
    sl_final        REAL,
    tp_final        REAL,
    sl_source       TEXT CHECK (sl_source IN ('order','poller','unknown')),

    -- All money columns below are in accounts.currency. On THIS account that is
    -- USC = US cents, not dollars. Never format with '$'. (trap 13)
    commission      REAL DEFAULT 0,
    swap            REAL DEFAULT 0,
    profit_gross    REAL DEFAULT 0,
    net_profit      REAL,                 -- sum of profit+commission+swap+fee across ALL deals (trap 9)

    risk_amount     REAL,                 -- account currency; NULL if sl_initial is NULL
    r_multiple      REAL,                 -- net_profit / risk_amount -> UNIT-FREE. NULL if risk unknown.

    mae             REAL,                 -- max adverse excursion, price units (filled in M5)
    mfe             REAL,                 -- max favourable excursion
    mae_r           REAL,
    mfe_r           REAL,

    close_reason    INTEGER,              -- DEAL_REASON_* of last OUT deal: your discipline metric
    magic           INTEGER,
    deal_count      INTEGER,
    rebuilt_at      INTEGER NOT NULL,

    UNIQUE (account_login, position_id, segment)
);

CREATE INDEX IF NOT EXISTS ix_trades_open   ON trades (account_login, open_time_msc);
CREATE INDEX IF NOT EXISTS ix_trades_symbol ON trades (account_login, symbol_base, open_time_msc);
CREATE INDEX IF NOT EXISTS ix_trades_status ON trades (account_login, status);

-- ---------------------------------------------------------------- human layer (never rebuilt)

-- Keyed on (account_login, position_id, segment) NOT trades.id, because
-- trades.id is regenerated on every rebuild and would orphan your notes.
CREATE TABLE IF NOT EXISTS annotations (
    account_login  INTEGER NOT NULL,
    position_id    INTEGER NOT NULL,
    segment        INTEGER NOT NULL DEFAULT 0,
    setup          TEXT,
    confidence     INTEGER CHECK (confidence BETWEEN 1 AND 5),
    emotion        TEXT,
    followed_plan  INTEGER,              -- 0/1, nullable
    notes          TEXT,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    PRIMARY KEY (account_login, position_id, segment)
);

CREATE TABLE IF NOT EXISTS tags (
    account_login  INTEGER NOT NULL,
    position_id    INTEGER NOT NULL,
    segment        INTEGER NOT NULL DEFAULT 0,
    tag            TEXT NOT NULL,
    source         TEXT NOT NULL CHECK (source IN ('auto','manual')),
    PRIMARY KEY (account_login, position_id, segment, tag)
);

CREATE INDEX IF NOT EXISTS ix_tags_tag ON tags (account_login, tag);

-- Named explanations for balance-invariant discrepancies (docs/mt5-deal-model.md
-- §6 + trap 16). A gap is NEVER absorbed into a tolerance — it gets a row here.
-- A row starts 'unexplained' and stays visible in every report until a human
-- writes a `reason`; it does not disappear, it acquires a name. Human-authored,
-- so it lives in this never-rebuilt section alongside annotations.
--   amount: account currency (USC). Signed + when sum(deal cash) exceeds balance.
--   The invariant `journal verify` enforces:
--     sum(deals cash) - sum(reconciliations.amount) == balance   (within 0.01)
CREATE TABLE IF NOT EXISTS reconciliations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_login  INTEGER NOT NULL,
    amount         REAL NOT NULL,
    effective_msc  INTEGER,               -- when the gap occurred (e.g. the correction deal's time_msc)
    status         TEXT NOT NULL DEFAULT 'unexplained'
                     CHECK (status IN ('unexplained','explained')),
    reason         TEXT,                  -- stays 'unexplained' until a human writes this
    evidence       TEXT,                  -- deal ticket, MT5 report figures, etc.
    created_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_recon_account ON reconciliations (account_login);

-- ---------------------------------------------------------------- live (M9)

-- Kept byte-identical in intent with migrations/002_live_trading.sql — a fresh
-- DB and a migrated one must end up with the same schema. See that file for the
-- full rationale on each table; the comments here stay short on purpose so the
-- two copies cannot drift in meaning.

-- CURRENT open positions, mirrored from positions_get() by `journal live`.
-- NOT history: one row per open position, REPLACEd each cycle, deleted on close.
-- The history of how SL/TP moved is the append-only `sl_tp_snapshots` (M4).
-- The web reads THIS instead of the bridge, which is what keeps rules 1 and 12
-- literally true inside `web/`.
-- `observed_msc` is true UTC; `open_time_msc` is broker server time (trap 7).
CREATE TABLE IF NOT EXISTS open_positions (
    account_login  INTEGER NOT NULL,
    position_id    INTEGER NOT NULL,
    symbol         TEXT NOT NULL,
    symbol_base    TEXT NOT NULL,
    direction      TEXT CHECK (direction IN ('buy','sell')),
    volume         REAL,
    open_price     REAL,
    price_current  REAL,
    sl             REAL,                    -- 0.0 = none set, NULL = unknown
    tp             REAL,
    profit         REAL,                    -- FLOATING, not realized. USC.
    swap           REAL,
    magic          INTEGER,
    open_time_msc  INTEGER,                 -- broker SERVER time
    observed_msc   INTEGER NOT NULL,        -- true UTC
    PRIMARY KEY (account_login, position_id)
);

-- The intent queue AND the audit log. Web INSERTs 'pending'; `journal live`
-- claims, sends, and writes the outcome back. The intent columns (kind/sl/tp/
-- volume) are write-once; only the lifecycle and result columns move.
--   pending -> claimed -> sent -> done | failed
--   pending -> rejected                     (refused by validation, never sent)
-- A 'sent' row orphaned by a crash is failed with an explanation and is NEVER
-- auto-retried.
--   sl/tp: NULL = leave untouched, 0.0 = clear it. (rule 4, load-bearing here)
CREATE TABLE IF NOT EXISTS trade_commands (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_login   INTEGER NOT NULL,
    position_id     INTEGER NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN
                        ('modify_sltp','close','close_partial','add_volume')),
    sl              REAL,
    tp              REAL,
    volume          REAL,
    requested_msc   INTEGER NOT NULL,       -- true UTC
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                        ('pending','claimed','sent','done','failed','rejected')),
    claimed_msc     INTEGER,
    completed_msc   INTEGER,
    retcode         INTEGER,
    result_deal     INTEGER,
    result_order    INTEGER,
    result_volume   REAL,                   -- ACTUAL filled volume (partial fills)
    result_price    REAL,
    broker_comment  TEXT,
    error           TEXT,
    raw_json        TEXT
);

CREATE INDEX IF NOT EXISTS ix_cmd_pending  ON trade_commands (account_login, status, id);
CREATE INDEX IF NOT EXISTS ix_cmd_position ON trade_commands (account_login, position_id, id);

-- ---------------------------------------------------------------- convenience

CREATE VIEW IF NOT EXISTS v_trades_annotated AS
SELECT t.*,
       a.setup, a.confidence, a.emotion, a.followed_plan, a.notes
FROM trades t
LEFT JOIN annotations a
       ON a.account_login = t.account_login
      AND a.position_id   = t.position_id
      AND a.segment       = t.segment;
