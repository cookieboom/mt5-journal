# Indicators §2 — multi-timeframe `tf()` — design + implementation plan

**Status:** designed 2026-10-07, not started. Umbrella spec:
`2026-10-07-indicators-design.md` (§2 there is the outline; this file
supersedes it). Next session: read this file, then `pipeline` skill → branch +
worktree `feat/indicators-mtf` → tasks below in order, TDD (rule 7). The plan
section is the plan; no separate plan file needed.

---

## 0. What §1 already built (merged to `main` at `1098f86`)

Read these before touching anything. They are small.

| File | What it is | Key contract |
|---|---|---|
| `src/journal/domain/indicators/builtins.py` | Function set + `REGISTRY: dict[str, Builtin]` | `Builtin(fn(frame, *args), params: tuple[Param], cost(consts, tf_ms) -> bars)`. Three-valued logic helpers `tri`, `logical_and/or/not` (1.0/0.0/NaN). |
| `src/journal/domain/indicators/lang.py` | Parser + validator | `parse(src) -> Program` (`inputs`, `steps: [Step(kind, target, expr)]`, `plots`, `hlines`, `signals`, `consts`). `resolve_consts(p, inputs)`, `call_consts(call, consts)`, `lookback(p, consts, tf_ms) -> int` (cap `MAX_LOOKBACK=20_000`), `error_at(node, msg) -> ScriptError(line, col)`. Validation lives in `_expr` / `_call` / `_is_const`. |
| `src/journal/domain/indicators/engine.py` | Interpreter | `evaluate(p, frame, inputs, tf_ms) -> Result(plots, hlines, signals)`; inner `ev(node)` dispatches on AST node type. `base_series(frame)`. `BUDGET_S = 2.0`. |
| `src/journal/domain/indicators/frame.py` | `to_frame(list[Candle]) -> DataFrame` | index `time_msc` (bar OPEN), float cols `open high low close volume spread`. |
| `src/journal/domain/indicators/library.py` + `library/*.ind` | Shipped scripts, id `lib:<stem>`, name = first-line `# comment`. | |
| `src/journal/web/indicators.py` | Service | `compute(conn, *, script|source, inputs, symbol, timeframe, from_ms, to_ms, session_id, include_forming)`; `_warm_bars(conn, symbol, tf, from_ms, need)` (counts bars, crosses gaps); replay clip via `reveal_end = cursor + tf_ms(session tf)`; `validate(source)` returns `lookback` per chart TF. Scripts/layout in `app_prefs` (`indicator_scripts`, `indicator_layout`). |
| `src/journal/web/routes/indicators.py`, `web/schemas.py` | `/api/indicators/{scripts,validate,compute,layout}` | Script error → 400 with `script_error {line,col,message}`. |
| `frontend/src/lib/indicators.ts` | Types, `normalizeLayout`, `mergeTail`, `clipResult`, `paneAssignments`, `toLineData`, `signalMarkers`, `indicatorsApi` | |
| `frontend/src/hooks/useIndicators.ts` | Full fetch + 2-bar tail fetch, clip to last drawn bar | |
| `frontend/src/hooks/useIndicatorSeries.ts` | Draws plots onto the chart (panes) | |
| `frontend/src/components/IndicatorPanel.tsx`, `ScriptEditor.tsx` | Side panel + editor (`HELP` string lists the syntax) | |
| Tests | `tests/indicator_helpers.py` (`walk`, `same`, `MINUTE`), `tests/test_indicator_{builtins,lang,engine,library}.py`, `tests/test_indicators_{service,routes}.py`, FE `*.indicators*.test.*`, `IndicatorPanel.test.tsx`, `useIndicators.test.ts` | Prefix-invariance property tests = the no-lookahead guard. Never weaken. |

Store facts measured 2026-10-07 (`data/journal.db`): XAUUSDc has native
M1 (770k), M5, M15, H1 (24k), H4 (1.1k, only since 2026-01-30), D1 (255).
BTCUSDc: M1/M5/M15 only. `candles_store.load_bars` returns native rows if any
exist in the range, else aggregates from M1 (`resample_m1` with a coverage
guard), else `[]`.

---

## 1. Design

### 1.1 Syntax

```python
trend = tf("H1", ema(close, 50))          # H1 EMA, on any lower chart TF
plot(trend, title="EMA50 H1", color="mark-amber")
signal("long", crossover(close, trend) and tf("H4", rsi(close, 14)) > 50)
```

`tf(TIMEFRAME, expr)`. Rules, all enforced in `lang`:
- `TIMEFRAME` is a string literal in `adapter.base.TIMEFRAMES`.
- `expr` is evaluated on the higher-TF bars. Inside it, only base series
  (`SERIES`), constants/inputs, builtins and `x[n]` are allowed. **Variables
  assigned at chart TF are refused** (they live on another time axis); the
  error says "assign it inside tf() instead". No `tf()` nested in `tf()`.
