# Handoff — read this first

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

**Last updated:** 2026-07-18

**Done:** M0 (adapter + store + doctor) · M0.1 (Candle→ms, enums probed from the
bridge) · M0.2 (fixtures re-recorded with `comment` preserved, `a15cc5e`) ·
M1 + M1.1 + M1.2 (ingest, archive detector, bridge-free `verify`, reconcile,
`equity` modelled — `1d086c2` / `10d9141`) · M2 + M2.1 (`reconstruct.py`:
deals → trades, `journal rebuild`, `journal verify` §6 identity 2 — 55 tests
green, `48a4cc7`) · M3 (candle store + mplfinance renderer, `journal chart
<position_id>` — 83 tests green, `797849b`) · **M4** (SL/TP poller,
`journal poll` — 110 tests green, `0f1b088`).

**M4 in one line:** `journal poll` snapshots live open positions'
`positions_get()` SL/TP into `sl_tp_snapshots` on change; `journal rebuild`
now consults that data whenever `orders_raw` gives nothing, closing (going
forward only) the gap M2 measured — only 6/68 trades had a recoverable
`sl_initial` from the order alone.

Decisions worth knowing before touching this code again:

- **Forward-only, by the nature of the MT5 API.** `positions_get()` returns
  only currently-open positions; a closed position's SL history cannot be
  retroactively polled. The 62 historical discretionary trades stay
  `sl_initial IS NULL` **forever** — M4 only helps trades open *while the
  poller runs*. Not a limitation to fix; it's the shape of the data.
- **The one genuinely subtle design decision, confirmed via AskUserQuestion
  before implementation:** the poller can now positively confirm "no SL was
  ever set" as a real `sl_initial = 0.0` (rule 4: "0 means none set") — the
  first path in the codebase to legitimately write that value (`orders_raw`
  alone was always ambiguous: the order only shows the SL at entry-instant, so
  its `0.0` is coerced to `NULL`, never stored). Feeding a confirmed-`0.0`
  straight into `risk_amount()` would treat `0` as a literal price near zero
  and return a huge, wrong number. `_real_sl_price()` at the `reconstruct()`
  call site keeps `trades.sl_initial` auditable (`0.0`) while risk math sees
  `None` (undefined exposure — same as fully unknown, *not* zero risk: no stop
  means unbounded downside, which is a different fact from a stop sitting
  exactly at entry).
- **The "all-zero → confirmed 0.0" coverage caveat is accepted, not solved,**
  and for a sharper reason than "keep it simple": the obvious safeguard
  (require the first observation to be near `open_time_msc`) would itself be a
  latent Trap-7 bug — `observed_msc` is the poller's **true UTC** wall clock,
  while `open_time_msc` is **broker server time**. They're comparable today
  only because this account's offset is 0; a naive proximity check would
  silently break the day the broker introduces DST. Blast radius is contained
  regardless: a wrongly-inferred `0.0` still yields `risk=None`, never a
  poisoned statistic — only `sl_source` reads `'poller'` instead of
  `'unknown'`. Tripwire: if a trade you **know** had an SL ever shows
  `sl_source='poller', sl_initial=0.0`, that's the signal to build the
  offset-corrected guard.
- **Change-only logging, not per-tick.** At a 5s interval the longest measured
  trade (11h25m) would produce ~8200 near-identical rows if logged
  unconditionally; a row is written only when `(sl, tp, volume)` actually
  changes for that `position_id`.
- **A real bug found via manual smoke-testing before the formal suite
  existed, now a permanent regression test:** two *different* SL states
  landing in the same millisecond (low clock resolution, or two `poll_once`
  calls back to back) collided on the `(account_login, position_id,
  observed_msc)` primary key, and `INSERT OR IGNORE` silently dropped the
  second, real observation — the same class of data loss Trap 16 forbids for
  `deals_raw`. Fixed by forcing strictly-increasing `observed_msc` per
  position on collision.
- **A UX gap found in review, fixed before commit:** `journal poll` with no
  `--once` only reported via `logging.info`, invisible with no handler
  configured — a long-running foreground command a human watches would have
  looked hung the whole time even while working. Added an `on_cycle` callback
  so the CLI prints live per-cycle feedback (silent on idle cycles, matching
  the change-only philosophy).
- **`RebuildReport.n_with_sl` now counts a confirmed-`0.0` as "known"**
  alongside real price levels (defensible — we *do* know there was no SL —
  but it's not the same as "has R"). Flagging so it's a conscious fact for
  whoever builds M5's stats, not a surprise.

**Live smoke:** `journal poll --once` against the live bridge — `0 positions,
0 snapshots`. Decisive, not just "didn't crash": this was the account's
first-ever poll, so any open position would have written at least one row
(nothing to dedupe against yet). Zero snapshots on a fresh table means zero
open positions at that moment — confirmed by reading `sl_tp_snapshots`
directly. SL/TP **value fidelity** against the terminal is still unverified —
that check needs an actual open position, which didn't exist at review time.
Do it the next time you catch a position open.

**Not blocked.**

**Next: M5 — analytics (R-multiple, MAE/MFE, sessions, behaviour).** Not yet
scoped in any detail — unlike M3→M4, no docs §7-style measurement exists for
M5 yet. Known constraints going in: `n=68` (or `72` live) is small — every
stat needs `n` shown and buckets under 20 suppressed (§9); R-coverage is only
6/68 today and will grow slowly, one poller-covered trade at a time, so R
statistics need to handle a growing-but-still-small sample honestly, not
pretend the coverage gap is already closed; EA vs discretionary trades
(`magic` / `reason==EXPERT`) must be kept separate or both populations become
meaningless (Account facts, below).

---

**Evidence from earlier milestones, kept for reference:**

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
| M5 | Analytics: R-multiple, MAE/MFE, sessions, behaviour | **next** |
| M6 | Annotations + weekly report | |

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
  with reason EXPERT). At M5, EA and discretionary trades must be separated or
  both populations are meaningless.

---

## Open questions

- [ ] Was `symbol_select` ever actually *needed* in `copy_rates_range`? Still
      unproven either way — the underlying probe was and remains inconclusive
      (it tested an already-selected symbol). M3 added the call as insurance
      regardless (`live.py`, item 0): idempotent, one call per windowed fetch,
      matches its two neighbours. The code question is closed; the empirical
      one — does this bridge actually need it — is not, and may never be worth
      resolving now that the insurance is cheap and in place.
- [ ] Funding-deal comments (`D-IDQRISGT-…`, `W-ALLINT-…`) are payment
      references, now committed to git. Zero analytical value. If this repo is
      ever pushed anywhere public, redact `comment` on funding deals only
      (`DEAL_TYPE_BALANCE/CREDIT/CHARGE/BONUS`) — never on trades, never on the
      correction. Already in history, so the cost of deciding rises with time.

**Closed:** the 14.50 gap (archived deals — see CURRENT STATE) · standalone
commission deals (none; MT5's report confirms `commission = 0.00`) ·
`BTCUSDc`/`EURUSDc` specs (M1 `symbol_specs`: tick_value 0.1 / 0.01 / 1.0 —
genuinely distinct, gold's transfer nowhere) · `MaxBars` (1,000,000 — doc §7) ·
per-symbol session hours (BTC 24/7, EUR ≈24h×5d, XAU ≈23h×5d — doc §7) ·
chart timeframe selection (duration ladder, ≤60 trade-bars, floor M1 — M3,
CURRENT STATE above) · chart cache identity (`position_id`, never `trades.id`
— M3, CURRENT STATE above).
