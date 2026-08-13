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

**2026-08-13 — `journal status` now asks the bundle's age too (`health._dist`).**
The previous entry taught `status` that a running daemon can be running old
code. The served frontend has exactly the same failure and it already had a
detector — `web.app.stale_dist_reason`, added two entries below — which ran in
exactly one place: the moment `journal serve` starts. That is the wrong moment.
`serve` is a process nobody restarts, so the warning scrolls off once and the
bundle then goes stale in silence for days; the 2026-08-12 parity fix sat
unbuilt on disk precisely that way. A check that only fires on a process you
never restart is a check you do not have.

So `checks()` composes it as a sixth entry, `frontend`, exactly the way it
composes `verify` and `backup.due` — no new detection lives in `health.py`
(§ its own docstring), the import is lazy so a bridge-free status pass still
pays nothing for FastAPI until the check runs, and it is a WARN forever: an
unbuilt bundle serves an old page, it does not make one number in this store
untrue. The mtime blind spot is inherited from both neighbours and stated
there; note it also reads the *importing checkout's* `frontend/`, so running
`status` from a worktree measures that worktree, not what `serve` mounts.

Gates: `uv run pytest` **812 passed, 1 skipped in 37.61 s** (+3), and
`journal rebuild` on a 62 MB `--dest` snapshot of the live store: **OK, 129
trades**. Against the live store it prints `[warn] frontend  frontend/dist is
missing -> npm --prefix frontend run build` from the worktree, exit 0.

**2026-08-13 — `journal status` can now see that the daemon is running OLD code
(`live_heartbeat.started_msc`, migration 011).** Five of the last seven changes
to `journal live` ended with the same sentence — *needs a `journal live`
RESTART* — and the restart is the one step no test can perform. Nothing on the
machine could tell whether it had happened: `beat_msc` proves a process is
alive, never that it is current. The failure is silent and it compounds, since
the daemon holds the features that exist because they run unattended (the daily
backup, the command-queue expiry, the bridge-blip retry). A human reading
`[ok] live  heartbeat 3s ago` had every reason to believe the fix was live.

`live_loop` now calls `live_store.mark_started` once, at startup; `_live`
compares that timestamp against the newest `.py` under `src/journal/` and warns
with the filename when the code is newer than the process. It is
`web.app.stale_dist_reason` applied to the daemon instead of the bundle —
mtimes, no hashes, no build stamp, same known blind spot (a `git checkout`
rewrites mtimes, so restoring old code can read as new). Both cost a warning
and never correctness, which is what makes the cheap check the right one.

`started_msc` is NULLable and NULL means *unknown, do not accuse*: the daemon
running right now predates the column, so the check stays quiet until the next
restart writes one — the guard cannot fire on its own deployment. It is a WARN,
so the exit code stays 0 (§ the three states).

Gates: `uv run pytest` **809 passed, 1 skipped in 21.52 s** (+4). Migration and
`journal rebuild` both run on a 62 MB `--dest` snapshot of the live store: **OK,
129 trades**, and `journal status` on that snapshot prints `[ok] live` (NULL
start) then, with a hand-set two-hour-old `started_msc`, `[warn] live ... it is
running OLD code -> restart journal live`, exit 0.

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


### Older entries — full prose in [`handoff-archive.md`](handoff-archive.md)

One line each, newest-first. Open the archive when you need the reasoning.

- **2026-08-13** `journal restore` — puts a snapshot back safely: verifies the
  source, refuses while `live` runs, moves the old store aside.
- **2026-08-13** `journal status` — one bridge-free read-only health pass;
  WARN exits 0, only FAIL exits 1.
- **2026-08-13** `journal serve` warns when `frontend/dist` is behind the
  sources. It was behind.
- **2026-08-13** `journal live` takes a daily backup on the only long-lived
  process.
- **2026-08-12** `origin` is PUBLIC and four tracked files carried real
  identifiers; scrubbed and pinned by `tests/test_repo_hygiene.py`.
- **2026-08-12** `journal backup` — online sqlite3 snapshot of the one file
  that cannot be re-synced (Trap 16).
- **2026-08-12** `uv run pytest` 4–5 min → 29 s; cause was LightGBM spawning
  threads it could not use.
- **2026-08-12** the four constants the browser copies from the server are
  pinned by a test.
- **2026-08-12** browser and server now refuse the same opens
  (`staleEntryReason` makes all three server checks).
- **2026-08-12** review fixes on the stale-feed guard, merged and pushed.
- **2026-08-12** frontend defect fixed — `useLiveForming` returns tri-state
  `live: boolean | null`, so a non-polling chart stops accusing `journal live`.
- **2026-08-11** server-side stale-feed guard on `open`
  (`execute._check_feed_fresh`).
- **2026-08-10** chart drawing tools built, reviewed, fix wave applied, merged.
- **2026-08-12** the drawing-tools browser pass ran; all 8 PENDING HUMAN items
  PASS, feature closed.
- **2026-08-06** M10 (the lab) shipped — `src/journal/lab/`, six modules.
- **2026-08-05** nothing pending a human run any more; every item confirmed
  against the live bridge.
- **2026-08-04** ~~PENDING HUMAN~~ risk-based auto lot sizing — resolved,
  archived.


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