- The target TF must be **strictly higher** than the chart TF. That is only
  known once the chart TF is known, so it is checked in `lookback()` /
  `evaluate()` (both receive `tf_ms`), not in `parse()`. Error: "tf('M5', …)
  on an H1 chart: tf() only reaches higher timeframes".
- `tf()` result is a chart-TF series and composes like any other value.

### 1.2 The closed-bar mapping (the whole point — lookahead trap)

An HTF bar `[T, T+H)` is visible on chart bar `[t, t+L)` **only when
`T + H <= t + L`**: it closed by the time the chart bar closed. Value on a
chart bar = value of the latest such HTF bar. Never the forming HTF bar.
Implementation: `pd.merge_asof` on close times (`htf_close = T+H`,
`ltf_close = t+L`, `direction="backward"`, `allow_exact_matches=True`).
Consequences, both intended: the line steps once per HTF close; on a live
chart the current HTF bar never contributes until it closes.

Live forming chart bar: its `t+L` lies in the future, so the rule above would
admit an HTF bar closing at that same instant — which has not closed. For the
forming bar use `t` (its open) instead of `t+L`. The engine gets
`forming_msc` (or a bool) to know which row that is.

Gaps: an HTF bar missing from the store simply never maps (the previous one
carries forward — the latest CLOSED value is still the truth). If the gap is
longer than `2·H`, emit NaN instead of carrying forward (stale ≠ known).
Memory `chart-gaps-are-genuine-market-closures`: weekends are real gaps; the
`2·H` cut uses **bar count**, i.e. "the HTF bar before this one in the store is
not the immediately preceding bucket AND chart time is > 2·H past its close".
Keep it simple: carry forward across market closures (weekend), NaN only when
the HTF series itself is missing for > 2 HTF buckets while chart bars exist.

### 1.3 Lookback

`lookback()` becomes per-TF. Return shape stays an `int` for the chart TF (no
caller break) plus a new `htf_lookback(p, consts, tf_ms) -> dict[str, int]`.
- Inside `tf(X, e)`: cost of `e` counted in **X bars** → `htf[X] = max(...)`.
- On the chart TF, a `tf()` call costs `ceil(H / L)` bars (so the first window
  bar already has a closed HTF bar mapped) — plus nothing else.
- Same `MAX_LOOKBACK` cap per TF.

### 1.4 Data loading (service)

`web/indicators.compute`:
1. collect the TFs referenced (`Program.htfs: set[str]`, filled by the parser);
2. for each: `view = load_bars(symbol, X, from_ms - H, to_ms)` +
   `_warm_bars(symbol, X, from_ms, htf_lookback[X])`;
3. replay: drop HTF bars with `T + H > reveal_end` (belt and braces — the
   mapping already excludes them, but the HTF frame must not even contain them);
4. pass `htf_frames: dict[str, DataFrame]` to `evaluate`;
5. HTF short of warm-up → same `pending` + `request_candles` as §1, for that TF.
   Response gains `warm_from_msc` = max over all TFs (the latest TF to become
   warm decides).

No new resampler: `load_bars` already falls back to M1. Native-partial ranges
(H4 native only since 2026-01-30) are a known limitation — note it, do not
solve (YAGNI; the H4 native store fills going forward).

### 1.5 Engine

`evaluate(p, frame, inputs, tf_ms, htf: dict[str, DataFrame] | None = None,
forming_msc: int | None = None)`. In `ev()`:
`Call` with `func.id == "tf"` → evaluate `args[1]` with a nested evaluator bound
to `htf[X]` (same `ev` logic, different `base_series`, empty variable env) →
map with §1.2 → return chart-TF Series. Cache per `(X, ast.dump(expr))` within
one evaluate (same `tf()` used in plot and signal).

### 1.6 Frontend

Nothing structural: results are chart-TF series. Only:
- `ScriptEditor.tsx` `HELP`: add the `tf("H1", expr)` line.
- `validate` response `lookback` per chart TF: a TF where the script is invalid
  (tf() not higher) returns `null`; editor shows "tidak berlaku di M5/H1" only
  if needed — keep minimal: render `lookback.M5 ?? "—"`.
- Library: add `library/ema_htf.ind` ("EMA (TF tinggi)") —
  `htf = input(...)` cannot be a string (inputs are numeric/bool only), so the
  shipped script hard-codes `tf("H1", ema(close, length))`.

---

## 2. Plan (one task = one commit, tests first)

Branch `feat/indicators-mtf`, worktree `.worktrees/indicators-mtf`
(`ln -s ../../../frontend/node_modules frontend/node_modules`, `uv sync`).

