# Spec — `journal live` takes the backup nobody remembers to take

**Status:** built 2026-08-13.

## Why

Trap 16 (the broker deletes its own deal history) is the reason this journal
exists, and it is also why `data/journal.db` is the only surviving copy of most
of what is in it. A lost file cannot be re-synced.

`journal backup` (2026-08-12) made a correct snapshot *possible*. It did not
make one *happen*: it is a foreground command a human has to remember to type.
HANDOFF records three ad-hoc snapshots in a year — that is the measured rate at
which humans remember. As of this spec `data/backups/` holds exactly one file,
dated the day the command was written.

The daemon that is already running all day is `journal live`. It is the only
process in this project with a heartbeat.

## What

`live_loop` takes an auto-snapshot at most once per `backup_every_s`
(default 24 h), keeping the newest `backup_keep` (default 7).

Rules that matter:

- **Stateless due-check.** "Due" = the newest `backups/journal-*.db` is older
  than `backup_every_s` (mtime), or there is none. No new table, no new column,
  no daemon state to get out of sync — and a `journal live` restarted twice an
  hour still backs up once a day.
- **Only when idle.** Skipped while a trade command is pending. The copy runs
  in the loop thread and stalls the cycle for the length of a 60 MB pager copy;
  an SL/TP or close command must never queue behind it.
- **Never kills the loop.** Any failure is logged and the cycle continues. A
  failed backup is bad; a `journal live` that exits because a disk was full is
  worse.
- **Same snapshots as the command.** Same directory, same `journal-<UTC>.db`
  name, same pruning, same read-back `integrity_check` — so `journal backup`
  and the loop cannot drift, and `--keep` prunes the loop's files too.

Off with `--no-auto-backup`.

## How

The snapshot logic moves out of `cli.backup` into `store/backup.py`
(`snapshot()`, `due()`, `BackupError`) with no behaviour change; the CLI keeps
its output and exit codes, `live_loop` gets the second caller. One shared choke
point, two callers — not two implementations that drift.

## Not doing

- No cron/launchd unit: the daemon already runs, and a second scheduler is a
  second thing to install and forget.
- No off-machine copy. Same disk is a real ceiling (marked `ponytail:`); a
  destination the user has to configure is a decision for a human, and `--dest`
  already covers a manual copy to an external drive.
- No compression. 60 MB × 7 is 420 MB on a machine that has it.
