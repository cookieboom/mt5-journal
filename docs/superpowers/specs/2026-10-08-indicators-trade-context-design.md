# Indicators §4 — Trade context + replay jump per period — design

**Status:** brainstormed and built 2026-10-08 (`feat/indicators-context`). Umbrella:
`2026-10-07-indicators-design.md` (its §3 outline, deferred there). Builds on
`2026-10-07-indicators-strategy-tester-design.md` (merged 2026-10-08).

Two features, one spec, one branch (user decision 2026-10-08):

- **A. Trade context** — the umbrella's deferred §3: how did *my* trades do
  when the active script's `signal()` agreed with them, vs when it did not.
- **B. Replay jump per period** — the half of the umbrella's replay jump the
  tester spec cut: days / weeks / session instances ranked by total R, click →
  replay at the start of that period.

Still out (YAGNI, re-confirmed 2026-10-08): `backtest_runs` table (only loss is
comparing an old run with a new one — add when that is actually wanted),
parameter sweeps (fastest route to overfit), walk-forward (the anti-overfit
tool; first candidate if one 70/30 split ever feels too thin), quantile
buckets (below).

Decisions taken in brainstorming (2026-10-08):

| Question | Decision |
|---|---|
| Order | **Both in one spec.** |
| "Signal true" for a trade | **Same-side signal in the last N closed bars** before the trade's reference time, default `N = 3`. A cross rarely lands exactly on the bar before entry; without a window almost every trade would read "false". |
| Where | **A new tab in the Strategy Tester.** Script, symbol and TF are already picked there; the tab sits beside the OOS headline, so "my trades vs the backtest" reads in one place. Not a Report section — no second picker. |
| Replay / paper | **Yes, as separate rows, ungated.** Real trades gated (§8), sim never mixed with real. `study` sessions excluded, as in the training summary. |

---

## 0. Measured 2026-10-08 (why the design looks like this)

| Source | closed | with R |
|---|---|---|
| `trades` XAUUSD | 137 | **36** |
| `trades` BTCUSD / EURUSD | 4 / 1 | 2 / 0 |
| `training_positions` | 72 | 34 |
| `paper_positions` | 3 | 0 |

A true/false split of 36 R-trades is ~18/18: **both R cells gated** today.
Win rate counts every closed trade (`net_profit`), so 137 split two ways
likely clears `n ≥ 20`. Hence:

- win rate + `n` is what this tab will mostly show for real trades; avg R /
  total R render gated (greyed, with `n_with_r`) until more trades carry an SL.
  That is the honest answer, not a defect.
- **Booleans only.** Quantile bins of a plot (umbrella outline) would be 4×~9
  trades → every cell gated. Add when R-trades pass ~80.

## 1. What exists (reuse, do not copy)

| File | Contract used |
|---|---|
| `web/indicators.load_frames(...)` | chart bars + warm-up + `tf()` frames. Context evaluates through it. |
| `domain/indicators/engine.evaluate` | `Result.signals[i]` three-valued series (1.0 fires); `Program.signals[i].side` `"long"|"short"`. |
| `analytics/report.bucket_stat(label, rows)` | §8-gated `BucketStat` (`n`, `win_rate`, `n_with_r`, `avg_r`, …) from rows with `net_profit`, `r_multiple`. **The real-trade cells use exactly this** — one definition of "win" and of the gate. |
| `domain/sim_stats.summary(rows)` | ungated `n, win_rate, avg_r, total_r` for replay/paper rows. |
| `domain/indicators/backtest._bucket` / `report` | breakdown buckets; B adds three keys here. |
| `web/backtest.replay_session(...)` | `decision_msc`, `exit_msc`, `lead_bars` → study session. B reuses it unchanged. |
| `store/training_store` | `origin` column (`blind`/`study`), migration 014. |
| `frontend/src/components/StrategyTester.tsx` | `Tab` union + tab list; add one tab, extend Breakdown. |

**No migration.** Nothing stored (rule 2, invariant 4).

---

## 2. Design

### A. Trade context

