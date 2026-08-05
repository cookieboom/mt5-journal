-- 010: lab_models — trained regime and timing models (CLAUDE.md rule 9's one
-- predictive corner). Not derived from raw, so `journal rebuild` never touches
-- it. The fitted estimator lives in cache/models/<id>.joblib; config_json plus
-- the recorded seed is enough to rebuild that artifact, which is what keeps
-- cache/ disposable (rule 6).
CREATE TABLE IF NOT EXISTS lab_models (
    id            INTEGER PRIMARY KEY,
    created_ms    INTEGER NOT NULL,
    symbol        TEXT NOT NULL,          -- verbatim, e.g. 'XAUUSDc' (rule 11)
    timeframe     TEXT NOT NULL,          -- matches candles.timeframe
    stage         TEXT NOT NULL,          -- 'regime' | 'timing'
    regime        TEXT,                   -- NULL for stage='regime' or pooled
    kind          TEXT NOT NULL,          -- 'logreg' | 'lgbm'
    config_json   TEXT NOT NULL,
    metrics_json  TEXT NOT NULL,
    train_from_ms INTEGER NOT NULL,
    train_to_ms   INTEGER NOT NULL,
    n_rows        INTEGER NOT NULL,
    pooled        INTEGER NOT NULL DEFAULT 0,
    artifact_path TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 0
);

-- At most one active model per group. `regime` is NULL for regime-stage and
-- pooled rows and SQLite treats NULLs as distinct in a unique index, so the
-- key is COALESCE'd. The index is partial: superseded rows stay for history.
CREATE UNIQUE INDEX IF NOT EXISTS lab_models_active
    ON lab_models (symbol, timeframe, stage, COALESCE(regime, ''))
    WHERE active = 1;

CREATE INDEX IF NOT EXISTS lab_models_lookup
    ON lab_models (symbol, timeframe, created_ms DESC);
