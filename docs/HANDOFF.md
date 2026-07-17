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

**Last updated:** 2026-07-16

**Done:** M0 (adapter + store + doctor) · M0.1 (Candle→ms, enums probed from the
bridge) · M0.2 (fixtures re-recorded with `comment` preserved, `a15cc5e`) ·
M1 + M1.1 + M1.2 (ingest, archive detector, bridge-free `verify`, reconcile,
`equity` modelled — 24 tests green, `1d086c2` / `10d9141`).

**The live smoke passed, and it is the strongest evidence this project has.**

```
sum(deal cash):  6061.72 → 6069.72   (+8.00, traded since the fixtures)
balance:         6047.22 → 6055.22   (+8.00)
residual:          14.50 →   14.50   (unmoved)
```

The broker returned 148 deals, not the 140 in the fixtures. Both sides of the
identity moved by exactly the same amount and the gap did not budge — the
prediction held against real money, on data that did not exist when the code was
written. `archived: none` (the Trap 16 tripwire is armed and quiet). Offset
measured 0 this sync, not inherited.

**Not blocked.**

**Next: M2 — `domain/reconstruct.py`.** The hard one. Everything built so far
exists to make it verifiable.

1. `ingest/deals.py` reads `equity = acct.raw.get("equity")`, justified in a
   comment as "the raw dump, the blessed carrier for un-modelled fields". It is
   not. `raw` was blessed for exactly one job — verbatim archival into
   `raw_json`, so the store survives MT5 adding fields. It is not a read path for
   semantic fields. Reading it from `ingest/` puts the MT5 field name `"equity"`
   outside the adapter (rule 12), returns `None` silently when absent, and sets
   the precedent "need a field? grab it from `.raw`" — which will be everywhere by
   M5, leaving the Protocol decorative. Fix: model `equity` on `Account` in
   `base.py`, map it in `live.py` and `fake.py`. Three lines, right file.
2. **The live smoke has never run.** Every drive so far used `FakeMT5Client`
   against a frozen fixture snapshot. `journal sync` via the CLI hardcodes
   `LiveMT5Client`, and that path has been executed zero times. `data/journal.db`
   does not exist.
   Prediction to check it against: live residual should be **exactly +14.50**
   however much has been traded since — each new deal moves `sum(deals)` and
   `balance` by the same amount, so the gap cannot drift. Anything else means
   something is broken; stop and find it before M2.

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
| M2 | **`reconstruct.py`: deals → trades** | **next — the hard one** |
| M3 | Candle store + mplfinance renderer (`journal chart <id>`) | |
| M4 | SL/TP poller — makes `sl_initial` knowable, and outruns the archiver | |
| M5 | Analytics: R-multiple, MAE/MFE, sessions, behaviour | |
| M6 | Annotations + weekly report | |

M0–M3 delivers the original ask: an automatic journal with charts.

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

- [ ] `MaxBars` actually in effect in the container (matters at M3).
- [ ] Funding-deal comments (`D-IDQRISGT-…`, `W-ALLINT-…`) are payment
      references, now committed to git. Zero analytical value. If this repo is
      ever pushed anywhere public, redact `comment` on funding deals only
      (`DEAL_TYPE_BALANCE/CREDIT/CHARGE/BONUS`) — never on trades, never on the
      correction. Already in history, so the cost of deciding rises with time.

**Closed:** the 14.50 gap (archived deals — see CURRENT STATE) · standalone
commission deals (none; MT5's report confirms `commission = 0.00`) ·
`BTCUSDc`/`EURUSDc` specs (M1 `symbol_specs`: tick_value 0.1 / 0.01 / 1.0 —
genuinely distinct, gold's transfer nowhere).
