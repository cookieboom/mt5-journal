# Chart Phase D — Training / Replay Mode — Design

**Date:** 2026-07-25
**Branch:** `chart-phase-d-training-replay` (based on `chart-phase-c-settings-panel`)
**Merge chain:** PR #8 (Phase B) → PR #9 (Phase C) → Phase D. Phase D's PR bases on
the Phase C branch so its diff is Phase-D-only, mirroring how C based on B.
**Status:** Approved (brainstorming). Next: implementation plan (`writing-plans`).

## 1. Purpose

A TradingView-style **bar-replay training mode**: replay cached historical candles
bar-by-bar with future bars hidden, place **fake positions** (with optional SL/TP),
have the backend evaluate them against subsequent bars, and store the outcomes in
**new, separate tables** for per-session and cumulative scoring.

This is a **read-your-own-decisions** trainer. Per **CLAUDE.md rule 9** it describes
and evaluates decisions *you* make — it **never** suggests entries, never scores "should
I take this trade". No signal features of any kind.

## 2. Non-negotiable constraints (from CLAUDE.md)

- **Rule 1 / M9:** the web layer never touches the MT5 bridge. Replay reads only
  **cached** candles via the Phase A candle store; missing ranges are filled through the
  existing queue (`candle_requests`), never by a direct bridge call.
- **Rule 2 / data separation:** training results live in **new `training_*` tables**,
  never in `trades` / `deals_raw` / `orders_raw`. `journal rebuild` rebuilds only
  `trades` from raw and **must not** drop or touch training data (durability pattern of
  `app_prefs`). Proven by test.
- **Rule 3:** all timestamps are **epoch-millisecond integers, server-UTC** (offset 0).
  WIB (UTC+7) is display-only. lightweight-charts needs UNIX **seconds** → divide by 1000
  only at the chart boundary.
- **Rule 4:** `NULL` = unknown, `0` = "none set". Fake-position SL/TP use `0` = none set
  (matching `sl_initial`/`tp_initial`); a position with no SL is excluded from R stats.
- **Account (USC / US cents):** every money figure is in cents. Use `money()`, never a
  bare `$`. **R-multiple (unit-free ratio) is preferred** over absolute P&L in analytics.
- **§8:** every reported statistic shows `n`; aggregate buckets with `n < 20` are greyed
  or suppressed.
- **Phase C isolation:** chart settings are GLOBAL (`app_prefs` key `"chart"`). Training
  state and config MUST NOT leak into or read/write chart prefs beyond *reading* them for
  rendering.

## 3. Evaluation engine (core — pure, backend, TDD)

`src/journal/domain/replay_eval.py`. A **pure** function over cached OHLC (no DB, no
bridge — CLAUDE.md rule 7), fixture-tested, tests written before implementation. The
authoritative source of truth; the frontend never re-implements SL/TP detection.

### 3.1 Fill model

- Entry is a **market order filled at the next bar's open**. A decision made while bar `N`
  is the newest revealed bar creates a **pending** position; it fills at `open[N+1]`.
- **Manual close** is symmetric: filled at the **next bar's open** (no lookahead). The
  position shows "closing…" until the next step resolves it.

### 3.2 Per-bar forward fold

On each newly-revealed bar `B` (play = repeated single steps; a multi-bar step evaluates
each bar `old_cursor+1 .. new_cursor` in order):

1. **Fill** any pending position with `decision_bar == B−1` → `entry_price = open[B]`,
   `entry_msc = time[B]`, status `open`.
2. **Evaluate** every open position against the bar's **wicks** `[low[B], high[B]]`:
   - Long: `sl_hit = sl>0 and low[B] ≤ sl`; `tp_hit = tp>0 and high[B] ≥ tp`.
   - Short: `sl_hit = sl>0 and high[B] ≥ sl`; `tp_hit = tp>0 and low[B] ≤ tp`.
   - **Both hit within one bar → SL fills first** (pessimistic; OHLC can't reveal true
     intra-bar order, and an honest trainer never flatters).
   - The **entry bar itself is evaluated** (step 1 then step 2 in the same iteration), so a
     gap or fast move can stop a position out on the bar it opened on.
3. **Exit price = the SL/TP level itself.** Gap-through-level slippage is **not modeled**
   (standard backtest simplification): a stop the bar gapped through fills at the stop
   price, not worse. (Known simplification; a future phase could model gap-open fills.)
