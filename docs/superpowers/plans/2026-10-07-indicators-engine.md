# Plan — indicators spec §1 (engine, script language, chart)

Spec: `docs/superpowers/specs/2026-10-07-indicators-design.md` §1.
Branch `feat/indicators-engine`. One task = one commit. TDD (rule 7).
Deleted in the branch's last commit.

Deviation from spec §1.4, decided at planning time: user scripts and the layout
live in `app_prefs` (keys `indicator_scripts`, `indicator_layout`), not in a new
table. Reason: no migration means no schema bump, so a running `journal live`
daemon on older code is not refused. `source_hash` is computed from the source
when spec 4 needs it. The spec is updated in task 1.

1. **lang** — `domain/indicators/lang.py`: `parse(source) -> Program`
   (inputs, statements, plots, hlines, signals, lookback). Whitelist validator,
   `ScriptError(line, col, msg)`, limits. Tests: `tests/test_indicator_lang.py`.
2. **builtins** — `domain/indicators/builtins.py`: the v1 function set, pure
   pandas, plus a per-function lookback cost. Tests: golden values, NaN for
   NULL sources, and prefix invariance over every builtin.
3. **engine** — `domain/indicators/engine.py`: `evaluate(program, bars,
   inputs) -> Result`. Applies inputs (clamped), type checks (bool vs float),
   and series-length broadcasting. Tests: end-to-end scripts, prefix
   invariance, input clamping.
4. **library** — `domain/indicators/library/*.ind` (sma, ema, ema_cross, bb,
   rsi, macd, atr, stoch, vwap, donchian, adx) + `library.py` loader. Test:
   every library script parses and is prefix-invariant.
5. **store + service** — `prefs_store` keys; `web/indicators.py`
   (validate, compute with warm-up, session clip, forming tail, scripts CRUD,
   layout). Tests: `tests/test_indicators_web.py`.
6. **routes** — `web/routes/indicators.py` + pydantic bodies. Tests through
   the TestClient: 400 with line/col, session clip, forming ignored in replay.
7. **FE lib** — `lib/indicators.ts`: types, API calls, `normalizeLayout`,
   result → series data, hover lookup. Tests.
8. **FE chart** — `CandleChart` `indicators` prop: series per plot, panes,
   hlines, replay signal markers, re-attach on chart-type switch. Tests.
9. **FE hook + wiring** — `useIndicators` (window, live tail poll, replay
   cursor), wire into `Chart.tsx`, and values in `ChartInfoPanel`.
10. **FE panel + editor** — `IndicatorPanel` (attach, toggle, inputs, remove,
    reorder) and `ScriptEditor` (textarea, debounced validate, line errors);
    toolbar button.
11. **Finish** — HANDOFF entry, memory, `graphify update .`, delete this plan.
