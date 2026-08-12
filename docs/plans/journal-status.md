# `journal status` — one bridge-free answer to "is this journal healthy?"

## Why

Every silent failure this project has actually had was invisible until it cost
something, and each one already has a detector — in a *different* command:

| Failure | Detector today | Only runs when |
|---|---|---|
| DB corruption | `PRAGMA integrity_check` inside `store/backup.py` | a backup is taken |
| balance drift / archived deals (Trap 16) | `journal verify` | typed |
| `deals_raw` synced but never reconstructed | nothing | — |
| no snapshot in days (Trap 16 again) | `backup.due()` | inside `journal live` |
| `journal live` not actually running | `live_heartbeat` | the web UI reads it |
| a trade command stuck `pending` | nothing | — |

So the human has to *remember which command asks which question*, and two of the
six have no command at all. `journal status` is the single thing you type when
you do not know what to look at.

## What it is

`journal status [--db PATH]` — **read-only, no bridge, no writes.** Every check
already exists as a function; this composes them and prints one line each. It is
a report, never a repair: a check that fails prints the command that fixes it.

Checks, in the order a broken journal breaks:

1. **integrity** — `PRAGMA quick_check`. FAIL beats every other line: on a
   corrupt file the rest of the output is fiction.
2. **balance** — both §6 identities via `ingest.deals.verify`. No account row
   yet → WARN "run `journal sync`", not a crash.
3. **trades** — is `trades` still the derivation of `deals_raw` it claims to
   be? Two questions, because the first misses the second: a `position_id`
   (Trap-1 whitelist: BUY/SELL, non-zero `position_id`) with no row in `trades`
   at all, and a trade deal whose `ingested_at` is newer than
   `MAX(trades.rebuilt_at)` — a partial close or re-synced OUT that landed on a
   position already reconstructed. Either → WARN "run `journal rebuild`". This
   is the gap with no detector today. Known ceiling: an SL/TP snapshot from the
   M4 poller also makes `trades` stale and moves neither watermark.
4. **backup** — age of the newest `backups/journal-*.db`. Older than 24 h or
   absent → WARN. Reuses `backup.auto_dir` / the same mtime rule as
   `backup.due`, so `status` and the `live` timer can never disagree.
5. **live** — `live_heartbeat` age. Never beaten → INFO (a journal with no
   daemon is a normal journal). Beaten but older than 60 s → WARN "`journal
   live` is not running". Plus `trade_commands` still `pending`.

## Rules

- **Never a repair, never a write.** A command called `status` that fixes things
  is the trap `verify` already refused (`cli.verify`: "a side effect inside a
  command called verify is the trap").
- **WARN is not FAIL.** Exit code 1 only for a check that means the data is
  wrong (integrity, balance). Stale backup, unrebuilt trades, no daemon: exit 0
  with the fix printed. A status command that exits non-zero for "you have not
  backed up today" gets `|| true`'d and then ignored.
- **No new detector logic.** Every number comes from a function that already
  ships. If a check needs new logic it belongs in the module that owns it, not
  in a status command.
- `frontend/dist` staleness is deliberately NOT here: `journal serve` already
  warns at the moment it matters, and a second copy is a second thing to drift.

## Shape

`store/health.py`:

```python
@dataclass(frozen=True)
class Check:
    name: str            # "integrity" | "balance" | ...
    state: str           # "ok" | "warn" | "fail"
    detail: str          # human sentence, always carries its numbers
    fix: str | None      # the command to run, or None

def checks(conn, db_path, *, now=None) -> list[Check]
```

Pure, deterministic given `(conn, db_path, now)`; `cli.status` only formats and
picks the exit code. Tests drive `checks()` directly plus one CliRunner pass for
the exit codes.