4. **End of range** with a position still open → `exit_reason = 'eod'`, unresolved.
   Excluded from win-rate and R stats (unknown outcome, rule-4 spirit); shown in the
   results table as "open (unresolved)".

### 3.3 Money & R (account conventions)

- `signed_move = (exit − entry)` for **buy**, `(entry − exit)` for **sell** (price units).
- `net_profit_USC = signed_move / tick_size × tick_value × volume`, using `symbol_specs`
  for the position's symbol. Commission and swap are **0** on this account (confirmed
  swap-free cent account), so gross = net; no cost term.
- `r_multiple = signed_move / |entry − sl|`, unit-free. **NULL when `sl == 0`** (no SL) →
  excluded from R aggregates (rule 4).
- **MAE/MFE** by **reusing `domain/excursion.py::compute_excursion`** over the `[entry_msc
  .. exit_msc]` bars at the session timeframe → non-negative price distances; `mae_r =
  mae / |entry − sl|`, `mfe_r = mfe / |entry − sl|` (NULL if no SL).

## 4. Schema — migration 006, `SCHEMA_VERSION = 6`

New migration file `src/journal/store/migrations/006_training_tables.sql` (numbered
contiguously after `005`; confirm the exact suffix against the existing files at
implementation time). Two new tables; nothing else altered.

### `training_sessions`
| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `symbol` | TEXT | exact MT5 symbol, e.g. `XAUUSDc` (rule 11) |
| `symbol_base` | TEXT | normalised, e.g. `XAUUSD` (rule 11) |
| `timeframe` | TEXT | `"M15"` etc., matching `candles.timeframe` |
| `range_start_msc` | INTEGER | historical window start, epoch-ms UTC |
| `range_end_msc` | INTEGER | historical window end, epoch-ms UTC |
| `cursor_msc` | INTEGER | newest revealed bar's time; forward-only |
| `status` | TEXT | `'active'` \| `'ended'` (CHECK) |
| `created_at_msc` | INTEGER | |

### `training_positions`
| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `session_id` | INTEGER | FK → `training_sessions(id)` ON DELETE CASCADE |
| `direction` | TEXT | `'buy'` \| `'sell'` (CHECK) |
| `volume` | REAL | lots |
| `decision_msc` | INTEGER | bar time when opened/decided |
| `entry_msc` | INTEGER NULL | fill time; NULL while pending |
| `entry_price` | REAL NULL | fill price; NULL while pending |
| `sl` | REAL NOT NULL DEFAULT 0 | `0` = none set (rule 4) |
| `tp` | REAL NOT NULL DEFAULT 0 | `0` = none set (rule 4) |
| `exit_msc` | INTEGER NULL | |
| `exit_price` | REAL NULL | |
| `exit_reason` | TEXT NULL | `'tp'`\|`'sl'`\|`'manual'`\|`'eod'` (CHECK) |
| `status` | TEXT | `'pending'`\|`'open'`\|`'closed'` (CHECK) |
| `net_profit` | REAL NULL | USC, signed; set on close |
| `r_multiple` | REAL NULL | NULL if no SL |
| `mae` / `mfe` | REAL NULL | price distances |
| `mae_r` / `mfe_r` | REAL NULL | ratios; NULL if no SL |
| `created_at_msc` | INTEGER | |

Indexes on `session_id` and `status`. Money/prices are `REAL`, compared with tolerance
(rule 5).

**Rebuild-safety:** a test asserts `training_sessions`/`training_positions` rows survive a
`journal rebuild` (which only drops+rebuilds `trades`). Migration test asserts
`SCHEMA_VERSION == 6`, fresh-DB == migrated-DB, and both new tables present.

## 5. API — `/api/training/*`

All endpoints read/write only the DB and the cached candle store. No bridge access.

- `POST /api/training/sessions` `{symbol, timeframe, range_start_msc, range_end_msc,
  cursor_start_msc?}` → creates a session (cursor at `cursor_start_msc` or a warmup offset
  inside the range). **Enqueues a candle fill** for `[range_start, range_end]` at `tf` via
  `candle_requests`; the response reports coverage/pending so the UI can show
  "preparing data" until the range is fully cached.
