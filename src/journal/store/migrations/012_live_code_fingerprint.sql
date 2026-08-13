-- 012: live_heartbeat.code_fingerprint — WHICH code the running daemon loaded.
-- 011 added `started_msc`, and a timestamp can only be compared against file
-- mtimes: editing ANY `.py` under the package — a web view, an analytics
-- module the loop never imports — accused the daemon of running old code, and
-- a `git checkout` rewriting mtimes hid a daemon that really was stale. This
-- column holds JSON {module path: sha256[:12]} for the modules the process had
-- actually imported, so `journal status` compares content and names the file.
-- NULL means "started before this column existed" — fall back to mtimes.
ALTER TABLE live_heartbeat ADD COLUMN code_fingerprint TEXT;
