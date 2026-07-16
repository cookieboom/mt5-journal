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
CREATE TABLE IF NOT EXISTS symbol_specs (
    symbol            TEXT PRIMARY KEY,   -- verbatim, e.g. 'XAUUSDc'
    symbol_base       TEXT NOT NULL,      -- normalised, e.g. 'XAUUSD'
    digits            INTEGER,
    point             REAL,
    tick_size         REAL,               -- trade_tick_size
    tick_value        REAL,               -- trade_tick_value, in ACCOUNT currency
    contract_size     REAL,
    currency_profit   TEXT,
    fetched_at        INTEGER NOT NULL    -- refetch weekly; brokers change these
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

-- ---------------------------------------------------------------- convenience

CREATE VIEW IF NOT EXISTS v_trades_annotated AS
SELECT t.*,
       a.setup, a.confidence, a.emotion, a.followed_plan, a.notes
FROM trades t
LEFT JOIN annotations a
       ON a.account_login = t.account_login
      AND a.position_id   = t.position_id
      AND a.segment       = t.segment;
