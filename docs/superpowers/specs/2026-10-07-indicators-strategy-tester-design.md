# Indicators §3 — Strategy Tester — design + implementation plan

**Status:** designed 2026-10-07, built and merged 2026-10-08 (`feat/indicators-tester`). Umbrella spec:
`2026-10-07-indicators-design.md`. This spec **re-sequences** the umbrella:

- the umbrella's §3 (indicator context of *real* trades) is **deferred** — the
  user chose to postpone it;
- this §3 is the umbrella's §4 backtest, reshaped into a TradingView-style
  **Strategy Tester**: a collapsible strip under the chart that expands into
  tabs (summary + equity curve, trade list, breakdown, settings);
- the **Rule 9 amendment lands here** (user decision 2026-10-07), adapted:
  there is no stored run, so markers render only beside an on-demand result;
- **replay jump per trade is in** (user decision), with the
  `training_sessions.origin` migration. Replay jump *per period*, the
  `backtest_runs` table, parameter sweeps, walk-forward: still out (YAGNI).

Next session: read this file, then `pipeline` skill → branch + worktree
`feat/indicators-tester` → tasks below in order, TDD (rule 7). The plan
section is the plan; no separate plan file.

---

## 0. What exists (read before touching anything)

| File | Contract used here |
|---|---|
| `domain/indicators/lang.py` | `parse` → `Program(steps, signals, …, htfs)`; `Step(kind, target, expr)`; `_output` handles `plot/hline/signal`; `_lookbacks` walks `p.steps`. `signal()` today: exactly 2 positional args, **no keywords**. |
| `domain/indicators/engine.py` | `evaluate(p, f, inputs, tf_ms, htf=, forming_msc=) -> Result(plots, hlines, signals)`; `map_closed`. Signals are three-valued floats (1.0 fires). |
| `domain/replay_eval.py` | `step_bar(positions, bar)`: pending fills at the OPEN of the first bar strictly later than `decision_msc`; fill bar evaluated for SL/TP; **stop-first**; `close_requested_msc` → exit at next bar's open. `r_multiple(direction, entry, exit, sl)`. **The backtest reuses this — never a second fill model.** |
| `domain/sim_stats.summary(rows)` | `n, win_rate, avg_r, total_r, avg_mae_r, avg_mfe_r` from mappings with `net_profit, r_multiple, mae_r, mfe_r`. Ungated by design. |
| `analytics/report.sequence_stats(rows)` | `(n, max_dd, max_win_streak, max_loss_streak)` from rows with `close_time_msc, net_profit`. Typed `list[sqlite3.Row]` — widen to `Sequence[Mapping[str, Any]]`. |
| `analytics/sessions.session_of(ms)` | session label of a UTC epoch-ms. |
| `web/indicators.py` | `compute()` loads chart bars + warm-up + `tf()` frames + pending fill. **Extract that loading into one helper both compute and backtest call** — do not copy it. |
| `store/training_store.py` | `create_session(...)`, `career_summary(conn)` (ungated `_summary`). |
| `frontend/src/pages/Chart.tsx` | wires `IndicatorPanel`, `useIndicators`, replay-only `signalMarkers` (line ~207). |
| `frontend/src/lib/indicators.ts` | `normalizeLayout`, `indicatorsApi`, types. |

Measured 2026-10-07: candles `spread` is in **points**, never NULL for
XAUUSDc M5 (57,646 bars, avg 237 pts); `symbol_specs.point` XAUUSDc 0.001,
BTCUSDc 0.01, EURUSDc 1e-5. Latest migration is `013_paper.sql`.

---

## 1. Design

### 1.1 Language — entries carry their exits; `exit()`

```python
fast = ema(close, 20)
slow = ema(close, 50)
signal("long", crossover(fast, slow), stop=low[1] - atr(14), target_r=2)
signal("short", crossunder(fast, slow), stop=high[1] + atr(14), target=bb_lower(close, 20, 2))
exit("long", crossunder(close, slow))
```

- `signal(side, cond, stop=, target=, target_r=)` — all keywords optional.
  `stop`/`target` are chart-TF series expressions (may use `tf()`), read **at
  the signal bar**: absolute price levels. `target_r` is a constant
  (`_is_const`), R multiple of the actual risk. `target` and `target_r` are
  mutually exclusive; `target_r` without `stop=` is allowed only because the
  form's SL default can supply the stop (checked in the simulator: no stop at
  all → `target_r` ignored, counted).
- `exit(side, cond)` — new statement. On a bar where `cond` is 1.0, every open
  position on `side` closes at the next bar's open (`close_requested_msc`).
