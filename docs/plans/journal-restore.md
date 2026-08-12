# `journal restore` — the half of `backup` that runs on the worst day

**Status:** built 2026-08-13.

## Why

`journal backup` ships, `journal live` takes one daily, seven are kept. Nothing
in this project reads one back into place. So the procedure on the day it
matters — a corrupt `journal.db`, a bad `rebuild`, a file deleted — is a human
improvising `cp` under stress, against the one file in this project that
cannot be re-synced (Trap 16). Three ways that improvisation loses data, all of
them silent:

1. **The old `-wal`/`-shm` are left behind.** Replace `journal.db` and leave its
   sidecars beside it and SQLite may recover the *previous* database's WAL frames
   into the *restored* file. `cp backup journal.db` alone is not a restore.
2. **`journal live` is still running.** It holds the old file open. After the
   copy it keeps committing into the file that was replaced (or into a WAL for
   it), so the store forks in two and the newest trades land in the copy nobody
   is reading.
3. **The backup was never read back.** Restoring an unverified snapshot over a
   damaged database turns one bad file into two.

## What it does

`journal restore [--from PATH] [--yes] [--db PATH]`

1. **Pick the source.** `--from`, else the newest `journal-*.db` in
   `<db dir>/backups`. Error if there is none.
2. **Verify the source FIRST.** `PRAGMA integrity_check` plus the deal/trade
   counts, read out of the snapshot before anything on disk is touched. A source
   that fails stops the command; the target is still whatever it was.
3. **Refuse while the daemon is up.** A heartbeat newer than
   `health.HEARTBEAT_MAX_AGE_S` means `journal live` is writing. Best effort — a
   target too corrupt to read is one nobody could be writing to sanely, and the
   restore proceeds.
4. **Move the current store aside, never delete it.** `journal.db` →
   `journal-replaced-<UTC>.db`, and its `-wal`/`-shm` with it (failure 1). Even
   a corrupt file is evidence, and it may hold deals the snapshot predates.
5. **Copy in through the SQLite backup API,** the same call `snapshot()` uses.
6. **Read the result back** — `integrity_check` and counts on the file now
   sitting at `db_path` — and print both, because a restore nobody has read
   back is the same guess a backup nobody has read back is.

The CLI confirms before step 4 unless `--yes` is passed: it replaces a database.

## Deliberately not

- **No merge, no partial restore.** The snapshot replaces the store wholesale.
  Deals the snapshot predates are recoverable — `journal sync` re-pulls whatever
  the broker still has, and the replaced file is still on disk. Merging two
  SQLite stores by hand is how you get a third, wronger one.
- **No auto-restore anywhere.** Nothing calls this but a human typing it.
- **`--from` accepts any file, not just auto-named ones.** It is verified like
  every other source; refusing a hand-named archive would refuse the file
  someone deliberately copied off-machine.
