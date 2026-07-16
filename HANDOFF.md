# Handoff — read this first

You are taking the **architect / reviewer** seat on mt5-journal. This file is the
only project state that does not already live in the repo. Everything else is in
`CLAUDE.md` (the rules) and `docs/mt5-deal-model.md` (the domain knowledge).

Read both before doing anything. They are dense and they are load-bearing.

---

## CURRENT STATE — update this section every session

**Last updated:** 2026-07-16

**Done:** M0 (adapter + store + doctor, committed), M0.1 (Candle→ms fix, honest
enums probed from the bridge, fixtures recorded).

**In progress:** M0.2 — re-record fixtures with `comment` preserved.

**BLOCKED ON A HUMAN:** the 14.50 USC balance gap. See
`docs/mt5-deal-model.md` §6. Reisa must open MT5 → Account History → Report and
read the **Swap** and **Commission** lines.

- Report shows swap/commission ≈ −14.50 → **the adapter is dropping `swap`.**
  Stop. Fix the adapter. Do not start M2 — every cost figure downstream is a lie.
- Report shows swap 0.00 → the gap is outside deal history. Open a
  `reconciliations` row with `status='unexplained'` and proceed to M2.

Do not let anyone resolve this with a tolerance. See §6.

**Next after that:** M2 — `domain/reconstruct.py`. The hardest and most important
milestone in the project.

---

## Who does what

| Seat | Tool | Owns | Never touches |
|---|---|---|---|
| **Architect / reviewer** (you) | Cowork | `docs/`, `schema.sql`, analysis scripts, reviewing Claude Code's plans | `src/`, `tests/` |
| **Implementer** | Claude Code | `src/`, `tests/`, `pyproject.toml` | `docs/`, `CLAUDE.md` |

**This separation is the point, not bureaucracy.** The reviewer's value comes
entirely from *not sharing the implementer's context*. Every real bug caught so
far was caught because a second reader with no stake in the code looked at it
cold. If you start writing `src/`, you become the implementer, you inherit its
blind spots, and the review loop degrades into two agents agreeing with each
other.

If Claude Code's plan looks fine to you, say so — but read the actual diff or
the actual data first, not the summary of it.

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

---

## Error log — why "measure, don't recall" is a rule

Both of these were caught by machinery we deliberately built, not by luck.

| What | Who was wrong | Caught by |
|---|---|---|
| `DEAL_TYPE_COMMISSION = 6` in the design docs | **The docs.** The bridge reports `BONUS=6, COMMISSION=7`. | The `live.py` enum assertion (CLAUDE.md rule 12) |
| `Candle.time` in seconds → `candles.time_msc` column | The plan. Would have silently produced empty charts at M3. | Independent review of `base.py` against `schema.sql` |
| Sanitising `comment -> ""` on all 140 deals | **The reviewer's own spec.** Destroyed `[sl 4030.000]` markers and probably the explanation for the 14.50 gap. | Counting non-empty comments in the recorded fixture |

The pattern: **the design documents are the least reliable source in this
project.** The bridge, the fixtures, and the account are authoritative. When they
disagree with a doc, the doc is wrong — patch it, and note what was measured.

---

## Roadmap

| | Milestone | Status |
|---|---|---|
| M0 | Adapter protocol, symbol normalisation, DB bootstrap, `doctor` | done |
| M0.1 | Candle→ms, probed enums | done |
| M0.2 | Re-record fixtures with comments | in progress |
| M1 | Ingest deals/orders → `_raw` tables, `journal verify` | next |
| M2 | **`reconstruct.py`: deals → trades** | the hard one |
| M3 | Candle store + mplfinance renderer (`journal chart <id>`) | |
| M4 | SL/TP poller — makes `sl_initial` knowable going forward | |
| M5 | Analytics: R-multiple, MAE/MFE, sessions, behaviour | |
| M6 | Annotations + weekly report | |

M0–M3 delivers the original ask: an automatic journal with charts.

---

## Facts about this account you must not re-derive

All measured, all in `docs/mt5-deal-model.md` §7. The load-bearing ones:

- Currency is **USC (US cents)**, balance 6047.22 ≈ $60.47. Never print `$`.
- **Server clock is UTC** (offset measured 0). Sessions need no conversion.
- **Hedging** account. Only `entry` 0 and 1 exist in 140 deals — no INOUT, no
  OUT_BY. Reconstruction takes the simple path.
- Symbols: `XAUUSDc`, `BTCUSDc`, `EURUSDc`. Suffix set is `{"c"}` and nothing
  else. `XAUUSDc`: tick_size 0.001, tick_value 0.1 **USC**, contract_size 1.0.
- 68 closed trades. **Statistics on n=68 are mostly noise** — every report must
  show `n` and suppress buckets under 20. This is a rule (§9), not a caveat.
- 12 deals carry `magic != 0` and 6 closed with reason EXPERT. **An EA touched
  part of this history.** At M5, EA trades and discretionary trades must be
  separated or both populations become meaningless.

---

## Open questions

- [ ] The 14.50 gap (blocking — see CURRENT STATE)
- [ ] Does this broker emit standalone commission deals? (all `commission` fields
      currently read 0.00 — suspicious until the MT5 report confirms)
- [ ] `BTCUSDc` / `EURUSDc` contract specs — gold's do **not** transfer
- [ ] `MaxBars` actually in effect in the container (matters at M3)