**Pure function** — `domain/indicators/context.py`:

```python
agreement(index, tf_ms, signals: [(side, Series)], ref_msc, direction, window,
          missing=np.array([])) -> "true" | "false" | None
```

- Last closed bar before `ref_msc`: the latest bar with
  `time_msc + tf_ms <= ref_msc`. Then the `window` bars ending at it — counted
  in **stored bars, not time** (weekends/gaps; memory: gaps are genuine).
- `"true"` when any `signal()` whose side matches the direction
  (`buy ↔ long`, `sell ↔ short`) is `1.0` on any of those bars. `"false"`
  when all of them are known and none fires. **`None` (unknown)** when the
  window has fewer than `window` bars in the store, or any value in it is NaN
  (warm-up) — never folded into `"false"` (rule 4). Unknowns are counted and
  reported, not bucketed. The service masks every bar before the frame's
  `warm_from_msc` to NaN: a pre-warm-up value is not trusted even when it
  reads 1.0.
- **Store holes are unknown, not quiet** (found on the live store while
  building): M5 has uncovered holes (2026-08-14→28, 09-07→20) and native D1
  stops 2026-07-26 while M1 runs on. `missing` = buckets M1 traded in that the
  frame lacks; one that closed by `ref_msc` inside the window's span → `None`.
  `MAX_GAP_MS` (4 days since the last closed bar) is the backstop where a
  symbol has no M1. Measured: 3 of 137 real trades on M5 had been labelled
  from stale bars before this.
- Opposite-side signals are ignored (a short signal before a buy is not
  "agreement"; showing "against" is a later column if asked).
- Forming bar never used: `ref_msc` is in the past and only closed bars
  qualify (invariant 5).

**Reference time per source** (the moment of decision, best known):

| Source | rows | `ref_msc` |
|---|---|---|
| real | `trades` where `symbol = :symbol`, `status = 'closed'` | `open_time_msc` (no decision time exists for real trades) |
| replay | `training_positions` ⋈ `training_sessions` where `s.symbol = :symbol`, `s.origin = 'blind'`, `p.status = 'closed'` | `decision_msc` |
| paper | `paper_positions` where `symbol = :symbol`, `status = 'closed'` | `requested_msc` |

`symbol` (verbatim) filters and reads candles; one symbol per tester run, so
grouping by `symbol_base` is that single group (rule 11). Hedged overlapping
trades each count once (CLAUDE.md).

**Service** — `web/trade_context.py`, route
`POST /api/indicators/context {script|source, inputs, symbol, timeframe, window=3}`:

1. Collect rows from the three sources; empty → 200 with zero counts.
2. One evaluation over `[min ref − warm-up, max ref]` via `load_frames` (not
   one per trade). Over `MAX_BACKTEST_BARS` (counted with `load_bars`, the
   same reader, before loading): the newest bars only — older trades read
   unknown and `clipped_from_msc` says where the cut fell. One old replay trade
   must not blank the tab (M1 spans always clip).
3. `classify` each row.
4. Response, per source `real | replay | paper`:
   `{true: Cell, false: Cell, n_unknown}`.
   Real cells = `bucket_stat` (gated; `total_r = avg_r · n_with_r` when
   `avg_r` is not `None`, else `None`). Sim cells = `sim_stats.summary`
   (ungated). Plus `window`, `computed_ms`, `source_hash`.
   Script error → 400 `script_error`, as compute.

### B. Replay jump per period

- `backtest.report` gains `breakdown.all.periods = {day, week, session}`
  (nested: `breakdown.*.session` already holds the session-label buckets):
  `day` (UTC midnight), `week` (Monday 00:00 UTC), `session` — the session
  instance from `analytics/sessions.session_window_msc(ms)`. Each bucket:
  `key` = period start msc, `end_msc`, `n, win_rate, avg_r, total_r` (`null`
  with no known R), `last_exit_msc`, and `segment: is | oos | mixed` by
  decision time, as the segments split. Ungated (tester exception).
