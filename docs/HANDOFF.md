# Handoff — read this first

> **Update 2026-07-24 (Phase 5 cutover):** the web UI is now the React SPA
> served at `/`; the Jinja2 templates, `/static/app.css`, the form-POST write
> routes, and the `jinja2`/`python-multipart` deps have been retired. `journal
> serve` and the loopback/WAL coexistence notes below are unchanged.

## YOUR STANDING INSTRUCTIONS

You sit in the **architect / reviewer** seat on mt5-journal. Not the implementer.

- **You own:** `docs/`, analysis scripts, reviewing Claude Code's plans and diffs.
- **Claude Code owns:** `src/`, `tests/`, `pyproject.toml`. **Do not touch them.**
- **`schema.sql` lives in the repo and the repo is canonical.** The reviewer
  proposes schema changes in review; Claude Code applies them. The reviewer keeps
  no working copy — a fork you cannot see is a fork you will review against by
  mistake. It already happened once.
- **Your value comes from not sharing Claude Code's context.** Every real bug
  found so far was caught because a second reader with no stake in the code
  looked at it cold. Write `src/` and you inherit its blind spots; the review
  loop collapses into two agents agreeing with each other.
- **The design documents are the least reliable source in this project** — they
  have been wrong three times, this file included. The bridge, the fixtures, the
  account, and the broker's own report are authoritative. When they disagree with
  a doc, the doc is wrong: patch it, and record what was measured.
- **Never write trading signals, entry/exit logic, or position advice.** This
  tool describes patterns in past data. That is all it does.
- Read `CLAUDE.md` and `docs/mt5-deal-model.md` before acting on anything they
  cover. They are dense and load-bearing.

**This file holds only what lives nowhere else: current state, seats, roadmap,
error log.** It does not restate account facts, traps, or schema — those have one
home each, and a second copy is a future lie. Point, never duplicate.

---

## CURRENT STATE — update this section every session

**Last updated:** 2026-08-13

**2026-08-13 — `journal live` no longer dies when the bridge blinks
(`live_loop`).** Every expensive thing in this project now lives inside that one
loop: the SL/TP snapshots that Trap 16 makes unrecoverable, the daily backup,
the beacon, the command queue. And the loop had exactly one exception handler
inside it — around the ingest pipeline, with a comment saying *"a failed ingest
must NOT kill the loop — losing the loop loses unrecoverable live SL history."*
Everything else in the cycle was unguarded, starting with `positions_get()` on
its first line. A Docker restart of the MT5 bridge, or any second where
`localhost:8001` refuses a connection, killed the process — and nothing restarts
it. The failure mode is silent by construction: the human is not watching a
terminal at 02:00, and the next morning the journal has a hole in it.

`live_cycle` is now wrapped in the loop: rollback, count, log, sleep, retry. Two
decisions carry the weight:

- **Roll back before sleeping.** The connection uses SQLite's implicit
  transactions, so a cycle that raised after a write holds the WAL writer slot
  until something ends it. Carrying that across a 5 s sleep starves `journal
  serve`'s enqueue past its `busy_timeout` — the exact bug already paid for
  twice (`deals.sync`, `candle_fill.fill_range`). It is one line and it is the
  reason the retry is safe at all.
- **`database is locked` still escapes.** Past the 5 s `busy_timeout` that
  message means a SECOND `journal live` on this DB, which is a configuration
  error `cli.live` already prints a plain-language exit for. Retrying it every
  five seconds forever would bury that message under a spinning process. It is
  the one exception the loop refuses to absorb.

Nothing beats the heartbeat on the failure path, deliberately: the process is up
but cannot see the broker, and a beat would tell `/live` and `journal status`
that all is well while the position mirror sits frozen. "live down" is the more
honest of the two available readings. Consecutive failures log once at the top
of a streak (with traceback) and again every 60th, so a night with a dead bridge
costs one screen of log rather than 17,000 lines; the recovery line names the
streak length. `LiveLoopReport.failed_cycles` and one `journal live` summary line
make a run whose every cycle raised stop reading as "cycles: 720", healthy.

`poll_loop` (M4, `journal poll`) has the same unguarded shape and was left alone
on purpose: it is a FOREGROUND command a human watches, so its traceback lands
in front of the person who can restart it. `journal live` is the one that runs
unattended.

Gates: `uv run pytest` **805 passed, 1 skipped in 40.16 s** (+5: bridge
recovery, no-dangling-transaction, `--once`, backup-while-down, and the lock
escape). `journal rebuild` on a fresh 62 MB snapshot of the live store: **OK,
129 trades**. `test_repo_hygiene.py` re-run with `data/` symlinked in: 3/3.
**Needs a `journal live` RESTART to take effect** — the running daemon is the
old code.

**2026-08-13 — the gates now run somewhere other than this laptop
(`.github/workflows/ci.yml`), and the public repo finally has a front page
(`README.md`).** § Definition of done says a task is finished when `pytest`
passes and the output is pasted. That has held — because a human and an agent
remembered every time, on one machine, in one checkout. Nothing enforced it,
and two of the last three months' bugs were environment-shaped rather than
logic-shaped: a bundle that had never been rebuilt (`ab8431c`), and a
dependency question ("is `httpx` actually declared?") that could only be
settled by hand-building a fresh venv. Both are exactly what a cold checkout
answers for free.

Two jobs, both running commands that already exist — nothing new to learn, and
nothing that can drift from what the human types:

- **python**, on `macos-latest`: `uv sync --locked` then `uv run pytest -q`.
  `--locked` is the point of the first step, not a detail — it fails when
  `uv.lock` and `pyproject.toml` have drifted, which is the dependency question
  above, asked automatically. arm64 macOS rather than a cheaper Linux runner
  because a Linux job would be proving a build of lightgbm/pandas nobody here
  ever executes.
- **frontend**, on `ubuntu-latest`: `npm ci`, `npm test`, `npm run build`. The
  build is `tsc -b && vite build`, so it is the type check too — one step, not
  two. It compiles into a `dist` that is thrown away with the runner: CI proves
  the bundle BUILDS, it never ships one, and `journal serve`'s stale-dist
  warning remains the thing that notices a real `frontend/dist` behind its
  sources.

No MT5 anywhere in it, and that is hard rule 1 paying for itself: every import
of the terminal sits behind `adapter/`, so all 800 tests run on a machine that
has never seen a broker. `data/` is absent from a checkout (rule 10), which
also decides what `tests/test_repo_hygiene.py` does there: its DB-derived layer
skips and the tracked-file scan runs — the layer that actually matters on a
**public** repository.

The README is the same rule 10 problem in a different shape: the repo is
public, was a bare file list, and the first thing a reader met was
`CLAUDE.md`'s account section. It now opens with what this is, why it exists
(Trap 16, stated plainly), the descriptive-not-predictive boundary, and the
quick start — and carries no login, broker, server or funding reference.
Verified rather than assumed: `test_repo_hygiene.py` was run with the live
`data/journal.db` symlinked into the worktree, so the layer with teeth
(identifiers read out of the real DB) ran against both new files and passed
3/3.

Gates: `uv run pytest` **800 passed, 1 skipped in 47.02 s** (unchanged — this
adds no source), `npm test` **346 passed**, `npm run build` clean, `uv sync
--locked` clean. Every CI command was run locally first, in a fresh worktree
with a fresh `npm ci`.

**And the first run found something in 47 seconds, which is the whole
argument.** `frontend` passed. `python` came back **15 failed, 779 passed, 6
errors**, and every one of them was one line: `OSError: dlopen(...
lib_lightgbm.dylib): Library not loaded: @rpath/libomp.dylib`. LightGBM's macOS
wheel does not ship OpenMP; it dlopens Homebrew's, from
`/opt/homebrew/opt/libomp/lib`. **This machine has `libomp` from some earlier
install and nothing in this repository ever recorded that** — so `lab/` could
not import at all on a Mac that has never run `brew install libomp`, and the
only way to discover it was a restore, a second Mac, or this. The workflow now
installs it before `uv sync`, and the README's quick start names it as the one
macOS prerequisite. Not a test bug and not worth pinning with a test: it is an
environment fact, and CI is the thing that keeps asking about it.

**2026-08-13 — a queued command now has a shelf life
(`execute.expire_stale`).** `journal status` learned this morning to *report* a
`trade_commands` row queued with nothing running to send it. Nothing ever
*retracted* one. A `pending` row is a promise the UI keeps making — `/live`
shows it queued and the human reads that as "the SL is on its way" — and with
`journal live` down, or up with `--no-trading`, it sat there indefinitely. Both
ways that ends are bad:

- the human walks away believing a stop is attached to a real position;
- `journal live` starts hours later and **sends it**. `enqueue` validates once,
  at queue time: `price_ref`, the stop distance the lot was derived from, and
  `_check_feed_fresh`'s verdict were all measured against a market that no
  longer exists. That verdict has a shelf life and now has an expiry to match.

`expire_stale(conn, login, max_age_s=STALE_PENDING_S)` marks `pending` rows
older than **300 s** `rejected`, with an error saying why and that nothing was
sent. It runs in `live_cycle` step 6, **before** the claim — so a stale row can
never be the one that cycle sends — and it runs with `trading` off too, that
being the one mode in which nothing else ever clears the row. `retcode` stays
NULL: the broker never saw it.

Three boundaries worth keeping:

- **`pending` only.** `claimed`/`sent` stay `recover_interrupted`'s, and the
  distinction is the whole design: a `sent` row MAY already exist at the broker,
  so closing it out on a timer — with no human reading the message — would
  invent an outcome for a real order. A `pending` one provably never left.
- **300 s is deliberately longer than a `journal live` restart**, which is the
  one routine reason a fresh row waits. A restart must not cost the human the
  command they just queued. Calibration knob on `STALE_PENDING_S`.
- **Count before UPDATE.** This runs every cycle and the answer is almost always
  zero; an UPDATE matching no rows still takes the WAL writer slot, and this
  project has twice paid for holding that slot for nothing (`deals.sync`,
  `candle_fill.fill_range`).

Side effect, and the reason the `ponytail:` note on `_maybe_backup` is gone: a
forgotten `pending` row used to defer **every daily snapshot** for as long as it
sat there (the backup steps aside for anything pending). That deferral is now
bounded by minutes instead of by how long a human takes to notice.

No frontend change — `CommandsTable` already renders `rejected` and prints the
`error` column, so the audit log explains itself. Gates: `uv run pytest`
**800 passed, 1 skipped** (+6: 4 on `expire_stale`, 2 on the cycle wiring).

**2026-08-13 — `journal restore`: the half of `backup` that runs on the worst
day.** Seven snapshots are kept, `journal live` takes one daily — and nothing in
this project read one back. So the procedure on the day it matters (corrupt
store, bad `rebuild`, deleted file) was a human improvising `cp` under stress,
against the one file here that cannot be re-synced (Trap 16). Three ways that
improvisation loses data, all silent, and each is now a refusal or a rename in
`backup.restore()`:

- **The old `-wal`/`-shm` left beside the new file.** SQLite can recover the
  *previous* database's WAL frames into the *restored* one. They move with it.
- **`journal live` still holding the old file open.** It keeps committing into
  the file that was replaced and the store forks in two, newest trades landing
  in the copy nobody reads. A heartbeat inside `health.HEARTBEAT_MAX_AGE_S`
  refuses the whole command. The check is best effort and reads through a
  **read-only URI** — never `db.connect()`, which would create and migrate the
  file it is about to replace — and a target too damaged to answer proceeds,
  because that is exactly the file this command exists to repair.
- **A snapshot nobody read back.** The source is `integrity_check`ed and counted
  *before* anything on disk is touched; a bad source leaves the target untouched.