- `exit` joins `OUTPUTS` (reserved). A script with only `exit()` and no
  `signal()` is refused (nothing to exit).
- New `Step` kinds `"stop"`, `"target"`, `"exit"` (target = signal/exit index)
  so `_lookbacks` and the engine pick them up with no special path.
  `Program.exits: list[Exit(side, expr)]`; `Signal` gains
  `stop: ast.expr | None`, `target: ast.expr | None`, `target_r: ast.expr | None`.
- `Result` gains `stops: list[Series | None]`, `targets: list[Series | None]`,
  `exits: list[Series]` (exits three-valued like signals).

### 1.2 Simulator — `domain/indicators/backtest.py` (pure)

`simulate(f, r, p, consts, settings, spread_pts, point) -> Sim`.

Walk the chart bars in order. On bar `i`:

1. `step_bar(open_positions, bar_i)` — fills and SL/TP/requested exits.
   MAE/MFE tracked per open position from the bars it lived through.
2. **Max hold:** an open position held `>= max_hold` bars gets
   `close_requested_msc = t_i`.
3. **`exit()` on bar i:** request close of every open/pending position on that
   side. Reason `"exit"`.
4. **Entry signals on bar i** (value == 1.0):
   - `opposite_closes` (default on): request close of the other side
     (reason `"opposite"`), cancel its pending ones.
   - `overlap` off (default): enter only if every existing position is closed
     or close-requested (the closing one exits at the same next open, before
     the new one fills — `step_bar` list order guarantees it).
   - Fill price is `open[i+1]` — the fill model says so; the simulator reads it
     at decision time to size the bracket exactly as a real bracket order
     relative to fill would. No bar `i+1` → no entry (window end).
   - **SL:** the signal's `stop` at bar i if finite; else the form default —
     `atr`: `fill ∓ k·ATR(atr_len)_i`; `points`: `fill ∓ n·point`; `none`: 0.
   - **TP:** the signal's `target` at bar i if finite; else `target_r`
     (script) or form `tp_r`: `fill ± R·|fill − sl|` (needs sl > 0); else 0.
   - Wrong side (long with `sl >= fill` or `0 < tp <= fill`; mirror for short)
     → not entered, counted `rejected`. Never silently "fixed".
5. End of window: still-open positions are **open** — listed, drawn, never in
   stats (unknown outcome, rule 4). Pending ones are dropped.

**Cost.** R is net of spread: `r = r_multiple(...) − spread_pts_entry · point /
risk`, where `spread_pts_entry` is the entry bar's `spread`. Unknown spread →
the `spread_fallback` setting (points); if that is empty too, `r = None` and the
trade is counted `n_unknown_cost`. `n_spread_fallback` is always reported.
`ponytail:` one spread per round trip, charged at entry — the bid/ask asymmetry
of SL/TP triggers is not modelled.

R with no SL (`sl == 0`) is `None` (rule 4) — such trades are listed, counted
in `n`, excluded from every R statistic, exactly like `sim_stats`.

`Trade`: `id, side, decision_msc, entry_msc, entry, exit_msc, exit,
reason (sl|tp|exit|opposite|max_hold|open), sl, tp, r, r_gross, mae_r, mfe_r,
spread_fallback: bool`.

### 1.3 Stats — same module, `report(sim, split_msc)`

- **Split:** chronological on `decision_msc`, `split_msc = from + split·(to −
  from)`, `split` default 0.7. **OOS is the headline** everywhere.
- Per segment `all | is | oos`: `sim_stats.summary` (rows with
  `net_profit = r`, so a win is `r > 0`) + `profit_factor` in R (`None` when no
  loss) + `sequence_stats` (rows `close_time_msc = exit_msc`, `net_profit = r`)
  → `max_dd_r`, streaks. Trades with `r is None` count in `n` only.
- **Equity:** cumulative R at each `exit_msc`, plus `split_msc` for the marker.
- **Breakdown** for `all` and `oos`: by entry hour (UTC — the FE relabels to
  WIB at display, rule 3), by `session_of(entry_msc)`, by weekday (UTC). Each
  bucket `n, win_rate, avg_r, total_r`.
- **Ungated, `n` everywhere** — a backtest is simulated, same reasoning as
  `sim_stats`. Add it to CLAUDE.md's exception list (third exception).

### 1.4 Service + API — `web/backtest.py`, route in `web/routes/indicators.py`

