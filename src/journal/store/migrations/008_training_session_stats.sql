-- Migration 008: Training session SL/TP statistics tracking
-- Purpose: Track SL/TP hit rates and performance metrics for training sessions

CREATE TABLE IF NOT EXISTS training_session_stats (
  session_id INTEGER PRIMARY KEY REFERENCES training_sessions(id) ON DELETE CASCADE,
  total_closed INTEGER NOT NULL DEFAULT 0,
  sl_hits INTEGER NOT NULL DEFAULT 0,
  tp_hits INTEGER NOT NULL DEFAULT 0,
  manual_closes INTEGER NOT NULL DEFAULT 0,
  updated_at_msc INTEGER NOT NULL
);
