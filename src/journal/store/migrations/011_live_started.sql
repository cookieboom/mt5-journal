-- 011: live_heartbeat.started_msc — when the RUNNING daemon loaded its code.
-- `beat_msc` says the process is alive; it cannot say the process is current.
-- Three features in one week shipped with "needs a `journal live` RESTART" and
-- nothing on the machine could see the daemon was still on the old code.
-- NULL means "started before this column existed" — unknown, never accused.
ALTER TABLE live_heartbeat ADD COLUMN started_msc INTEGER;