`POST /api/indicators/backtest`
`{script|source, inputs, symbol, timeframe, from_ms, to_ms, split=0.7,
exits: {sl: "atr"|"points"|"none", sl_value=1.5, atr_len=14, tp_r=2|null,
max_hold=null, opposite_closes=true, overlap=false}, spread_fallback=null}`

1. Load via the helper extracted from `compute` (chart bars + warm-up +
   `tf()` frames + pending fill request). **No `session_id`** — the tester is
   hidden in replay (a backtest over a session's range would read past the
   cursor; invariant 2). No forming bar — stored bars are closed.
2. Cap: `MAX_BACKTEST_BARS = 200_000` chart bars → 400 with the count.
3. `symbol_specs.point` missing → 400 "no symbol specs for …".
4. Response: `{computed_ms, source_hash, split_msc, segments{all,is,oos},
   equity[{t, r}], trades[…], breakdown{all,oos}{hour,session,dow},
   counts{rejected, open, spread_fallback, unknown_cost, no_stop},
   warm_from_msc, pending}`. Script error → 400 with `script_error` (as
   compute). `source_hash` = sha256 of source + sorted inputs JSON.

Nothing stored (rule 2 / invariant 4). Tester settings live in the client's
`indicator_layout` blob, per layout entry, under `backtest`.

### 1.5 Rule 9 amendment (CLAUDE.md, this spec)

Replace rule 9's text with:

> **Descriptive by default; two predictive parts.** Everything outside
> `src/journal/lab/` and indicator `signal()` output describes past data and
> must not generate signals or recommendations. `lab/` and `signal()` may
> predict, under non-optional conditions: the output always renders beside its
> out-of-sample expectancy (R), `n`, and age (for `signal()`: the Strategy
> Tester result for that script/symbol/timeframe — no result, no live
> markers); it never places, modifies, or sizes an order (`trade_commands`
> still needs a human click; the tester has no order button); it is never the
> input to another automated step; it fires on closed bars only. No "should I
> take this trade" features anywhere.

### 1.6 Live markers

When the tester holds a result for the active script + symbol + TF and its
"Tampilkan di chart" toggle is on (default on), the live chart draws each
simulated trade: entry arrow at `entry_msc`, exit marker at `exit_msc` with
its R. The collapsed strip always shows the headline next to them:
`OOS +0.21 R/trade · n 34 · 3 mnt lalu`. Results come from closed bars only,
so no marker ever sits on the forming bar. The tester re-runs when the chart's
last closed bar changes while it holds a result (debounced) and on every
settings change; age shows since `computed_ms`. Replay keeps §1's own
behind-the-cursor `signal()` markers; the tester is hidden there.

### 1.7 Replay jump per trade — migration 014

- `014_training_origin.sql`: `ALTER TABLE training_sessions ADD COLUMN origin
  TEXT NOT NULL DEFAULT 'blind' CHECK (origin IN ('blind', 'study'))` (follow
  how `013` and `schema.sql` are kept in step). `journal live` must be
  restarted afterwards — the version check refuses old code (`store/db.connect`).
- `create_session(..., origin="blind")`. `career_summary(conn,
  include_study=False)` excludes `study` by default; the training summary UI
  gets a "sertakan sesi studi" toggle. Cherry-picked winners must not inflate
  blind-training stats.
- `POST /api/indicators/backtest/replay {symbol, timeframe, decision_msc,
  exit_msc|null, lead_bars=50, tail_bars=20}` → study session with
  `cursor = the bar lead_bars stored bars before decision_msc` (count bars,
  not time — weekends), `range_start = cursor`, `range_end = exit + tail_bars`
  bars (or the last stored bar). Returns the session; the FE opens replay on it
  with the same indicator active (its §1 markers show behind the cursor).

### 1.8 Frontend

- `components/StrategyTester.tsx` — docked under the chart on `Chart.tsx`,
  hidden in replay and when no active indicator has `signal()`. Collapsed:
  one strip (script picker if several, headline, ▲). Expanded (~320 px; below
  `md` a sheet, see memory `mobile-nav-and-chart-sheet`): tabs
  - **Ringkasan** — table IS | OOS | Semua: n, win rate, avg R, total R, PF,
    max DD (R), streaks; OOS column first and emphasised; equity curve (a small
    lightweight-charts line of cumulative R, split time marked); warnings line
    for `rejected / open / spread_fallback / unknown_cost / pending`.
  - **Daftar trade** — rows: side, entry (WIB), exit, reason, R, MAE/MFE;
    sortable by R; click → main chart scrolls to the trade; "Replay" button →
    replay jump (1.7).
  - **Breakdown** — Jam (WIB labels) / Sesi / Hari, toggle OOS | Semua; n,
    win rate, avg R, total R per row.
  - **Pengaturan** — range (date inputs, default last 90 days), split %, SL
    default (ATR×k + length / poin / tanpa), TP R (or none), max hold, sinyal
    lawan menutup, overlap, spread fallback, "Tampilkan di chart". Script
    inputs stay in `IndicatorPanel` (one place).
