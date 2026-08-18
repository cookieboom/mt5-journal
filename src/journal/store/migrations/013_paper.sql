-- 013: paper trading — a virtual account with a balance the human sets.
-- Money is USC (accounts.currency), all *_msc are epoch ms server-UTC.
-- NOT derived from raw and NOT a chart cache: `journal rebuild` never touches
-- these tables, exactly like the training_* group. No fictional deal ever
-- reaches deals_raw (rule 2) — that is the whole reason this group exists.

CREATE TABLE IF NOT EXISTS paper_accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    initial_balance REAL    NOT NULL,          -- USC
    balance         REAL    NOT NULL,          -- USC, REALIZED only
    leverage        INTEGER NOT NULL,          -- e.g. 500 for 1:500
    stopout_pct     REAL    NOT NULL,          -- margin level % that liquidates
    status          TEXT    NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','archived')),
    created_at_msc  INTEGER NOT NULL,
    archived_at_msc INTEGER
);

-- One order is one position: this account is margin_mode = 2 (HEDGING), so a
-- pending order is just a row with status='pending'. A partial close SPLITS —
-- the closed slice becomes a new row with parent_id set and the parent's
-- volume shrinks — so every closed row is a complete trade record and no
-- statistic needs a special case.
-- sl/tp: 0 = none set (rule 4). sl_initial is written ONCE at fill so R stays
-- honest after the stop is moved, the same discipline `trades` uses.
CREATE TABLE IF NOT EXISTS paper_positions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id     INTEGER NOT NULL
                       REFERENCES paper_accounts(id) ON DELETE CASCADE,
    symbol         TEXT    NOT NULL,           -- verbatim MT5 symbol (rule 11)
    symbol_base    TEXT    NOT NULL,           -- normalised (rule 11)
    direction      TEXT    NOT NULL CHECK (direction IN ('buy','sell')),
    order_kind     TEXT    NOT NULL CHECK (order_kind IN ('market','limit','stop')),
    request_price  REAL,                       -- limit/stop trigger; NULL for market
    volume         REAL    NOT NULL,
    sl             REAL    NOT NULL DEFAULT 0,
    tp             REAL    NOT NULL DEFAULT 0,
    sl_initial     REAL,                       -- write-once at fill; NULL = unknown
    expires_msc    INTEGER,                    -- NULL = good till cancelled
    status         TEXT    NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','open','closed','cancelled','expired')),
    requested_msc  INTEGER NOT NULL,
    entry_msc      INTEGER,
    entry_price    REAL,
    exit_msc       INTEGER,
    exit_price     REAL,
    exit_reason    TEXT CHECK (exit_reason IN ('tp','sl','manual','stopout','reverse')),
    net_profit     REAL,                       -- USC, signed; NULL until resolved
    r_multiple     REAL,                       -- NULL when no SL (rule 4)
    mae            REAL,
    mfe            REAL,
    mae_r          REAL,
    mfe_r          REAL,
    parent_id      INTEGER REFERENCES paper_positions(id) ON DELETE CASCADE,
    created_at_msc INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_account
    ON paper_positions (account_id, status);
CREATE INDEX IF NOT EXISTS idx_paper_positions_symbol
    ON paper_positions (symbol, status);

-- Latest tick per symbol. Overwritten freely — a latest-value cache like
-- live_candles, NOT part of the candles append-only contract. `tick_msc` is the
-- broker's tick time; `updated_msc` is true UTC of the overwrite and is what
-- staleness is judged against.
CREATE TABLE IF NOT EXISTS live_quotes (
    symbol      TEXT PRIMARY KEY,
    bid         REAL    NOT NULL,
    ask         REAL    NOT NULL,
    tick_msc    INTEGER NOT NULL,
    updated_msc INTEGER NOT NULL
) WITHOUT ROWID;