- Jump: the existing `POST /api/indicators/backtest/replay` with
  `decision_msc = key`, `exit_msc = last_exit_msc` (a trade can exit after its
  period ends, and the newest period's `end_msc` lies in the future).
  `replay_session` now ends the range at the last bar `load_bars` has when
  nothing follows the exit, and never past now. **No new endpoint.**

### Frontend

- New tab **"Trade saya"** in `StrategyTester` (between *Breakdown* and
  *Pengaturan*). **Only beside a tester result** (rule 9: signal() output
  always next to its OOS expectancy, n, age). Lazy: fetches on open and when
  script / symbol / TF / window change, not on every live re-run (closed
  trades change on sync, not on bars); kept across tab switches, refreshed on
  reopen after 5 min; errors not kept; pending retried 5× per key. Window `N`
  input (1–50), debounced, its draft kept across tab switches.
- Layout: three blocks *Real*, *Replay*, *Paper* (sim blocks labelled "simulasi,
  tanpa gating"); each a 2-row table *Sinyal searah dalam N bar: ya / tidak*
  × `n · win rate · n R · avg R · total R`, plus "tidak diketahui: k". Gated
  cells greyed with the reason (`n R 18 < 20`), using the existing gated-cell
  style. Above the blocks, the tester's OOS headline line is repeated so the
  comparison is one glance. No verdict text, no "take/skip" wording (rule 9 —
  this tab is descriptive).
- *Breakdown* tab gains a **Periode** sub-section: `Hari | Minggu | Sesi`
  toggle, list sorted by total R (toggle best/worst), each row
  `tanggal WIB · n · win rate · total R · IS/OOS`, click → replay jump (same
  handler as the trade list).

---

## 3. Plan (one task = one commit, tests first — rule 7)

1. `domain/indicators/context.agreement` + tests: window counting across a
   weekend gap, NaN → `None`, short window → `None`, opposite side ignored,
   ref exactly on a bar close, forming bar excluded.
2. `sessions.session_window_msc` + `backtest.report` periods + tests
   (week starts Monday UTC, `oos` flag at the split, keys stable).
3. `web/trade_context.py` + route + schema; tests with fixture DB: real gated
   via `bucket_stat`, replay excludes `study`, paper included, unknown counted,
   cap → 400, script error → 400.
4. FE: `lib` types + API, "Trade saya" tab, Periode sub-section + jump; component
   tests.
5. Docs: CLAUDE.md unchanged (real cells use the existing gate; sim rows fall
   under the existing replay exception — say so in one line of the handoff),
   HANDOFF, memory `indicators-spec.md`; delete nothing (spec carries the plan).

Gates: `uv run pytest`, `ruff`, `mypy`, FE tests, `journal rebuild`, then a
browser check on XAUUSDc M5 with an EMA-cross script.

## 4. Risks

- **Reading agreement as edge.** 137 trades, mostly gated R. The tab shows `n`
  on every cell and never phrases a recommendation. Rule 9 stays intact: this
  is descriptive of past trades.
- **Reference time for real trades** is the fill, not the decision. With
  `N = 3` and M5 that is a 15-minute tolerance — stated in the tab tooltip.
- **Load.** One evaluation over the span of all trades; months of M5 is well
  under the 200k cap (57k XAUUSDc M5 bars total, measured 2026-10-07).

## 5. Review outcome and follow-ups

Four review waves (2026-10-08). Kept deliberately, not defects: the tab does
not refresh while it stays open; counting the clip through `load_bars` costs
~1.8 s on M1 (correct by construction — two hand-copied versions of its
native-else-M1 rule drifted and were removed).

Pre-existing, outside this spec:

- `load_frames` / the Strategy Tester read straight across store holes;
  expose the M1-missing buckets there so every consumer treats a hole alike.
- `load_bars` picks native vs M1 per range, so a partly-filled native TF can
  hide M1 bars inside that range (replay shows the same).
- `sim_stats.summary` reports `total_r = 0` with no known R (tester segments,
  training summary); buckets and context cells now say `null`.