1. **lang: parse `tf()`** — `lang.py`: in `_expr`/`_call`, special-case
   `func.id == "tf"` before the REGISTRY lookup (it is not a builtin; add
   `"tf"` to `OUTPUTS`-style reserved names so it cannot be assigned). Validate
   literal TF, arity 2, no keywords, inner scope (new `_expr(..., scope="htf")`
   flag: Names must be `SERIES` or `p.consts`; nested `tf` refused). Record
   `Program.htfs`. Tests in `tests/test_indicator_lang.py`: accepted forms;
   refused — non-literal TF, unknown TF, chart variable inside, nested tf,
   keyword, wrong arity.
2. **lang: lookback per TF** — `lookback()` must skip costs inside `tf()` (add
   `ceil(H/L)` instead, and raise if `H <= L`); new `htf_lookback()`. Tests:
   `tf("H1", ema(close, 50))` on M5 → chart 12, H1 200; on H1 chart → error.
3. **engine: mapping helper** — pure function in `engine.py` (or new
   `domain/indicators/mtf.py` if > ~40 lines):
   `map_closed(htf_values: Series, htf_ms, ltf_index, ltf_ms, forming_msc) ->
   Series`. Tests (`tests/test_indicator_mtf.py`): exact-close boundary
   included; forming chart bar never sees the HTF bar closing at its close;
   carry-forward across a weekend; NaN after > 2 missing HTF buckets.
4. **engine: evaluate `tf()`** — wire into `ev()`, cache, `htf`/`forming_msc`
   params. Tests: values equal a hand-built H1 EMA mapped; **prefix-invariance
   extended**: build M1 `walk(…)` (`tests/indicator_helpers.py`), resample to
   M5 and H1 with `domain.resample.resample_m1` (needs `Candle`s — add a
   `walk_candles` helper returning the list before `to_frame`); for random k,
   evaluating with chart bars `[:k]` and HTF bars clipped to
   `T+H <= t_k + L` equals the full result `[:k]`. Must cover a script mixing
   chart-TF and `tf()` series.
5. **service + library** — `web/indicators.py`: load per-HTF frames, warm-up,
   replay clip, pending, `warm_from_msc` max; `validate` lookback map with
   `None` for invalid TFs. `library/ema_htf.ind`. Tests in
   `tests/test_indicators_service.py`: seed M5 + H1 (use `seed(conn, n, tf=,
   step=)` already there); replay session with cursor mid-hour → the H1 value
   stays the previous hour's; forming: no unclosed H1; tf() lower than chart →
   400 via routes test.
6. **FE** — `ScriptEditor.tsx` HELP line + `lookback` nullable in
   `lib/indicators.ts` `ValidateResult` type; adjust editor text. `npx vitest`.
7. **Finish** — HANDOFF entry (keep ≤ 6, archive older to
   `docs/handoff-archive.md`), memory `indicators-spec.md` (§2 done),
   `graphify update .`, gates, browser check on a DB copy
   (`sqlite3 data/journal.db ".backup '<scratchpad>/journal.db'"`, then
   `JOURNAL_CACHE_DIR=<scratchpad>/cache uv run journal serve --db <copy> --port 8765`,
   `npm --prefix frontend run build` first): M5 chart with
   `tf("H1", ema(close, 50))` steps once per hour; replay stepping inside an
   hour does not move it.

### Gates (definition of done)

`uv run pytest` · `uv run ruff check src tests scripts` · `uv run mypy` ·
`cd frontend && npx vitest run && npx tsc -p tsconfig.json --noEmit` ·
`uv run journal rebuild --db <copy>` + `journal verify --db <copy>` · paste the
outputs. Then fast-forward merge, rebuild `frontend/dist` on `main`.

### Gotchas carried from §1

- `npx tsc -b` fails on this repo (`tsconfig.node.json` noEmit); use
  `npx tsc -p tsconfig.json --noEmit`. `Array.prototype.at` is not in the TS
  lib — use index access.
- Tests import shared helpers as `from indicator_helpers import …` (tests/ has
  no `__init__.py`).
- BSD `sed` has no `\b`; use `perl -pi -e`.
- Wilder smoothing needs 8n warm-up, EMA 4n — the warm-up test
  (`test_cost_is_enough_warmup`) will catch a wrong cost; do not loosen it.
- `ponytail:` comments mark known ceilings (e.g. `_warm_bars` on M1-aggregated
  TFs).

---

## 3. After §2

§3 (indicator context of real trades + replay positions, §8-gated for real
trades) and §4 (backtest on `replay_eval.step_bar`, IS/OOS, `backtest_runs`
migration, replay jump per signal and per period, `training_sessions.origin`,
Rule 9 amendment in `CLAUDE.md`) — outlines in the umbrella spec.
