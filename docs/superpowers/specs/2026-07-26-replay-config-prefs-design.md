# Persist replay config popup preferences — design

**Date:** 2026-07-26
**Status:** approved (design)
**Branch base:** `chart-phase-d-training-replay`

## Problem

The replay config popup (`ReplayConfigModal.tsx`) resets to hardcoded defaults
(`XAUUSDc` / `M15` / blank date / 300 bars / speed 4) every time it opens. A user
who repeatedly replays with the same specs must re-enter all five fields each
time. Chart appearance already persists across sessions via a DB-backed prefs
store; replay config should get the same treatment.

## Goal

Remember the popup's five inputs — **symbol, timeframe, start date, history bars,
speed** — so the next replay opens pre-filled with the last-launched config.
Persistence mirrors the existing chart-prefs architecture (DB-backed +
localStorage mirror), per user decision.

## Non-goals

- No new UI controls, buttons, or "reset defaults" affordance in this pass.
- No change to the replay evaluator, cursor/range math, or `ReplayConfig` shape.
- No server-side validation of the prefs blob (server stays schema-agnostic,
  exactly like chart prefs — the client owns the schema).

## What is persisted

The **raw form inputs**, not the derived `ReplayConfig`:

```ts
interface ReplayFormPrefs {
  version: 1;
  symbol: Sym;          // oneOf(SYMBOLS)
  timeframe: Timeframe; // oneOf(TIMEFRAMES)
  startDate: string;    // "yyyy-mm-dd" or "" (kept only if valid pattern)
  historyBars: number;  // clamped [100, 1000]
  speed: number;        // clamped [1, 10]
}
```

`startDate` is persisted and **pre-filled** on reopen (user decision). It remains
freely editable text the user can overwrite. Cursor/range are still computed at
submit time in the modal from these inputs — nothing about that math moves.

## Architecture (mirrors chart prefs)

| Layer | Chart prefs (existing) | New replay prefs |
|---|---|---|
| DB | `app_prefs` key `"chart"` | `app_prefs` key `"replay"` — **no migration** (table + generic `get_pref`/`set_pref` already exist) |
| Backend wrappers | `get_chart_prefs` / `set_chart_prefs` | add `get_replay_prefs` / `set_replay_prefs`, `REPLAY_KEY = "replay"` in `prefs_store.py` |
| API | `GET`/`PUT` `/api/chart/prefs` | add `GET`/`PUT` `/api/replay/prefs` in `web/app.py` |
| lib | `chartPrefs.ts` | new `frontend/src/lib/replayPrefs.ts` |
| hook | `useChartPrefs` | new `frontend/src/hooks/useReplayPrefs.ts` |

### `prefs_store.py`

Add alongside the chart wrappers (the generic core is unchanged):

```python
REPLAY_KEY = "replay"

def get_replay_prefs(conn): ...   # json.loads(get_pref(conn, REPLAY_KEY)) or None
def set_replay_prefs(conn, prefs): ...  # set_pref(conn, REPLAY_KEY, json.dumps(prefs), now_ms())
```

### `web/app.py`

Two routes copied from the chart-prefs pair, pure DB (never touches the bridge —
M9 boundary holds):

- `GET  /api/replay/prefs` → `{"prefs": get_replay_prefs(conn)}` (null until first save)
- `PUT  /api/replay/prefs` → body stored verbatim; returns `{"ok": true, "updated_ms": ts}`

### `frontend/src/lib/replayPrefs.ts`

Mirrors `chartPrefs.ts`, minus the URL-selection helpers:

