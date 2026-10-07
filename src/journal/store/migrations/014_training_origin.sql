-- 014: training_sessions.origin — 'blind' (a session the human started cold)
-- vs 'study' (a Strategy Tester replay jump onto a trade the tester already
-- found). Study sessions are cherry-picked by construction, so the career
-- summary excludes them by default; they must never inflate blind stats.
ALTER TABLE training_sessions ADD COLUMN origin TEXT NOT NULL DEFAULT 'blind'
    CHECK (origin IN ('blind', 'study'));