- `GET /api/training/sessions/{id}` → `{session, positions}` (rehydrate on reload).
- `GET /api/training/sessions?status=` → list (history / career view).
- `DELETE /api/training/sessions/{id}` → discard a throwaway attempt (cascade positions).
- `POST /api/training/sessions/{id}/step` `{n}` → advance cursor by `n` bars. Backend
  loads the cached candles for the crossed bars, runs the pure evaluator (fill pending,
  evaluate open), persists changes, returns `{cursor_msc, events:[fills/exits], positions}`.
- `POST /api/training/sessions/{id}/positions` `{direction, volume, sl, tp}` → create a
  **pending** position at the current cursor (`decision_msc = cursor_msc`).
- `POST /api/training/sessions/{id}/positions/{pid}/close` → mark for manual close (fills
  at next bar's open on the next step).
- `POST /api/training/sessions/{id}/end` → mark session `ended`; any still-open positions
  become `exit_reason='eod'`.
- `GET /api/training/summary` → cumulative career stats (n, win%, avg R, total R, avg
  MAE/MFE in R), **§8-gated** (aggregates with `n < 20` flagged for greying). Per-session
  summary is derivable from `GET /sessions/{id}`.

`store/training_store.py` holds the pure DB access (mirroring `prefs_store.py`): session
CRUD, position lifecycle, and summary aggregation queries. Tested standalone.

## 6. Frontend — Replay mode on `/chart` (isolated)

TradingView model: a **Replay button** on the chart toolbar; entering overlays replay
controls; exiting **restores the exact pre-training chart state**.

- **Config modal** on Replay click: symbol, timeframe, date range, playback speed →
  `POST /sessions` → enter replay.
- **`hooks/useReplaySession.ts`** owns ALL replay state (session id, cursor, positions,
  playback loop, events). It **reads** chart prefs for rendering only and has **no write
  path** to `useChartPrefs` / `app_prefs`. Training config persists server-side in
  `training_*`, never in the `"chart"` prefs key.
- **Snapshot on enter / restore on exit:** capture `{symbol, tf, prefs, visible range}`
  entering replay; restore on exit so the chart returns to exactly its prior condition.
- **`REPLAY` badge** prominently visible while active.
- Reuses **`CandleChart`** fed candles **clipped to `cursor_msc`** (future bars never
  drawn). Fake-position SL/TP/entry price-lines drawn via the existing `liveLines` overlay
  from replay state — never mixed with real-trade overlays.
- **Playback control bar:** `|< Reset · |> Step · ▶ Play/Pause · speed · >> Jump`, with a
  cursor date/time indicator. **Reset** = start a fresh attempt over the same range;
  previously-closed trades remain in history (delete the session to discard a throwaway).
- **Order ticket** (Buy/Sell, volume, SL, TP inputs) · **Positions panel** (open positions
  marked-to-current-bar for unrealized display + Close button; closed list) · **Summary
  panel** (per-session + career, §8 greyed).
- **`lib/replay.ts`**: pure **display** helpers only (clip-to-cursor, unrealized-for-display
  formatting) — **no SL/TP detection in TS** (backend authoritative, no logic duplication).
  vitest-tested.

## 7. Testing / Definition of done

- **TDD** `domain/replay_eval.py`: long & short; SL-only, TP-only, no-SL; both-hit-in-one-bar
  (SL-first); entry-bar immediate stop; gap-through-level; manual close (next-bar-open);
  eod-unresolved; R and P&L per `symbol_specs` (XAUUSDc, BTCUSDc, EURUSDc); MAE/MFE reuse.
- `store/training_store.py`: roundtrip, cascade delete, status lifecycle, summary/§8 gating.
- Migration test: `SCHEMA_VERSION == 6`, fresh == migrated, tables present, **rebuild
  leaves training rows intact**.
- API lifecycle tests in `tests/test_api.py`: session create/step/open/close/end/summary.
- vitest: `lib/replay.ts` helpers; `useReplaySession` reducer logic; component smoke.
- **Done when:** `uv run pytest` green (output pasted), `npm --prefix frontend test` green,
  `npm --prefix frontend run build` 0 errors, and `uv run journal rebuild` still succeeds
  with `training_*` rows intact.

## 8. Out of scope (YAGNI)

- Rewindable/scrub cursor (forward-only; Reset for a fresh attempt).
- Limit/stop pending orders (market entry only, at next bar's open).
- Gap-open slippage modeling (fills assume the exact SL/TP level).
- Commission/swap modeling (0 on this account).
- Any signal/recommendation feature (rule 9).