- `DEFAULT_REPLAY_PREFS` — `{ version:1, symbol:"XAUUSDc", timeframe:"M15", startDate:"", historyBars:300, speed:4 }` (today's modal defaults).
- `normalizeReplayPrefs(raw)` — coerce any stored/DB/corrupt object to a valid
  `ReplayFormPrefs`: `oneOf` for symbol/timeframe, `clampInt` for
  historyBars/speed, `startDate` kept only if it matches `/^\d{4}-\d{2}-\d{2}$/`.
- `loadReplayPrefs(store=localStorage)` / `saveReplayPrefs(s, store)` — localStorage read/write under key `"mt5j.replay.config"`.
- `reconcileReplayPrefs(local, dbParsed, localExists)` — same rule as chart:
  DB present → DB wins (normalized); DB absent → keep local, seed DB if the
  browser had a stored row (`shouldImport`).
- `STORAGE_KEY` exported for the existence probe.

### `frontend/src/hooks/useReplayPrefs.ts`

Mirrors `useChartPrefs`, with the write triggered on **submit** rather than
per-keystroke (the modal is short-lived, not always-mounted):

- On mount: instant `loadReplayPrefs()`, then `GET /api/replay/prefs`, reconcile
  (DB authoritative), `setPrefs` + `saveReplayPrefs`, and `PUT` if `shouldImport`.
- `save(next: ReplayFormPrefs)`: `setPrefs` + `saveReplayPrefs` (instant local) +
  fire-and-forget `PUT /api/replay/prefs` (no debounce needed — one call per launch).
- Returns `{ prefs, save }`. No `reset` (out of scope).

### `ReplayConfigModal.tsx`

- New prop `initial: ReplayFormPrefs`; the five `useState` initializers seed from
  `initial.*` instead of hardcoded literals.
- `onStart` signature gains the form snapshot: `onStart(cfg: ReplayConfig, form: ReplayFormPrefs)`.
  `submit()` builds `cfg` exactly as today, then calls
  `props.onStart(cfg, { version:1, symbol, timeframe:tf, startDate, historyBars, speed })`.

### `Chart.tsx`

- `const replayPrefs = useReplayPrefs();`
- Pass `initial={replayPrefs.prefs}` to `<ReplayConfigModal>`.
- In `onStart(cfg, form)`: call `replayPrefs.save(form)` before/after
  `replay.start(cfg)` (order irrelevant; save is fire-and-forget).

## Data flow

```
open modal ──initial=prefs──> fields seeded from last-launched config
   │
submit ──> cfg (derived: cursor/range) ──> replay.start(cfg)
   └──────> form snapshot ──> replayPrefs.save(form)
                                   ├─ localStorage (instant, authoritative)
                                   └─ PUT /api/replay/prefs (fire-and-forget)
next open ──GET /api/replay/prefs──> reconcile (DB wins) ──> fields pre-filled
```

## Error handling

- Offline / dev (no server): PUT fails silently; localStorage is the source of
  truth. GET failure keeps localStorage state. Identical to chart prefs.
- Corrupt/legacy blob: `normalizeReplayPrefs` fills every field from defaults;
  an invalid `startDate` collapses to `""`.
- localStorage quota / private mode: `saveReplayPrefs` swallows the throw.

## Testing

- **`tests/test_prefs_store.py`** — roundtrip: `get_replay_prefs` is `None`
  before save; after `set_replay_prefs`, `get_replay_prefs` returns the parsed
  object and does not collide with the `"chart"` key.
- **`tests/test_web.py`** — `GET /api/replay/prefs` returns `{"prefs": null}`
  initially; `PUT` a blob returns `{"ok": true}` with an `updated_ms`; a
  subsequent `GET` echoes the blob.
- **vitest (`replayPrefs`)** — `normalizeReplayPrefs` clamps out-of-range
  bars/speed, rejects a bad symbol/timeframe and bad `startDate`;
  `reconcileReplayPrefs` returns DB value when present and `shouldImport=true`
  only when local existed and DB was absent.

## Definition of done

`uv run pytest` green (output pasted), vitest green, `npm run build` clean,
`uv run journal rebuild` still succeeds (prefs are not derived from raw, so
rebuild must not touch them). Manual: launch a replay with non-default specs,
close, reopen → fields pre-filled with those specs.