- One palette / one type scale (memories `frontend-color-tokens`,
  `frontend-type-scale`): no raw hex, no `text-[13px]`.
- `ScriptEditor` HELP: `signal(..., stop=, target=, target_r=)`, `exit(side, cond)`.

---

## 2. Plan (one task = one commit, tests first)

Branch `feat/indicators-tester`, worktree `.worktrees/indicators-tester`
(`ln -s ../../../frontend/node_modules frontend/node_modules`, `uv sync`).

1. **lang: `signal` keywords + `exit()`** — tests in `test_indicator_lang.py`:
   accepted forms; refused: unknown keyword, `target` with `target_r`,
   non-constant `target_r`, `exit` with bad side, exit-only script, `exit = 1`
   (reserved); `lookback` counts `stop`/`target`/`exit` expressions.
2. **engine: stops/targets/exits in `Result`** — tests in
   `test_indicator_engine.py`; **extend the prefix-invariance test** to the new
   series (and the MTF one in `test_indicator_mtf.py`).
3. **simulator** — `tests/test_indicator_backtest.py`, hand-built frames:
   fill at next open; stop-first on a bar hitting both; exit() at next open;
   opposite closes then reverses on the same open; one-at-a-time vs overlap;
   max hold; ATR SL and `target_r` sized from the fill; script stop wins over
   form; wrong-side → rejected; window end → open, not in stats; spread cost
   in R and fallback/unknown counts; MAE/MFE. **No-lookahead guard:**
   `simulate(bars[:k])` reproduces exactly the full run's trades with
   `exit_msc < t_{k-1}` (property test, random k, mixed script with `tf()`).
4. **stats** — same test file: split by decision time, segments, PF `None`
   with no loss, `r None` excluded from R stats, equity, breakdown buckets;
   widen `sequence_stats` typing (existing report tests stay green).
5. **service + route** — extract the loader from `compute` (compute tests
   unchanged and green), `web/backtest.py`, route; tests in
   `tests/test_backtest_service.py` + routes: seeded bars → known trades; cap
   → 400; missing specs → 400; script error → 400 with position; pending.
6. **migration 014 + replay jump** — `origin`; `career_summary` excludes study
   by default (+ `include_study`); `/api/indicators/backtest/replay` counts bars
   across a gap; tests in `test_training_store.py` / routes; migration test as
   the repo's other migrations do.
7. **CLAUDE.md** — rule 9 text (1.5) and the third ungated exception (1.3).
8. **FE** — `lib/indicators.ts` types + api + `normalizeLayout` backtest
   settings; `StrategyTester.tsx` with the four tabs; markers on the live chart;
   replay button; career toggle; HELP. Vitest: layout normalisation, tester
   renders headline/OOS first, trade click scrolls, markers only with a result,
   hidden in replay.
9. **Finish** — HANDOFF entry (≤ 6, archive older), memory `indicators-spec.md`
   (re-sequenced roadmap: tester done; real-trade context deferred),
   `graphify update .`, gates, browser check on a DB copy (EMA cross with
   `stop=`/`target_r=` on XAUUSDc M5, 90 days: panel opens, OOS headline,
   equity curve, trade click scrolls, replay jump opens a study session that
   the career summary excludes). Restart `journal live` + `serve` after merge
   (migration 014).

### Gates

`uv run pytest` · `uv run ruff check src tests scripts` · `uv run mypy` ·
`cd frontend && npx vitest run && npx tsc -p tsconfig.json --noEmit` ·
`uv run journal rebuild --db <copy>` + `journal verify --db <copy>` · paste
outputs. Fast-forward merge, rebuild `frontend/dist` on `main`.

### Gotchas carried

- `npx tsc -b` fails here; use `npx tsc -p tsconfig.json --noEmit`. No
  `Array.prototype.at`.
- Tests import `from indicator_helpers import …` (`walk`, `walk_candles`, `same`).
- BSD `sed` has no `\b`; use `perl -pi -e`.
- `warm_from_msc` convention is `index[lookback]` (one bar conservative), the
  same for every TF.
- Never weaken a prefix-invariance test to make one pass.