The database being replaced is **moved aside, never deleted**
(`journal-replaced-<UTC>.db`, beside the store): it may hold deals the snapshot
predates, and Trap 16 means `sync` cannot always get them back. If the copy
itself throws, the moved files are renamed back — the human is never left with
neither file where they left it. Whole-file replacement only, no merge:
`ponytail:` noted on `restore()`.

`journal restore [--from PATH] [--yes]` defaults to the newest auto-named
snapshot **by mtime** — deliberately the same file `backup.due()` measures and
`status` prints, so "the backup you have" and "the backup restore picks" can
never be two different files. It confirms before replacing unless `--yes`.
`status`'s integrity FAIL now points at it (`journal backup --dest rescue.db &&
journal restore`), which is the first time that check has had an actual recovery
path to name.

**Found while testing it, on real data: `journal status` CRASHED on a corrupt
store.** A 62 MB copy of the live DB with 20 kB scribbled over it printed a
`sqlite3.DatabaseError` traceback out of `_balance` and not one line of the
report — the command whose entire purpose is answering "is this journal
healthy?" was mute in the one state where the answer is no. `_integrity` had
already detected it; the next check simply died before anything reached the
terminal. The guard is one `except sqlite3.DatabaseError` around each check
inside `health.checks()` — at the choke point, not in five checks, because the
next check to touch a bad page has not been written yet — turning the reader
into a `fail` line pointing at `journal restore`. Pinned by a test that corrupts
`deals_raw`'s own b-tree root, located through `sqlite_master.rootpage` so a
schema change cannot quietly un-corrupt the fixture.

Verified end to end against a 62 MB copy of the live store (never the store
itself): corrupt it → `status` prints 3 FAILs and exits 1 → `restore` puts the
snapshot back, keeps `journal-replaced-*.db` → `status` all-`ok` → `journal
rebuild` OK, 129 trades. The live-heartbeat refusal was exercised against the
running daemon: a fresh copy of the live DB refuses the restore by name.

Gates: `uv run pytest` **794 passed, 1 skipped** (+17: 12 on `restore()`, 4 on
the command, 1 on the crash above). Spec: `docs/plans/journal-restore.md`.

**2026-08-13 — `journal status`: one bridge-free answer to "is this journal
healthy?"** Every silent failure this project has actually had already had a
detector — in a *different* command, run at a *different* moment.
`integrity_check` only runs when a backup is taken. The §6 identities only run
when a human types `journal verify`. `backup.due()` only runs inside the
`journal live` loop. And two failures had no command at all: deals synced but
never reconstructed, and a `trade_commands` row queued with nothing running to
send it — the second one means the human believes an SL is in flight while it
sits in a table. Knowing *which* question to ask is the part a human gets
wrong, so `journal status` asks all five at once:

```
== status: data/journal.db ==
[ok  ] integrity  quick_check ok
[ok  ] balance    identity 1 PASS, identity 2 PASS (129 trades)
[ok  ] trades     129 trades, every raw position reconstructed
[ok  ] backup     journal-20260812T140045Z.db, 9.2h old (1 kept)
[ok  ] live       heartbeat 1s ago
```

`store/health.py` composes; it does not detect. Every number comes from a
function that already ships (`ingest.deals.verify`, `backup.auto_dir`,
`live_store.read_heartbeat`, Trap 1's own BUY/SELL whitelist), so `status` and
the command that owns a check can never disagree about the answer.

Three decisions worth keeping:

- **WARN is not FAIL, and only FAIL sets the exit code.** "Wrong" (corrupt
  file, money that does not add up) exits 1. "Undone" (overdue backup,
  unrebuilt trades, no daemon) exits 0 with the fix printed. A status command
  that fails the moment nobody backed up today is one that gets `|| true`'d and
  then ignored.
- **A missing DB is a FAIL, not an empty store.** `connect()` creates the file
  it opens, and a fresh store passes almost every check — the most confident
  possible answer to a typo'd path. `status` refuses to create anything.
- **No repairs and no writes.** Same trap `cli.verify` already refused: a side
  effect inside a command named for looking is how you learn it was not just
  looking. `frontend/dist` staleness is deliberately NOT here either — `journal
  serve` warns at the moment it matters, and a second copy is a second thing to
  drift.

The `trades` check is the only one with logic of its own, and it asks TWO
questions because the first misses the second: a position with no trade row at
all (never rebuilt), and a position whose row exists but predates a deal that
has since landed on it (a partial close, a re-synced OUT) — caught by comparing
`MAX(deals_raw.ingested_at)` over BUY/SELL deals against `MAX(trades.rebuilt_at)`.
Measured on the live DB before it shipped: rebuilt_at leads the newest trade
deal by 6.2 h, so the check reads `ok` there rather than nagging forever. Its
known ceiling: an SL/TP snapshot arriving from the M4 poller also makes `trades`
stale and moves neither watermark.

Gates: `uv run pytest` **777 passed, 1 skipped in 19.73 s** (was 755; +23 new —
20 on `health.checks()` directly, 3 on the CLI's exit codes). Run against the
live 62 MB DB with `journal live` up: the output above, exit 0. `journal
rebuild` on a fresh snapshot of it: **OK, 129 trades**; `status` on that
rebuilt snapshot exercises the WARN path (no backups folder beside it) and
still exits 0. Spec: `docs/plans/journal-status.md`.

**2026-08-13 — `journal serve` now says when it is serving a stale bundle, and
it was serving one.** The SPA is read off disk (`frontend/dist`, gitignored) and
nothing in this project builds it. So a merged frontend fix lands in `main`,
`pytest`/`vitest` are green, the page renders — and the browser keeps running
the old JavaScript until someone remembers `npm --prefix frontend run build`.
There is no symptom other than "the fix did not work", which is why the debugging
starts on the Python side. It has cost this project twice already (the `POST 405`
against a route the bundle predated; the replay-anchor session).

It was true again while writing this: `stale_dist_reason` was pointed at the real
checkout and answered `frontend/dist is 1 file(s) behind the source (newest:
src/lib/candles.ts)` — i.e. the running dashboard did **not** have the
2026-08-12 browser/server parity fix (`29afa88`) it was merged and pushed with.
`frontend/dist` has been rebuilt as part of this change; a running `journal
serve` picks that up on reload, no restart needed.

`web.app.stale_dist_reason(frontend=None) -> str | None` compares
`dist/index.html`'s mtime against `frontend/src/**` plus the four root build
inputs (`index.html`, `package.json`, `vite.config.ts`, `tailwind.config.js`),
and `cli.serve` prints it to stderr **before** uvicorn takes the terminal.
Three choices worth keeping:

- **Warning, never a refusal.** An old bundle is still a working dashboard, and
  a `serve` that refuses to start because npm was not run is worse than the bug.
- **mtimes, not hashes or a build-info file.** The check has to be cheaper than
  the mistake. The known miss is a clock-skewed or freshly-checked-out tree
  reading as fresh; the cost of that miss is the warning, never correctness.
- **`*.test.*` is excluded.** vitest files never reach the bundle, and a guard
  that nags after every frontend test edit is a guard that gets ignored.

Gates: `uv run pytest` **754 passed, 1 skipped in 37.17 s** (was 748; +6 new, all
mtime cases built in `tmp_path` — fresh, missing, one behind, newest-of-many,
a root build input, and the test-file exclusion). Frontend sources untouched;
`dist` rebuilt from them.

**2026-08-13 — `journal live` now takes the backup nobody remembers to take.**
`journal backup` (below) made a correct snapshot *possible* a day earlier. It
did not make one *happen*: it is a foreground command a human types. This file
records three ad-hoc snapshots in a year, and `data/backups/` held exactly one
file — dated the day the command was written. That is the measured rate at
which humans remember, against the one file in this project that cannot be
re-synced (Trap 16).

So the snapshot logic moved out of `cli.backup` into `store/backup.py`
(`snapshot()`, `due()`, `BackupError`) with no behaviour change, and
`live_loop` became its second caller — one choke point, two callers, not two
implementations that drift. `journal live` is the only long-lived process here,
so it is the only thing that can carry a timer.

Four rules, each of which is a way an automatic backup goes wrong:

- **The due-check is stateless.** "Due" = the newest `backups/journal-*.db` is
  older than 24 h by mtime, or there is none. No table, no column, no daemon
  state to drift — and a `journal live` restarted six times a day still backs
  up once a day. Hand-named files (`--dest`) do not count as the module's
  record of itself, so a manual copy elsewhere cannot silently suppress the
  automatic one.
- **Never in front of a trade.** Skipped entirely while a command is pending.
  The copy runs in the loop thread; an SL/TP or a close must not queue behind
  60 MB of pager copy.
- **Never fatal.** Any failure is logged and the loop continues. A failed
  backup is bad; a `journal live` that exits because a disk filled is worse.
- **Pruning refuses to run behind a bad snapshot.** `--keep` deletes the oldest
  auto-named files only when the new one passed its own `integrity_check` —
  otherwise the files it would delete may be the last good ones. (The old
  in-command version pruned first and *then* printed "do not delete anything".)

`--no-auto-backup` turns it off. `--keep` is not exposed on `live`; the daemon
keeps 7 (≈434 MB at today's 62 MB), and `journal backup --keep N` re-prunes the
same folder if that is ever wrong.

Gates: `uv run pytest` **748 passed, 1 skipped in 36.83 s** (was 737; +12 new,
and `test_live_account_identifiers_absent` skips in a worktree — no `data/`).
Frontend untouched. `journal backup` run against the live 62 MB DB with
`journal live` and `journal serve` both up: `integrity: ok`, 264 raw deals,
129 trades; `journal rebuild` on that snapshot **OK, 129 trades**.

**Not verified by a run:** the daily timer firing inside a real `journal live`
— it fires 24 h after the last snapshot, so the first real one lands a day
after the next restart. Spec: `docs/plans/auto-backup-in-live.md`.

**2026-08-12 — `origin` is a public GitHub repository, and four tracked files
carried this account's real identifiers.** Rule 10 has said "never commit
anything containing a real account login" since M0. It was enforced by people
remembering it, which is why it had already failed once (`7464753`, a funding
reference in `tests/fixtures/deals.json`) and then failed again in the document
*describing* that leak.

Found and scrubbed:

| File | Carried |
|---|---|
| `docs/plans/cleanup-2026-07.md` | a funding reference, quoted in full |
| `docs/HANDOFF.md` (open questions) | two funding-reference prefixes |
| `tests/test_execute.py`, `tests/test_live.py` | `_LOGIN = <the real login>` |
| `docs/mt5-deal-model.md` | the broker name and the live server name |

The last two rows were **not** found by reading — they were found by the guard
this entry is really about. `tests/test_repo_hygiene.py`, three tests, no new
dependency:

- **`test_no_funding_reference_pattern`** knows the *shape* of a funding
  reference (direction letter, scheme, currency, digits) and scans every
  tracked text file. Needs no database, so it also protects a fresh clone.
- **`test_fixture_account_is_sanitised`** pins `login == 0` and
  `company`/`server`/`name == "REDACTED"` in `tests/fixtures/account.json` —
  the sanitiser contract `scripts/record_fixtures.py` is supposed to honour.
- **`test_live_account_identifiers_absent`** is the one with teeth. It reads
  the login, broker and server out of `accounts`, and every funding
  `comment`/`external_id` out of `deals_raw`, from the **untracked**
  `data/journal.db`, and asserts none of them appears in a tracked file. It
  cannot be fooled by a leak in a shape nobody predicted, and it is why no real
  value is written down in the test. It `skip`s when there is no database (a
  clone, a worktree, CI) and opens read-only *without* `immutable=1` — `journal
  live` may be writing, and immutable would tell SQLite to ignore its WAL.

Two details that are deliberate. Comparisons use token boundaries
(`(?<![0-9A-Za-z])…`), or a 9-digit login would match inside a hex hash in
`uv.lock` and fail for nothing. And the failure message prints
`<9-char identifier>: <paths>`, never the value — pytest echoes the compared
expression, so what is compared must already be redacted; the paths are enough
to fix it.

Non-vacuity checked one at a time: a fake reference appended to a doc, the
fixture login set to `12345`, and the real login (read from the DB, never typed)
appended to a doc — each failed exactly its own test with the other two green,
and each perturbation was reverted.

**Still open, and only a human can close it:** all of this is the *working
tree*. The identifiers remain in published git history; removing them is a
force-push over public commits. See OPEN QUESTIONS.

Gates: `uv run pytest` **737 passed in 20.55 s** (was 734; 3 new). Frontend
untouched — no vitest/tsc/build. `journal rebuild` **OK, 129 trades** against a
`journal backup` snapshot of the live DB (`journal live`/`serve` were up).

**2026-08-12 — `journal backup` exists, because the one file this project
cannot lose had no command that copied it.** Trap 16 is the reason this journal
exists: the broker deletes its own deal history. That makes `data/journal.db`
the only surviving copy of most of what is in it — a lost file cannot be
re-synced, because the deals are no longer on the server. Until now the only
backups were the ad-hoc `sqlite3 .backup` snapshots taken by hand when someone
happened to remember (this file records three of them), and the obvious
alternative is wrong: `cp data/journal.db` can hand you a file whose newest
commits still live in the `-wal` it did not copy.

`journal backup` is SQLite's own online backup API (`sqlite3.Connection.backup`,
stdlib) and nothing else. It copies through the pager, so WAL content comes
along, and it restarts itself if a writer commits mid-copy — which is why it is
safe to run with `journal live` and `journal serve` up, and why it needs no
bridge. Output is one self-contained file, no `-wal`/`-shm` sidecars to keep
with it: `<db dir>/backups/journal-<UTC>.db`, or `--dest` for somewhere else.

Three guards, each of which is a way a backup silently is not one:

- **Missing source refuses.** `sqlite3.connect()` CREATES the file it cannot
  find, so a typo'd `--db` would have snapshotted a brand-new empty database
  and printed success.
- **Existing destination refuses.** Backing up ONTO a file is data loss in the
  one command whose whole purpose is not losing data.
- **The copy is read back.** `PRAGMA integrity_check` plus the `deals_raw` and
  `trades` counts, printed. A backup nobody has opened is a guess.

`--keep 7` prunes older snapshots, oldest first, and only ever touches files it
named itself (`journal-*.db` in the auto directory); `--dest` snapshots and
anything else in that directory are never pruned. Timestamps are fixed-width
UTC, so name order is chronological order — no stat calls. `--keep 0` keeps
everything. It uses plain `sqlite3`, not `store.db.connect()`, deliberately: a
backup must not migrate the schema of the thing it is preserving.

Verified end to end against the real 62 MB database with `journal live` and
`journal serve` running: the snapshot took `integrity: ok`, 264 raw deals /
129 trades, and then `journal rebuild` on the SNAPSHOT gave 129 trades and
`journal verify` **PASS on both identities** — i.e. the backup is a working
database, not just a file of the right size. Gates: `uv run pytest` **734
passed in 47.98 s** (was 728; 6 new CLI tests covering the snapshot round trip,
both refusals, and both prune branches). Frontend untouched.

**2026-08-12 — `uv run pytest` went from ~4–5 minutes to 29 seconds, and the
cause was LightGBM spawning threads it could not use.** The rule "tests pass
before any commit" is only obeyed if running them is cheap; at five minutes it
was starting to be skipped for "obviously safe" diffs, which is exactly when
this project has been bitten before.

Nothing was mocked and no test was weakened. `--durations` said the whole
suite was the lab: 19 tests at 13–27 s each, everything else under 0.15 s. A
`cProfile` of one `train_all` on the fixture the tests use (1200 bars, 3 folds)
put **9.26 s of 10.5 s inside `lightgbm/basic.py:update`** — 8 boosted fits at
1.17 s each. The wall time was thread thrash, not work: the same three lab
files spent **590 s of system time** against 138 s of user time.

`_new_estimator` now picks the thread count from the row count
(`train.SINGLE_THREAD_ROWS = 100_000`, table of measurements in the docstring
there). Under it, `n_jobs=1`; at or over it, `-1` as before. Measured on this
machine (10 cores, 15 features, 200 trees): 5k rows 0.63 s → 0.11 s, 100k rows
0.89 s → 0.75 s, and the lines cross just past that — 800k rows is 2.75 s
threaded against 4.64 s single. A dataset is two rows per bar, so the fast side
is "fewer than ~50k bars": every fit the suite does, and any hand-run training
on a normal window. A full-M1 training (715k bars stored today) still gets all
ten cores.

This is a speed choice, not a correctness one, and `test_lab_train.py::
test_small_fits_are_single_threaded_and_big_ones_are_not` pins it on both
sides of the line so it does not get "optimised" back. One caveat worth
knowing: LightGBM's `deterministic=True` reproduces a fit for the *same* thread
count, so a model retrained after this change can differ in the last digits
from one trained before it. Stored artifacts are untouched, and lab output has
always shipped next to its own out-of-sample expectancy — which is the number
that would move if it mattered.

Gates: `uv run pytest` **728 passed in 29.07 s** (was 727 in 226–315 s; 1 new
test). Frontend untouched, so no vitest/tsc/build run. `journal rebuild` **OK,
129 trades** and `journal verify` **PASS on both identities**, both against a
`sqlite3 .backup` snapshot of the live DB — `journal live` and `journal serve`
were running against the real one.

**2026-08-12 — the four constants the browser copies from the server are now
pinned by a test.** The entry directly below ends with "Change one, change the
other" and leaves that to a comment. Nothing enforced it, and the drift it
guards against is silent in exactly the way this project keeps getting bitten
by: the browser arms a button the server refuses, and it is only visible
against a live broker.

`tests/test_frontend_constants.py` reads `frontend/src/lib/candles.ts` **as
text** — no node, no `npm install`, no build — and compares four mirrors:

| TypeScript (`lib/candles.ts`) | Python |
|---|---|
| `FEED_STALE_MS` | `execute.FEED_STALE_MS` |
| `PRICE_REF_STOP_FRACTION` | `execute.PRICE_REF_STOP_FRACTION` |
| `TF_MS` (via `MIN`) | `domain.resample.timeframe_ms` |
| `TIMEFRAMES` | `adapter.base.TIMEFRAMES` |

The first two are the stale-feed pair the entry below created. The last two
are older and were never written down as a pair at all: `candles.ts`'s
`bucketStart` comment already says it "must agree" with
`domain.resample.bucket_start`, and a `TF_MS` that disagreed would compute
windows that do not line up with the stored bar times. `domain/resample`
already `assert`s its own table against `adapter.base.TIMEFRAMES` at import;
this is that same guard extended across the language boundary, which is why it
lives in pytest and not in vitest — the Python values are the ones being
mirrored, so the check belongs on the side that owns them.

The TS is parsed with a deliberately narrow regex + `_num`, which accepts only
the literal forms actually in the file (`15_000`, `0.25`, `240 * MIN`) and
**asserts** on anything else rather than guessing. A mirror check that
silently mis-parses is worse than none; if someone reformats a constant, the
test says "teach `_num` the new form" instead of quietly passing.

Non-vacuity verified: each of the four was perturbed in `candles.ts` in turn
(`15_000`→`16_000`, `0.25`→`0.3`, `M15: 15 * MIN`→`14 * MIN`, `H4` dropped
from `TIMEFRAMES`) and each time exactly its own test failed and the other
nine passed; `candles.ts` was restored from git after each.

No behaviour change: the test, this note, and four comment lines pointing at
the test from the constants themselves (both sides) — a drift guard nobody can
find is a drift guard nobody keeps.

Gates: `uv run pytest` **727 passed** (was 717; 10 new — 5 tests, one of them
parametrised over the 6 timeframes) · vitest **346 passed** (unchanged) · tsc 0
· vite build 0. `journal verify` **PASS on both identities** against the live
DB (read-only). `journal rebuild` **OK, 129 trades** — run against a `sqlite3
.backup` snapshot of the live DB, not the live DB itself, because `journal
live` and `journal serve` were both running against it and a test-plus-comments
diff cannot affect rebuild anyway.

Also closed while here: the OPEN QUESTION claiming `httpx` was missing from
`pyproject.toml` — it is present in `[dependency-groups].dev` and the fresh
worktree venv for this work was created by exactly the clean `uv sync` the
question predicted would break, then ran all 727 tests including
`test_storage_api.py`. Removed from the list below.

**2026-08-12 — the browser and the server now refuse the same opens.** Closes
the cleanup the entry directly below deliberately left out ("the frontend still
gates its button on `staleEntryReason`'s 2 × timeframe while the server's window
is 15 s"). The two rules were never the same rule: `_check_feed_fresh` makes
three checks, `staleEntryReason` mirrored one. So the button armed, the human
clicked, and the open came back 400 from a chart that still looked alive.

Both missing checks are now made in the browser, on the same windows and the
same numbers:

- **The forming row has stopped being refreshed.** The bar itself cannot show
  this — a frozen feed reports the current bucket, unchanged, forever — so
  `live_candle_payload` now carries `forming_updated_msc` (new
  `live_store.forming_updated_msc`; `read_forming`'s signature is untouched, it
  has six callers). `staleEntryReason` reads it against `FEED_STALE_MS`. It is
  the stamp, never the prices: `touch_forming` restamps a quiet bucket without
  moving one, and reading prices here would resurrect exactly the bug the
  2026-08-12 entry below fixed.
- **The sized price did not come from the feed.** The browser already held both
  numbers and never compared them: `plannedEntry` (what POST `/api/live/open`
  sizes from) is `shownCandles.last.c`, while the poll's `forming.c` is what the
  server sees. They are equal in the normal case and part exactly when
  `mergeForming` refuses to append a bar more than one interval ahead — the
  stalled-`/api/candles` case the server's docstring names. Same
  `PRICE_REF_STOP_FRACTION` (0.25 of the stop distance, not an absolute price
  gap).

Why the browser and not a server round trip: `/api/size` is posted on every
drag and its `error` already disarms the button, so routing the checks through
it looked lazier — but a frozen feed stops `entry` changing, so no new sizing
call fires and the last `error: null` stands. The client check re-evaluates on
every 5 s poll (`useApi` sets a fresh object each time), which is the only place
that reads a clock that keeps moving. `/api/size` is also the replay sizing
path; feed checks must never reach it.

`FEED_STALE_MS` and `PRICE_REF_STOP_FRACTION` are therefore duplicated into
`lib/candles.ts`. That is the design the server docstring already assumed
("`lib/candles.staleEntryReason` gates the button in the browser on the same
facts"), now actually true. Change one, change the other.

Gates: `uv run pytest` **717 passed** (1 new in `test_api.py`, plus a new
assertion on the existing null case), vitest **346 passed** (7 new, all written
failing first), tsc 0, vite build 0, `journal rebuild` OK. **Untested in the browser** — the two new branches need a real
frozen feed or a wedged candle fetch to reach. `frontend/dist` is gitignored, so
`npx vite build` must be re-run in the main checkout for this to reach the page.

**2026-08-12 — review fixes on the stale-feed guard. MERGED to `main` and
pushed.** The 2026-08-11 guard below was reviewed and had two real defects; both
are fixed here.

1. **It refused healthy feeds.** `updated_msc` only advances when
   `serve_watches` writes a bar in the CURRENT bucket, and a bucket with no
   ticks in it (EURUSDc outside its session, the seconds right after a
   rollover, a sparse response at the live edge — the repo already has a test
   for sparse) produces no such bar. One quiet minute at M1 froze the stamp on
   a perfectly healthy feed and every open on that symbol was refused 15 s
   later. Fixed at the source: `serve_watches` now calls the new
   `live_store.touch_forming` when the bridge answered but had no current-bucket
   bar, stamping `updated_msc` without touching the prices. An **empty**
   response is deliberately still not stamped — that is the bridge going blind,
   which is exactly what the guard is for.
2. **It never checked that `price_ref` came from the feed.** A moving feed
   proves the server sees prices; it says nothing about the number the browser
   posted — which is what `/api/live/open` sizes the lot from. The failure in
   the guard's own docstring therefore still got through: a wedged
   `/api/candles` fetch leaves `mergeForming` painting the last bar it has while
   the frontend's `staleEntryReason` (2 × timeframe = 30 min at M15) still arms
   the button. `_check_feed_fresh` now compares `price_ref` against the close of
   the freshest actively-watched forming bar and refuses past
   `PRICE_REF_STOP_FRACTION` (0.25) of the stop distance the lot was derived
   from — the drift matters in proportion to that distance, not in absolute
   price. The refusal names both prices and tells the human to reload.

Also: `_check_feed_fresh` moved to run **last** in `enqueue_open`, after
`load_open_context` + `validate`. Running it first made an unknown symbol or a
missing spec report itself as "`journal live` tidak berjalan"; it also means the
stop distance the tolerance is measured against is already known to be real.
`live_store.newest_forming_update` became `newest_forming`, returning
`(updated_msc, close)` from one row so the stamp and the price can never be
paired across different bars.

Not changed *in this fix*: the frontend still gates its button on
`staleEntryReason`'s 2 × timeframe while the server's window is 15 s, so a
wedged tab can still show an armed button and get a 400. That 400 now says
exactly what to do. Unifying the two windows is a real cleanup, deliberately
left out here — **and done in the entry above, later the same day.**

**2026-08-12 — that frontend defect is now FIXED** (see the paragraph directly
below for what it was). `useLiveForming` returns `live: boolean | null`, `null`
meaning "not polling / first poll still in flight", and `staleEntryReason` takes
the tri-state: `null` still blocks the button but says `Status feed belum
diketahui — chart belum polling harga live.` instead of naming the daemon. The
false accusation is reachable exactly when `liveEnabled` is false while
`replayOpen` is still false — the replay CONFIG drawer being open — and in the
first seconds after load, before the first poll answers. Both now say the true
thing. Gates: vitest **339 passed** (1 new, written failing first), tsc 0, vite
build 0, pytest 716. Untested in the browser.

**Separate frontend defect, found while tracing this and NOT fixed** (it is not
the same bug — the wording overlaps, the source does not). The drawings browser
pass below recorded "the risk panel on `/chart` printed ``journal live` tidak
berjalan — harga acuan tidak segar` while `journal live` was in fact running and
the liveness badge read `live · 1s`". That string is the FRONTEND's
(`lib/candles.staleEntryReason`, first branch), reached whenever its `feedLive`
argument is false — and `useLiveForming` returns `live: enabled && !!data.live`,
so a chart that simply is not polling (`liveEnabled` false) reports itself as
"`journal live` tidak berjalan". The server's own message of nearly the same
wording comes from the heartbeat branch, which cannot be false while the badge —
computed from the same heartbeat, same 15 s window — says `live · 1s`. The fix
belongs in `staleEntryReason`: distinguish "not watching" from "no heartbeat".

Gates: `uv run pytest` **716 passed** (was 712; 2 new in `test_execute.py` for
the price-ref comparison, 2 new in `test_live.py` — the quiet bucket, and the
empty response that must still read stale). No "no stop" case is tested because
none exists: `validate` refuses an SL-less open before the guard ever sees it.

**2026-08-11 — server-side stale-feed guard on `open`.** Closes the OPEN
QUESTION at the bottom of this file (2026-08-04 review, answered 2026-08-05 with
"block it" but implemented frontend-only). `execute._check_feed_fresh` now runs
first inside `enqueue_open` — the single choke point every open passes through —
and refuses when either (a) `journal live` has no heartbeat or its last beat is
`FEED_STALE_MS` (15 s, the same window `api.live_status_payload` uses) old, or
(b) an **actively watched** forming bar for that symbol has not been refreshed
inside the same window. New `live_store.newest_forming` does the second
check; it joins `live_candles` to an unexpired `live_watches` row on purpose,
because `live_candles` rows are never pruned — an hour-old row from a chart
that was closed is not a frozen feed, and gating on it would refuse every
legitimate open from `/live`, which mounts no chart at all.

Why the server and not just the panel: `volume` is frozen at enqueue, and the
executor's fresh-tick `_check_level` re-validation catches a stop on the wrong
*side*, never a wrong *size*. A stale `price_ref` therefore does not fail loudly
— it silently resizes the order (0.10 lot / 4035 close / 4030 stop = 50 USC
intended; if the market really sits at 4060, ~300 USC goes out). The browser
gate `lib/candles.staleEntryReason` still arms the button; this is the copy that
guards the row actually being written. No frontend change was needed —
`useLiveCommand.confirm` already surfaces a 400's `error` in the panel.

Deliberately NOT gated: `/api/live/open/preview`. Preview writes nothing, and a
direct poster is refused at enqueue regardless; the cost is that a hand-rolled
POST sees the refusal one step later than a risk-ceiling refusal would.

Gates: `uv run pytest -q` **712 passed** (was 705; 6 new in `test_execute.py`
covering both refusals plus the three cases that must NOT refuse — expired
watch, another symbol's frozen feed, fresh feed — and 1 new HTTP-boundary test
in `test_web.py`). `journal verify` PASS on both identities.

**2026-08-10 — chart drawing tools: built, whole-branch reviewed, fix wave
applied. MERGED to `main` and pushed (`d8e253c`); NOT yet human-verified.** Four TradingView-style
drawing tools (trendline, hline, rectangle, text note) on `/chart` in both
normal and replay mode, read-only on `/trades/:id/view`, absent from `/lab`.
Built over 11 tasks on `worktree-chart-drawings` per
`docs/superpowers/plans/2026-08-10-chart-drawing-tools.md` /
`docs/superpowers/specs/2026-08-10-chart-drawing-tools-design.md`. New:
`frontend/src/lib/drawings.ts` (pure geometry/parsing/reducer, no React, no
chart API), `hooks/useDrawingGesture.ts`, `hooks/useDrawings.ts` (debounced
write-through, DB-only — no localStorage mirror), `components/
DrawingOverlay.tsx`, `components/DrawingPalette.tsx`,
`components/TextDrawingInput.tsx`; `GET/PUT /api/drawings` +
`prefs_store.get_drawings/set_drawings` (per-symbol key, or per-replay-session
when `session_id` is given — `drawings_key` normalises the symbol
server-side, rule 11). No migration, no new table, no new dependency.

A whole-branch code review found 3 IMPORTANT + 7 MINOR findings; all were
fixed in the same session (single fix wave, no second pass):

- **IMPORTANT 1** — replay-session startup briefly read AND wrote the LIVE
  drawings key. `replay.start(cfg)` is an async POST; `setReplayOpen(true)`
  lands before the response assigns `replay.session`, and the plan's own
  `useDrawings(symbol, drawingSession, true)` line (verbatim, not a slip)
  didn't account for that window — during it, `replayOpen` was `true` but
  `replay.session` was still `null`, so the fallback `?? null` selected the
  live per-symbol key, rendered it editable, and would have persisted an
  in-window edit to the live key. Fixed by gating the hook itself
  (`drawingsReady = !replayOpen || replay.session != null`), used both as
  `useDrawings`'s `enabled` and as `drawingsProp.editable`. The plan doc is
  annotated SUPERSEDED at that line rather than silently rewritten.
- **IMPORTANT 2** — a pending debounced PUT was dropped on unmount (only the
  symbol-switch case flushed; unmount, the more common exit, just cleared the
  timer). Now flushed in the unmount cleanup, with `keepalive: true`.
- **IMPORTANT 3** — dragging/wheeling the right price axis rescales
  `priceToCoordinate` without touching the logical range, so drawings sat at a
  stale y until something else forced a re-render. Fixed with `pointerup`/
  `wheel` listeners on the pane node feeding the existing `bumpProjection`
  dispatch.
- Minors: a non-ok PUT response was silently swallowed (now warns); `apply()`
  ran a side effect inside a `setItems` updater (StrictMode hazard — hoisted
  out, matching the discipline `useDrawingGesture` already documents);
  re-grabbing an endpoint right after finishing a draw could misfire as a
  measure double-click-hold (CandleChart records every pointerup as a
  potential seed regardless of source — a completed draw/drag now clears it);
  the palette painted over Chart.tsx's loading/gaveup/error banners (moved
  `top-2` → `top-12`); the drawings size cap used `len()` on the dumped JSON —
  verified this is actually a no-op given `json.dumps`'s `ensure_ascii=True`
  default (the string is already pure ASCII, so char-length already equals
  byte-length), kept the explicit `.encode("utf-8")` anyway as
  self-documenting/future-proof, and corrected the test that had wrongly
  claimed an under-count bug; the spec's `PUT` response shape (`{ saved_ms }`)
  didn't match the shipped one (`{ ok, updated_ms }`, matching the sibling
  prefs endpoints) — doc corrected, code kept; the spec's "~25 lines" budget
  for `CandleChart.tsx` was never realistic for what Tasks 9/10 actually wire
  up — doc now states the real growth (726 → 904 over the feature, → 925
  after the fix wave).

Gates after the fix wave: `cd frontend && npx vitest run` **323 passed** (was
316) · `npx tsc -b` silent, 0 errors · `npm run build` clean · `uv run pytest -q`
**705 passed** (was 704). Two first-use defects found after that and fixed in
`d8e253c` — drawing to the right of the newest bar (`toPoint` clamping collapsed
whitespace anchors) and endpoint/corner handles moving the whole object instead
of resizing it (the `hitTest` handle branches were unreachable; rect also gained
c1/c2 handles) — take vitest to **338 passed**. Full command outputs and non-vacuity verification
(each IMPORTANT/MINOR fix reverted, its new test confirmed to fail, then
restored) are in
`.superpowers/sdd/2026-08-10-chart-drawing-tools/final-fix-report.md` on this
worktree.

**2026-08-12 — the browser pass ran and all 8 PENDING HUMAN items PASS; this
feature is closed.** Items 1–3 and 5–8 were driven through Chrome against
`journal serve` + `journal live` on `main` (`3afbb03`), with every result
cross-checked against the persisted blob (`GET /api/drawings`, `app_prefs`)
instead of eyeballed. Item 6 included the IMPORTANT 1 case against a real
`replay.start()` slowed to 6 s by patching `window.fetch`: the drawing made
inside the pending window was discarded, the live key never moved. Item 4
needed a real open position — not something to open on the account for a test —
so the automated pass covered only its hit-test half (a *planned* SL dragged
off a drawing at the identical price); **the human ran the live half and
confirmed it, 2026-08-12**. Per-item results are in the plan file's PENDING
HUMAN section. (This feature was merged and pushed well before this pass — the
earlier "Not merged to `main`" line here was stale.)

Two unrelated observations from that pass, neither drawings-related, neither
chased down: `/trades/:id/view` hangs on "Memuat…" instead of erroring when
the id is not a `position_id`, and the risk panel on `/chart` printed
"`journal live` tidak berjalan — harga acuan tidak segar" while `journal live`
was in fact running and the liveness badge read `live · 1s`.

**2026-08-06 — M10 (the lab) shipped.** `src/journal/lab/` (six modules:
`features.py`, `labels.py`, `evaluate.py`, `train.py`, `store.py`, `score.py` —
`evaluate.py` was split out of `train.py` mid-build so the walk-forward maths
could be tested without fitting anything), migration 010 / `SCHEMA_VERSION =
10` (`lab_models` table), five `/api/lab` routes, and the frontend (`/lab`
page, regime shading + probability strip there, and a badge on `/live`). Two
new dependencies: `scikit-learn`, `lightgbm` (CLAUDE.md rule 8 updated). Rule 9
is rewritten and now scopes prediction to `lab/` alone — everywhere else in
the codebase still only describes past data. Full reference: `docs/lab-models.md`.

Gates run on this branch (worktree `lab-regime-timing`, verbatim in the task-13
report): `uv run pytest` **677 passed**; `cd frontend && npx vitest run` **219
passed** (33 files); `npx tsc --noEmit` silent; `npm run build` succeeds;
`uv run journal rebuild` succeeds and all 8 `lab_models` rows (one regime ×
2 kinds, three regimes × timing × 2 kinds) survived it unchanged.

**A real model has now been trained against real data — this was the first
thing pending and it is no longer pending.** This worktree had no `data/` at
all (fresh checkout, `data/` is gitignored per rule 10), so verifying end to
end required `journal sync` (262 deals) → `journal rebuild` (128 trades) →
`journal candles-warm XAUUSDc H1 --from ... --to ...` (11,816 new H1 bars, ~2
years) → `journal serve` → train, both via a direct `POST /api/lab/train` call
and by clicking Train on `/lab` in a real browser (confirmed rendering: full
metrics table, `n` on every figure, expectancy beside baseline, "just now"
ages). Numbers from the first (curl) run, defaults (`n_bars=24, k_atr=1,
rr=2, er_threshold=0.35`), XAUUSDc H1, `n_bars_read=11816`, no dropped
features, no assumed spread:

- **Regime (lgbm, active):** accuracy 0.728 over n=19,571 test rows (5 purged
  folds). The confusion matrix is heavily `range`-biased — this account's H1
  XAUUSDc history is mostly range by the ER threshold, which is exactly why
  the timing-stage split below matters.
- **Timing / trend_up (lgbm, active):** n_taken=1406/3774, win_rate 73.5%,
  **expectancy +1.19R vs baseline +0.14R**, AUC 0.869. Model clearly adds
  value in this regime.
- **Timing / trend_down (lgbm, active):** n_taken=535/1666, win_rate 74.4%,
  **expectancy +1.22R vs baseline +0.14R**, AUC 0.876. Same story.
- **Timing / range (lgbm, active):** n_taken=926/18104, win_rate 29.7%,
  **expectancy −0.09R vs baseline −0.03R, AUC 0.51 — the model LOSES to
  baseline here.** Recorded exactly as measured, not smoothed over: in the
  regime that dominates this account's H1 history, the timing model is worse
  than entering at random. `/lab`'s "a model is only interesting where it
  beats the baseline" framing is not decoration — this is the case it's
  warning about. Do not activate the range-regime timing model in a workflow
  that treats its probability as informative without re-checking this table
  after every retrain.
- Pooled fallback did NOT trigger — every regime had ≥500 rows
  (`pooled_min_rows` default), so all three trained per-regime as expected.
- `GET /api/lab/score` returned `status: "ok"` for a live 50-bar XAUUSDc H1
  window with real regime/probability output per bar.

`/live`'s badge was not visually confirmed against an open position — there
was no open position and no `journal live` heartbeat running in this worktree,
and opening one is out of scope for a docs task. `LabBadge.tsx`'s rendering
logic (regime label, both probabilities, age with staleness threshold,
out-of-sample expectancy with its `n`, pooled note, and the five status texts)
is covered by 8 green `LabBadge.test.tsx` cases instead; a live-position visual
pass is still a reasonable thing for a human to do opportunistically.

**Previous entry — 2026-08-05:**

**Everything up to and including M9 and the frontend rework is merged,
running, and human-verified as of 2026-08-05 — no pending human run
anywhere at that point.**

**2026-08-05 — nothing is pending a human run any more.** The human confirmed,
in person and against the live bridge, every item this file and the project
memory had been carrying as PENDING HUMAN:

- The on-close ingest freeze is gone (gap-aware `sync_candles` + capped fetches
  + post-ingest beat, and two-phase `deals.sync` with a windowed history pull —
  measured 243 s → 49 s, 124 bridge round trips → 0). Watched on a real close.
- SL/TP drag on `/chart` with the bridge running: the whole click-through,
  including a live order reaching the broker.
- Risk-based auto lot sizing opening a real position with the SL attached from
  the first tick — which also settles the older "an accepted order has never
  landed" note below (AutoTrading is on).
- The live-bar rollover fixes: the stale-`now_msc` one in `serve_watches`
  (backend) and, found the same day, a second FRONTEND cause of the *identical*
  symptom — `useChartData.loadUpTo` fetched from a mid-bucket cursor, so the bar
  that was forming when `/chart` opened could never be returned by
  `time_msc BETWEEN from AND to` and was lost for good (one bar, once per page
  open). Fixed by flooring the forward `from` to the bucket start (`219d95e`).
  **If "the live bar vanishes at rollover" is ever reported again, check BOTH
  layers** — the symptom does not tell you which one it is.

Remember `frontend/dist` is gitignored and is what `journal serve` ships: after
any frontend merge, run `npm run build` in the main checkout or the browser
keeps the old bundle.

**Previous entry — 2026-07-23:**

**M9 in one line (MERGED to main; branch `claude/trading-system-plan-2959b7` since deleted):** the
journal became able to *act*, not just describe. Six phases: (1) a real
migration runner in `store/db.py` + `migrations/002_live_trading.sql` (bumps
`SCHEMA_VERSION=2`, applied automatically by `connect()`); (2) trade ops at the
adapter boundary (`order_check`/`order_send` on the Protocol, new `TradeAction`/
`OrderType`/`OrderFilling`/`TradeRetcode` enums, a scriptable `FakeMT5Client`
write side); (3) a pure command layer (`domain/commands.py` validate/
build_request/classify + `execute.py` enqueue/claim/record) with the human's
1.00-lot hard cap unit-tested; (4) `journal live` — the single process that owns
the bridge: mirrors `open_positions`, **auto-ingests on close** (sync→rebuild→
candles→rebuild, ask 2), and executes queued commands, never auto-retrying a
`sent` order; (5) the web live view + a mandatory two-step confirm before any
order (`/live`, `/live/commands`), with `serve` refusing any non-loopback
`--host`; (6) a frontend redesign — live strip, an inline-SVG equity/cumulative-R
tape, design tokens with light+dark, self-explaining `n/a` cells.

**M9 decisions (human, 2026-07-23):** execution is GO; trading is **ON BY
DEFAULT** (`--no-trading` opts out of command execution; the UI confirm step and
the loopback bind-check are the primary guards, not a flag); **1.00-lot hard cap**
per command, enforced in `domain/commands.py`; a real account is acceptable (no
demo gate). Rule 9 still binds: the human types every number; the system only
validates, sends, and reports the broker's verbatim answer — no suggested SL, no
auto-breakeven, no sizing.

**M9 verification — MEASURED so far:** `uv run pytest` **375 green** (was 202 at
M8's baseline; +173 across the six phases). Boundary greps clean: no
`import MetaTrader5` and no `TRADE_*`/`ORDER_*` value outside `adapter/`; `web/`
imports no adapter. Migration replay test passes (fresh-v2 == migrated-v1→v2).
On the live DB (migrated in place, backup kept): `migrate`→v2, `rebuild`→72/72
mae-mfe, `verify`→**both identities PASS**, residual +0.00, the 14.50 USC archived
reconciliation intact.

**Live smoke — MEASURED 2026-07-23 (real account, real bridge):**
- **Auto-ingest on close (ask 2) — PROVEN.** `journal live --no-trading` running,
  a real XAUUSDc position (#1582918124, 0.01 lot) opened → heartbeat went
  `0 open` → `1 open · 1 SL/TP snapshot(s)`; closed → `closed [1582918124] —
  menjalankan ingest… -> ingested`. `trades` grew 72→82 and `verify` still PASSED
  both identities afterward — the close-triggered pipeline ran and left the DB
  consistent, with no manual command.
- **Web live view — PROVEN.** `/live` rendered the open position live (floating
  P&L −0.90 USC labelled floating, SL/TP shown as `0`=none-set, "data 3s ago").
- **Two real bugs found and fixed by running it live** (regression-tested):
  `database is locked` (connect() now WAL + busy_timeout so live+serve coexist),
  and a silent heartbeat that read as a freeze (per-cycle heartbeat + an
  `on_closing` notice before the blocking ingest). **Footgun learned:** run
  `live` AND `serve` with the SAME absolute `--db`; `serve` without `--db` from
  the worktree makes a stray empty `data/journal.db` and `/live` looks empty.
- **Order SEND path (ask 1) — REACHED THE BROKER, verdict recorded faithfully.**
  A `modify_sltp` (SL 4090, TP left unchanged) typed in `/live` on a real 0.01-lot
  XAUUSDc position went UI → `pending` → claimed → `order_check` → `order_send` →
  **the broker answered**. The loop recorded it `failed`, retcode **10027
  (`TRADE_RETCODE_CLIENT_DISABLES_AT` — "AutoTrading disabled by client")**, with
  the broker comment, and did NOT retry. So the whole plumbing AND the failure
  path are proven; the rejection is a TERMINAL SETTING (the container's MT5 has
  Algo/AutoTrading turned OFF), not a code fault. This also surfaced and fixed a
  real honesty bug: the audit log rendered a left-unchanged `TP` (NULL) as
  "unknown"; it now reads "(tetap)" via a shared `format.level_word` — a modify's
  NULL level is a deliberate "leave it", not ignorance (rule 4).
- ~~**STILL NOT measured:** a `done` order that actually LANDS~~ — **MEASURED
  2026-08-05.** The risk-based auto-lot-sizing live pass (its section below)
  opened a real position with the SL attached from the first tick, so AutoTrading
  is on and an accepted order landing is proven. The browser visual/contrast pass
  of the redesign is confirmed too.

M9 is now *live-verified for ingest, the read/observe surface, and the full
order-send plumbing up to the broker's verdict; a successful (accepted) order has
not landed yet only because the terminal's AutoTrading is off.*

**CORRECTION (2026-07-25) — kill a stale-doc misunderstanding.** Earlier phrasings
(this file's own status table, and handoffs that quoted it) said the "M9 live
smoke is pending a human run," which later work read as "the live/bridge path is
unproven." That is wrong and has been for a long time. The live round trip —
`journal live` owning the bridge, mirroring `open_positions`, serving `/api/live`,
and the browser UI reading it — WORKS and was measured 2026-07-23 (above). The web
layer never touches the bridge directly (rule 1 / M9 boundary); it goes through the
`journal live` process + the command/candle queues, and that whole path is proven.
Both items that section once listed as pending — (1) an *accepted* order actually
changing the SL in MT5, (2) a browser visual/contrast pass of the SPA — were
**CONFIRMED by the human 2026-08-05**. M9 has no pending human verification left.
Chart Phase B's live-position overlay consumes the same proven `/api/live` data
path — its "positive path" is verifiable exactly the way `/live` already is; only
the chart-specific line rendering is new frontend, not a bridge concern.

**Done:** M0 (adapter + store + doctor) · M0.1 (Candle→ms, enums probed from the
bridge) · M0.2 (fixtures re-recorded with `comment` preserved, `a15cc5e`) ·
M1 + M1.1 + M1.2 (ingest, archive detector, bridge-free `verify`, reconcile,
`equity` modelled — `1d086c2` / `10d9141`) · M2 + M2.1 (`reconstruct.py`:
deals → trades, `journal rebuild`, `journal verify` §6 identity 2 — 55 tests
green, `48a4cc7`) · M3 (candle store + mplfinance renderer, `journal chart
<position_id>` — 83 tests green, `797849b`) · M4 (SL/TP poller, `journal poll`
— 110 tests green, `0f1b088`) · M5 (MAE/MFE + `journal report` — 138 tests
green, `11cac94`) · M5.1 (session + EA/discretionary breakdowns in
`journal report` — 150 tests green, `3a5d198`) · M6 (annotations +
manual/auto tags, `journal annotate`/`tag` — 179 tests green, `24ce64b`) ·
M6.1 (weekly Markdown report, `journal weekly` — 188 tests green,
`a989eac`) · **M7** (web dashboard, `journal serve` — 202 tests green).

**M7 in one line:** a read-mostly FastAPI/Jinja2 dashboard on `localhost`
(`journal serve`, default `127.0.0.1:8000`) sitting entirely on top of the
existing pure functions — Dashboard (`build_report`), Trades list + detail with
on-demand chart PNG (`render_trade`), and Weekly (`build_weekly`), plus the only
web writes: annotation + manual-tag forms (`set_annotation`/`add_tag`/
`remove_tag`). New package `src/journal/web/` (`app.py` factory, `views.py`
context builders, `format.py` Jinja filters, `templates/`, `static/app.css`).
It never imports the MT5 adapter (rules 1 & 12) — `sync`/`candles`/`poll`/
`rebuild` stay CLI-only. Same display discipline as the CLI: money always
carries `USC`, unknown reads "n/a"/"unknown" (never 0), n<20 buckets greyed
(§9), URLs key on `position_id`. New deps (rule 8, approved): `fastapi`,
`jinja2`, `uvicorn`, `python-multipart`. Verified live: dashboard figures match
`journal report`; annotation/tag written from the UI survive `journal rebuild`.

**M5 in one line:** `trades.mae`/`mfe`/`mae_r`/`mfe_r` (NULL since M2) are now
filled by `rebuild()`, and `journal report` gives a first honest read of the
account — money stats at full coverage, R-stats correctly gated as
"insufficient" at today's `n=6`. Session bucketing and EA/discretionary
breakdowns were scoped **out** to M5.1 (the roadmap's one-line M5 description
bundled 4 features, ~4x M3/M4's size — split mirrors how M1→M1.1/M1.2 and
M2→M2.1 actually happened).

**The plan went through a validation pass before any code was written, and it
caught three real bugs, plus a fourth surfaced while fixing the third — all
four are now regression-tested, not just fixed:**

- **No money conversion needed at all.** `mae_r = mae / |open_price -
  real_sl|` — `risk_amount`'s `tick_size`/`tick_value`/`volume` cancel
  algebraically against the same terms in `mae_money`. First draft added a
  `distance_to_money()` helper to `domain/risk.py`; that file stays completely
  untouched instead — a stricter version of M4's own "don't modify
  `domain/risk.py`" precedent (M4 solved a *different* problem there; here
  there was no risk.py-shaped problem to solve).
- **A bar-open-time filter would have silently dropped most short trades.**
  `candles.time_msc` is a bar's *open* time; requiring it to fall inside
  `[open,close]` returns nothing for a trade that doesn't contain a bar-open
  boundary — true of most of the 11 sub-M1 trades (min 1s). Fixed with
  covering-bar semantics, mirroring `render/chart.py::_nearest_bar_index`
  (the same problem, already solved once for chart markers): the bar
  *containing* open through the bar *containing* close.
- **Scanning every timeframe for a symbol is unsafe on this hedging account.**
  Two overlapping trades of different durations can pick different TFs
  (`choose_timeframe`); a coarser trade's wider bar would leak into a shorter
  trade's excursion if the TF column were ignored. Excursion is scoped to
  **the trade's own TF**, not the symbol alone.
- **The fix for that surfaced a fourth issue:** a bulk in-memory preload
  (mirroring M4's `sl_tp_snapshots` pattern) risks a short trade silently
  matching a *different, disjoint* trade's stale cluster on the same
  symbol+TF, since `candles` pools every trade's window (schema.sql: "Dedupes
  across trades on the same symbol/day"). Fixed with a **scoped SQL query per
  trade** (symbol + that trade's own TF + its own `window_for` window) instead
  of a bulk scan — which also meant excursion couldn't thread through
  `reconstruct()` the way M4's `snapshots` did (a trade's open/close only
  exist *after* `reconstruct()`'s loop runs). It's a post-processing step in
  `rebuild()` instead: `Trade` is a mutable dataclass, so `_fill_excursions`
  sets `mae`/`mfe`/`mae_r`/`mfe_r` in place before the INSERT loop.
  `reconstruct()`'s signature and pure logic are untouched by M5.
- **The SL-exactly-at-entry ZeroDivisionError guard (Trap 6/M2.1) recurred a
  third time**, now in `mae_r`/`mfe_r`: `real_sl == open_price` gives a
  *known* zero `risk_distance`, not an unknown one — gate on it being truthy.
  Three occurrences of one bug shape in this codebase now (`r_multiple`,
  `mae_r`/`mfe_r`, and `profit_factor` in the report below) — worth watching
  for as a pattern, not three unrelated bugs.
- **A real workflow wrinkle, documented rather than hidden:** MAE/MFE needs
  `candles`, which `journal candles` only fetches for trades already in
  `trades` — so the order is `sync → rebuild → candles → rebuild` (rebuild
  **twice**) on a fresh account. Safe (`rebuild` is idempotent) and
  unavoidable even in steady state.
- **`journal report`'s win/loss/breakeven classification uses tolerance**
  (`abs(net_profit) <= 1e-9`), never `==`/`>`/`<` on a raw float (rule 5) —
  every downstream count depends on getting this comparison right.
- **`n_with_mae` is a plain diagnostic, never gated** — "how much of the
  account has candle coverage yet" isn't itself an average, so it's shown
  regardless of `n`, unlike `avg_r`/`avg_mae_r`/`avg_mfe_r`.

**Live smoke:** `journal candles → rebuild → report` against the live
account. `candles`: 2494 new bars, 72/72 trades windowed. `rebuild`: `mae/mfe`
went from 0 to **72 computable**. `report`:
```
win rate: 34.7%   avg win: 9.92 USC   avg loss: -3.75 USC
profit factor: 1.41   expectancy: +1.00 USC
R-multiple: n/a (n=6, need ≥20)      -- correctly withheld, not a bug
MAE/MFE:    candle coverage 72/72; n/a (n=6, need ≥20) -- same reason
```
Net profitable despite a sub-40% win rate (cuts losses short: avg loss
magnitude less than half avg win) — and the report correctly refuses to
average 6 R-multiple data points as if they were reliable.

**Not blocked.**

**M5.1 in one line:** `journal report` gained two behaviour breakdowns —
`by session` (five fixed UTC trading-session buckets) and `by source` (EA vs
discretionary) — each reusing M5's §9 `n≥20` gate per bucket, so a thin bucket
reads `n/a` (with its count beside it) instead of a number pretending to be
reliable. No schema change, no migration, `domain/reconstruct.py` untouched —
`open_time_msc` and `magic` were already on `trades`. New pure module
`analytics/sessions.py` (`session_of` + `SESSION_ORDER`); `build_report` gained
`BucketStat` + `by_session`/`by_source`. 150 tests green (was 138). Followed the
4-phase plan in `docs/plans/M5.1-sessions-ea-breakdown.md` verbatim, TDD each
phase (test written and seen failing before the code).

M5.1 decisions worth knowing:

- **Session model = fixed UTC trading-session windows**, half-open `[start,end)`,
  tiling the whole day: Asian 00–07 · London 07–12 · LDN/NY 12–16 · New York
  16–21 · Late 21–24. Server clock IS UTC (`server_utc_offset_s=0`, docs §7),
  so the hour is read with no offset — via the repo's canonical
  `datetime.fromtimestamp(ms/1000, tz=timezone.utc)`, never the naive
  `utcfromtimestamp` (rule 3).
- **Counts, not denominators.** The per-symbol hours caveat (BTC 24/7 but
  XAU/EUR are not — docs §7) is handled by reporting *raw bucket counts* and
  gating averages; the report never divides by a "hours available" figure we
  have not built. A low bucket count may just mean the symbol was shut.
- **EA split classifies on `magic` alone** (docs §7: `magic!=0` ⟺ EXPERT ⟺ the
  same 6 trades). A truthy magic is EA; `0` **and** `NULL` both fall to
  discretionary — rule 4: an unknown magic is not evidence of EA.
- **Live read (72 trades):** sessions partition exactly (23+35+5+3+6=72), source
  splits 6 EA / 66 discretionary — matching §7's measured EA count. All six EA
  trades opened in the London session (EA and London share `n_with_r=6`), a
  consistency cross-check the data surfaced on its own. Every session but Asian
  (23) and London (35) sits under the gate and reads `n/a`, by design.
- **`journal rebuild` still succeeds** post-change (breakdowns are read-only);
  the DoD run showed 72 trades / mae-mfe 72 computable, unchanged.

**M6 in one line:** the human layer landed. `journal annotate <position_id>`
captures setup/confidence/emotion/plan/notes; `journal tag add/rm/ls` manages
manual tags; `rebuild` now also writes auto tags; and `journal weekly` renders
one ISO week to a Markdown file in `cache/`. The storage (`annotations`, `tags`,
`v_trades_annotated`) already existed in `schema.sql` and the live DB, so M6 was
**wiring only — no schema change, no migration**. 188 tests green (was 150).
Shipped as two commits at the natural split, mirroring M5→M5.1: **M6**
(annotations + tags, `24ce64b`) and **M6.1** (weekly report, `a989eac`).
Followed `docs/plans/M6-annotations-weekly-report.md` with TDD each phase.

M6 decisions worth knowing:

- **The human layer is keyed on `position_id`, never `trades.id`** (which
  renumbers every rebuild — schema comment). Annotations and manual tags live in
  the "never rebuilt" section and **survive `rebuild`** — verified live: a
  manual tag + annotation set before a rebuild both persisted while the 34 auto
  tags regenerated around them.
- **The auto-tag pass (`_fill_auto_tags` in `rebuild`, mirroring
  `_fill_excursions`) deletes ONLY `source='auto'`** before re-inserting — that
  one WHERE clause is what keeps manual tags safe. Idempotent across rebuilds.
- **Auto tags are structural facts, not opinions:** `sub-1min`,
  `held-overnight`, `weekend`. The value-laden `big-win`/`big-loss` are gated
  off below n=20 (§9) and computed from account deciles by the caller, so no
  outlier label is applied against a sample too small to define one.
- **Weekly attributes a trade to the week it CLOSED in** (realized P&L),
  Mon–Sun UTC, half-open. Weekly rates/averages are §9-gated (a week rarely
  clears n≥20, so they usually read `n/a`); the raw counts, the realized net
  total (a sum), and the annotated/manually-tagged trades are always shown —
  that is what a weekly review is for. Reuses M5.1's `bucket_stat` (promoted
  from private) so weekly and account reports share one definition of "a win".
- **Weekly output is a reproducible `cache/` artifact** (rule 6) — verified
  byte-identical on regeneration; `cache/` is gitignored.

**Next: roadmap complete through M7.** The original ask (M0–M3) plus the
poller, analytics, the human layer, and the M7 web dashboard (`journal serve`,
`cec87d9`) are all shipped. No milestone is currently
scheduled; natural follow-ups if the tool earns daily use: auto-tag rule
expansion (the `source='auto'` pipeline is built), a multi-week/trend view
(the weekly builder generalises), and richer annotation querying/filtering.

---

**Evidence from earlier milestones, kept for reference:**

**M4 in one line:** `journal poll` snapshots live open positions'
`positions_get()` SL/TP into `sl_tp_snapshots` on change; `journal rebuild`
consults that data whenever `orders_raw` gives nothing, closing (going
forward only) the gap M2 measured — only 6/68 trades had a recoverable
`sl_initial` from the order alone.

M4 decisions still worth knowing:

- Forward-only by the nature of the MT5 API — `positions_get()` only returns
  open positions, so the 62 historical discretionary trades stay
  `sl_initial IS NULL` forever; M4 only helps trades open *while polling*.
- A confirmed-`0.0` (poller-observed "no SL ever") is a real, auditable fact
  (rule 4) but must never reach `risk_amount()` as a price — `_real_sl_price()`
  is the guard M5 reused three paragraphs above.
- The "all-zero → confirmed" coverage caveat is accepted, not solved: a
  proximity safeguard would itself be a latent Trap-7 bug (`observed_msc` is
  poller wall-clock UTC; `open_time_msc` is broker server time).
  Blast radius is contained regardless — a wrong `0.0` still yields
  `risk=None`, never a poisoned statistic.
- Change-only logging, not per-tick (11h25m at 5s intervals would be ~8200
  rows/trade otherwise).
- Two bugs caught before commit, both now regression-tested: a same-millisecond
  PK collision that silently dropped a real SL observation (fixed by forcing
  strictly-increasing `observed_msc`), and `journal poll`'s activity being
  invisible in a terminal with no logging handler configured (fixed with an
  `on_cycle` CLI callback).

**M3 in one line:** trades became visible. `journal candles` fetches each
closed trade's render window into the central `candles` table; `journal chart
<position_id>` reads it back and writes a PNG to `cache/`.

M3 decisions still worth knowing:

- TF picked by a duration ladder (finest TF where the trade spans ≤60 bars,
  floor M1, padded 15 bars each side) — M15 was rejected as a default because
  it draws the *median* trade as a single candle (doc §7).
- 11/68 trades are sub-M1 (min 1s) — rendered honestly, both markers on the
  one bar, title says so, never a fabricated intrabar line.
- Cache keys on `position_id`, never `trades.id` (which renumbers every
  rebuild) — CLI takes `position_id` only, no `--trade-id` alias.
- SL/TP hlines and R display both gate on the VALUE (rule 4), not on
  `is not None` — the exact guard shape M4 later needed again for
  `risk_amount()`, above.
- Axis reads `sync_state.server_utc_offset_s`, never hardcodes 0, and displays
  WIB consciously (chart = primary display surface, rule 3).
- `live.py.copy_rates_range` calls `symbol_select` first (item 0) — insurance
  against Trap 12, not a proven bug.
- Self-inflicted bug: `record_fixtures.py`'s rates addition initially sourced
  trade selection from the live pull, drifting the frozen M1/M2 fixture
  snapshot and breaking 8 tests. Fixed by anchoring rates selection to the
  already-committed fixtures on disk.

**M2 closed the milestone everything since M0 was built to make verifiable.**
Reconstruction is a *partition* of the deals, and the §6 identity-2 invariant
now proves it lost or double-counted nothing:

```
offline (140-deal fixture):  sum(trades.net) 63.72 + non-trade 5998.00 = 6061.72
live (traded since):         sum(trades.net) 71.72 + non-trade 5998.00 = 6069.72
```

Both partition the balance exactly. Offline drive: rebuild → 68 trades →
reconcile 14.50 → verify PASS both identities → rebuild idempotent. The live
identity-2 check passed too (71.72 + 5998.00 = 6069.72) — the invariant held on
data that did not exist when the code was written.

Two facts M2 *measured* (numbers in `docs/mt5-deal-model.md` §7):
- `sl_initial` is recoverable from `orders_raw` for only **6 of 68** trades, and
  those six are exactly the EA set: `{sl!=0} == {magic!=0} == {reason==EXPERT}`.
  Discretionary R-coverage is **0 of 62**. So one side of the EA/discretionary
  split M5 requires is empty until the M4 poller records SLs going forward —
  another way M4 is load-bearing, not a nicety.
- 62 of 68 trades therefore carry `sl_initial IS NULL` / `r_multiple IS NULL`,
  correctly excluded from R stats (never coerced to 0 — Trap 6).

**The M1.2 live smoke still stands as the strongest ingest evidence:**

```
sum(deal cash):  6061.72 → 6069.72   (+8.00, traded since the fixtures)
balance:         6047.22 → 6055.22   (+8.00)
residual:          14.50 →   14.50   (unmoved)
```

The broker returned 148 deals, not the 140 in the fixtures. Both sides of the
identity moved by exactly the same amount and the gap did not budge — the
prediction held against real money. `archived: none` (the Trap 16 tripwire is
armed and quiet). Offset measured 0 that sync, not inherited.

### The 14.50 USC gap — RESOLVED, do not reopen

Cause: **the broker archived deals and deleted them from history.** Correction
deal `1399033630` @ 2026-07-11 04:58:56, amount `0.00`, comment `"Archived
deals"`. The deleted deals netted −14.50 USC.

Confirmed against MT5's own `Account History → Report`: the report's cumulative
Balance column ends at **6061.72** while its `Balance:` line reads **6047.22** —
MT5's own export carries the identical gap. **Not an adapter bug.** Swap and
commission are genuinely `0.00` (swap-free cent account); the bridge is faithful.

Full evidence and arithmetic: `docs/mt5-deal-model.md` §6 and Trap 16.

At M1/M2 this becomes one `reconciliations` row with `status='explained'` — not
`unexplained`, and never a tolerance. §6 has the exact row.

### What this discovery changed

**MT5 is not a durable record of your trading. This journal is.** The broker
deletes history — already observed, five days before M0 began. Every day without
a sync is a day something can vanish for good.

That promotes M4 (poller) from convenience to the reason the project exists, and
turns `deals_raw` being append-only from a style rule into an archival guarantee.
See Trap 16.

---

## Who does what

| Seat | Tool | Owns | Never touches |
|---|---|---|---|
| **Architect / reviewer** (you) | Cowork | `docs/`, analysis scripts, reviewing Claude Code's plans | `src/`, `tests/`, `schema.sql` |
| **Implementer** | Claude Code | `src/`, `tests/`, `schema.sql`, `pyproject.toml` | `docs/`, `CLAUDE.md` |

**This separation is the point, not bureaucracy.** The reviewer's value comes
entirely from *not sharing the implementer's context*. If you start writing
`src/`, you become the implementer, you inherit its blind spots, and the review
loop degrades into two agents agreeing with each other.

If Claude Code's plan looks fine to you, say so — but read the actual diff or the
actual data first, not the summary of it.

---

## How this project has been worked

1. **One milestone per session.** Plan mode on. Name the files that may be
   touched. Approve only after reading the plan properly.
2. **Definition of done = pasted evidence.** Real pytest output, real command
   output. "Tests pass" without the output is not done.
3. **Commit per milestone, then `/clear`.** Context quality degrades long before
   the window fills.
4. **Knowledge goes in `docs/`, not `CLAUDE.md`.** CLAUDE.md is a ~110-line
   instruction budget. Every line added weakens the others. If Claude Code starts
   ignoring a rule, suspect a bloated CLAUDE.md before suspecting the model.
5. **Measure, do not recall.** See the error log below.
6. **The human runs anything that writes to git or touches the live account.**
   Fixture recording included — sanitisation review is a human job.
7. **State dependencies out loud.** When handing over more than one task, say
   which are parallel and which gate which. An instruction that arrives alongside
   doubt about whether it still applies cannot be executed with confidence.

---

## Error log — why "measure, don't recall" is a rule

Every one of these was caught by machinery deliberately built for it, not by luck.

| What | Who was wrong | Caught by |
|---|---|---|
| `DEAL_TYPE_COMMISSION = 6` in the design docs | **The docs.** Bridge reports `BONUS=6, COMMISSION=7`. | The `live.py` enum assertion (CLAUDE.md rule 12) |
| `Candle.time` seconds → `candles.time_msc` column | The plan. Would have silently produced empty charts at M3. | Independent review of `base.py` against `schema.sql` |
| Sanitising `comment -> ""` on all 140 deals | **The reviewer's own spec.** Destroyed the string `"Archived deals"` — the literal answer to the 14.50 question — and every `[sl]`/`[tp]` marker. | Counting non-empty comments in the recorded fixture |
| "The 14.50 might be swap the bridge is dropping" | The hypothesis. `swap = 0.00` on all 140 deals and in MT5's own report. | Reading the report instead of theorising about it |
| "A widening residual means the broker archived more history" | **The reviewer's M1 spec.** Archiving moves no money, so the residual never budges. Shipped as a false docstring in `ingest/deals.py`. | Reasoning through what archiving actually does to a balance |
| The reviewer's `schema.sql` working copy | **The reviewer.** It was never installed; Claude Code wrote a better `reconciliations` table (dropped a redundant column, dropped an unused state, better placement). The reviewer had been reviewing against a file that did not exist. | Reading the repo instead of the working copy |
| This file claiming the 14.50 was "BLOCKED ON A HUMAN" after it was resolved | **This file.** A stale handoff is worse than none: it sends a fresh reader to redo finished work, then hands them a decision rule that is now wrong. | Auditing the repo against what was actually asked for |
| A second `schema.sql` at repo root, frozen since M0.1 (`e653905`), diverged from `src/journal/store/schema.sql` — missing `accounts.balance`/`equity`, and a `reconciliations` table pre-dating the M2 review fix (3 statuses instead of 2, different column order) | **Nobody's edit — an old tracked file nobody deleted.** `db.py` only ever reads `src/journal/store/schema.sql`; the root copy was dead but readable, and reading it first gives you wrong facts about the schema with no error to warn you. | A fresh reviewer session diffing both files byte-for-byte before trusting either |
| `probe_rates.py` printing "VERDICT: no dependency. live.py is correct as written" | **The reviewer's own probe.** It tested `symbol_select` on `BTCUSDc` — a traded symbol already in the container's persistent Market Watch — so both arms of the experiment were the same arm. The script asserted a conclusion its design could not reach, in the confident voice reserved for measurements. A probe that overclaims is worse than no probe: it closes a question that is still open. | Re-reading the probe's own method after seeing the result it wanted |
| `record_fixtures.py`'s M3 rates-recording addition sourced trade selection from the *live* pull the script was already doing, to pick which trade's candle window to fetch | **Claude Code's M3 implementation, first pass.** The script has always refreshed *every* fixture on each run (its original, correct job); adding rates on top of that fresh pull meant a routine re-run silently drifted `deals.json`/`orders.json`/`account.json`/`symbols.json` away from the frozen 2026-07-16 snapshot 8 M1/M2 tests hardcode (140 deals, 68 trades, balance 6047.22, …) — the account had genuinely traded more since. | `pytest` — 8 tests went red immediately after a live re-run, before any commit |
| M4's `poll_once` silently dropped a real SL observation: two DIFFERENT states for the same position landing in the same millisecond collided on the `sl_tp_snapshots` primary key, and `INSERT OR IGNORE` kept only the first | **Claude Code's M4 implementation, first pass.** The bug wouldn't fire at a real 5s poll interval, but did fire immediately under a fast test loop — exactly the gap between "works in the demo" and "works under load" this project's testing culture exists to close. | An ad-hoc verification script run before the formal test suite existed, asserting on the actual row count in `sl_tp_snapshots` rather than trusting the reported `snapshots_written` count |
| M4's `journal poll` (no `--once`) reported cycle activity only via `logging.info`, invisible in a terminal with no handler configured | **Claude Code's M4 implementation, first pass.** A long-running foreground command a human is meant to watch would have looked hung the entire time even while working correctly — the CLI's only feedback was a single summary line printed after Ctrl+C. | Self-review of the diff before commit, not a test — logging visibility isn't something `pytest` checks by default; worth remembering next time a command runs in the foreground indefinitely |
| M5's first MAE/MFE draft added a `distance_to_money()` helper and refactored `domain/risk.py` to use it | **Claude Code's M5 plan, first draft.** Unnecessary: `risk_amount`'s `tick_size`/`tick_value`/`volume` cancel algebraically in `mae_money/risk_amount`, leaving `mae_r = mae / abs(open_price - real_sl)` — no money conversion, no risk.py change, ever needed. | A design-review pass (Plan agent) done deliberately *before* writing code, working the algebra through by hand |
| M5's first MAE/MFE draft filtered candles by "bar open time falls inside `[open,close]`" | **Claude Code's M5 plan, first draft.** `candles.time_msc` is a bar's OPEN time; the filter would have returned `(None,None)` for most of the 11 sub-M1 trades (min 1s), since a fast trade rarely contains a bar-open boundary at all — a coverage gap silently misreported as "no data". | The same design-review pass, cross-checked against the measured duration profile (docs §7) instead of assuming candles align to trade windows |
| M5's first MAE/MFE draft scanned every timeframe stored for a symbol, reasoning "OHLC bars preserve true extremes at any granularity" | **Claude Code's M5 plan, first draft.** True for one timeframe alone, but this account is hedging (CLAUDE.md line 26): two overlapping trades of different durations can sit at different TFs, and a coarser trade's much wider bar would leak into a shorter trade's excursion if the TF column were ignored. | The same design-review pass, reasoning through what "hedging + per-trade TF choice" implies for a symbol-wide scan |
| M5's *corrected* design still risked a bulk in-memory candle preload (mirroring M4's `sl_tp_snapshots` pattern) picking up a different, disjoint trade's stale cluster on the same symbol+TF | **Claude Code's M5 implementation, working through the TF fix.** The central `candles` table pools every trade's window (schema.sql: "Dedupes across trades on the same symbol/day") — a "nearest preceding row anywhere" scan isn't scoped to one trade the way a bounded SQL query is. | Reasoning through the bulk-preload approach's failure mode before implementing it, not after a test caught it — the regression test (`test_excursion_scoped_per_trade_not_contaminated_across_timeframes`) was written to prove the FIX, not to find the bug |

The pattern: **the design documents are the least reliable source in this
project.** The bridge, the fixtures, the account, and the broker's own report are
authoritative. When they disagree with a doc, the doc is wrong — patch it, and
note what was measured.

---

## Roadmap

| | Milestone | Status |
|---|---|---|
| M0 | Adapter protocol, symbol normalisation, DB bootstrap, `doctor` | done |
| M0.1 | Candle→ms, probed enums | done |
| M0.2 | Re-record fixtures with comments preserved | done (`a15cc5e`) |
| M1 | Ingest deals/orders → `_raw` tables, `journal verify` | done (`1d086c2`) |
| M1.1 | Archive detector, bridge-free verify, offset COALESCE | done (`1d086c2`) |
| M1.2 | Model `equity` on `Account`; live smoke passed | done (`10d9141`) |
| M2 | `reconstruct.py`: deals → trades, `rebuild`, §6 identity 2 | done (`48a4cc7`) |
| M2.1 | Review fixes: zero-risk R guard, NULL time_msc reject, guard dedup | done (`48a4cc7`) |
| M3 | Candle store + mplfinance renderer (`journal chart <position_id>`) | done (`797849b`) |
| M4 | SL/TP poller — makes `sl_initial` knowable, and outruns the archiver | done (`0f1b088`) |
| M5 | MAE/MFE + core `journal report` (money stats + gated R-stats) | done (`11cac94`) |
| M5.1 | Session bucketing + EA/discretionary behaviour breakdowns | done (`3a5d198`) |
| M6 | Annotations + manual/auto tags (`journal annotate`/`tag`) | done (`24ce64b`) |
| M6.1 | Weekly Markdown report (`journal weekly`) | done (`a989eac`) |
| M7 | Web dashboard on localhost (`journal serve`) — read-mostly + annotation/tag writes | done |
| M8 | Per-symbol breakdown (`by_symbol`) + dedicated `/report` web page | done |
| M9 | Live positions + trade interaction + auto-ingest on close + UI redesign (`journal live`, `/live`) | **done — merged to main.** Live-verified 2026-07-23 (real account/bridge): auto-ingest-on-close, `/live` observe, and the order-send path to the broker all proven. The browser UI → live data (`open_positions`/`/api/live`) → `journal live` → bridge round trip WORKS and has for a long time. Only unmeasured: an *accepted* order landing — blocked solely by the MT5 container's AutoTrading toggle (a terminal setting, not code) — plus a browser visual/contrast pass. |
| Frontend rework | Jinja2 → React SPA served at `/`; Jinja UI retired at the Phase 5 cutover | done (`8d1de45`, 2026-07-24 — see the note at the top of this file) |
| M10 | Lab: regime + entry-timing models on candle data (`/lab` page, badge on `/live`) | done (`b4250a5`, 2026-08-08 — migration 010 / `SCHEMA_VERSION = 10`, `docs/lab-models.md`; see 2026-08-06 above) |

M0–M3 delivers the original ask: an automatic journal with charts. **Done.**
M4 onward — poller, analytics, annotations — is what makes the journal worth
returning to daily rather than a one-shot report.

---

## Account facts

**One home: `docs/mt5-deal-model.md` §7.** All measured against the live bridge.
Do not copy them here — a second copy drifts, and then two documents disagree
with no way to tell which one lies. Read §7.

The three worth a pointer, because they change how you work:

- **Trap 16** — the broker deletes history. The most important fact in the
  project. It is why the journal exists.
- **§9** — n=68. Every report must show `n` and suppress buckets under 20. A rule,
  not a caveat.
- **An EA touched part of this history** (12 deals with `magic != 0`, 6 closes
  with reason EXPERT). At M5.1, EA and discretionary trades must be separated
  or both populations are meaningless.

---

## Open questions

- [ ] Was `symbol_select` ever actually *needed* in `copy_rates_range`? Still
      unproven either way — the underlying probe was and remains inconclusive
      (it tested an already-selected symbol). M3 added the call as insurance
      regardless (`live.py`, item 0): idempotent, one call per windowed fetch,
      matches its two neighbours. The code question is closed; the empirical
      one — does this bridge actually need it — is not, and may never be worth
      resolving now that the insurance is cheap and in place.
- [ ] The real identifiers are still in the git **history**. `7464753` scrubbed
      the fixture; 2026-08-12 scrubbed the four places that had pasted one back
      in (see CURRENT STATE) and added the guard that keeps the working tree
      clean. Neither rewrote history, and `origin` is a **public** GitHub
      repository. Removing them from history means a force-push over published
      commits — a decision with its own cost, and the only part still open.

**Closed:** `httpx` missing from the dependencies (it is not — it is in
`[dependency-groups].dev`; a clean `uv sync` into a fresh worktree venv ran the
whole suite, `test_storage_api.py` included, 2026-08-12) · the 14.50 gap
(archived deals — see CURRENT STATE) · standalone
commission deals (none; MT5's report confirms `commission = 0.00`) ·
`BTCUSDc`/`EURUSDc` specs (M1 `symbol_specs`: tick_value 0.1 / 0.01 / 1.0 —
genuinely distinct, gold's transfer nowhere) · `MaxBars` (1,000,000 — doc §7) ·
per-symbol session hours (BTC 24/7, EUR ≈24h×5d, XAU ≈23h×5d — doc §7) ·
chart timeframe selection (duration ladder, ≤60 trade-bars, floor M1 — M3,
CURRENT STATE above) · chart cache identity (`position_id`, never `trades.id`
— M3, CURRENT STATE above).

## ~~PENDING HUMAN~~ — risk-based auto lot sizing (2026-08-04)

**CONFIRMED BY THE HUMAN 2026-08-05: all five steps below ran against the real
broker and behaved as described.** Kept as the record of what was checked. The
OPEN QUESTION at the end of this section is still open — it is a product
decision, not something a run can confirm.

The five steps, as run:

1. Start the MT5 bridge and `uv run journal live`. Confirm `journal doctor`
   reports the account and a recent tick.
2. On `/chart`, drag the SL line below the current price. Confirm the panel
   shows a lot, a risk in USC, and a Buy label — and that dragging above the
   price flips it to Sell.
3. Set the risk to the smallest workable value and open ONE position on the
   smallest symbol. Confirm: the ConfirmModal shows the intent sentence; the
   command appears in the audit log **showing the symbol and the direction**
   (not `#null` — see Finding 2 of the 2026-08-04 fix wave); `journal live`
   sends it; MT5 shows the position WITH the SL attached from the first tick.
4. Confirm the realised risk matches the panel's figure within the entry
   slippage, using `risk_amount` on the resulting trade after `journal sync`.
5. Try to open with an SL far enough away to exceed 5% of balance. Confirm the
   panel refuses and no command row is written.

**ANSWERED 2026-08-05 — the human chose option A: block the open on a stale
feed.** `lib/candles.staleEntryReason` gates the live panel's button on
`journal live`'s heartbeat AND on the age of the bar the entry price is
actually read off (stale past 2× the timeframe), with the reason shown in the
panel. Deliberately NOT wired to `views.positions_context.stale`, which the
original note suggested: that field is computed from `open_positions`, and with
no rows it returns `stale=False` (`views.py`, "cannot tell 'flat' from 'live
never ran'") — so it is always False in exactly the case that matters, the
first open on a flat account.

**CLOSED 2026-08-11 — the server half now exists too.**
`execute._check_feed_fresh`, called first inside `enqueue_open`, refuses on a
missing/stale heartbeat or on an actively-watched forming bar that has stopped
being refreshed (`live_store.newest_forming_update`). See the 2026-08-11 entry
in CURRENT STATE for why the watch join is what keeps it from refusing every
legitimate `/live` open. The original note, kept
for the reasoning:

**OPEN QUESTION — stale feed can size against a stale price (2026-08-04 review):**
Volume is frozen at enqueue by design; the executor's fresh-tick
re-validation (`_check_level`) catches a stop on the wrong *side* but not a
changed *size*. If `plannedEntry` (the last shown bar's close, `Chart.tsx`
`shownCandles` tail) is stale — `journal live` down, or a stale feed — the
human can size 0.10 lot against a 4035 close with a 4030 stop (50 USC
intended), the market gaps to 4060 before the command executes,
`_check_level` still passes on the SL side, and ~300 USC goes out instead of
50. Still bounded by `MAX_RISK_PCT` (5% of balance), but that can be many
multiples of the stated budget. `RiskSizePanel`'s live gate is
`disabled={!live}`, which only checks that `/api/live` responded, not that
the data is fresh. `views.positions_context` already returns `stale`/`age_s`
and `LiveData` already carries them, so if the answer is "block it" the data
is already there to wire up. If the answer is "allow it", the panel should
at least show the feed age next to the price so the divergence is visible
before the human commits size. No guard implemented — this is a product
decision, not a bug.
