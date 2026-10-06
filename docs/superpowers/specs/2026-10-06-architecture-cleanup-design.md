# Architecture cleanup: web hardening, layering, file size

**Status:** approved by the human 2026-10-06 ("kerjakan satu per satu")
**Branch:** `refactor/architecture-cleanup`

## Problem

A design review on 2026-10-06 found the data/adapter/order core sound and the
web layer the weakest part. Two findings were reproduced, not inferred:

- `POST /api/watch` with `Host: rebind.attacker.example` → **200**. Nothing
  checks `Host` or `Origin`, so a DNS-rebinding page can reach every route,
  including `/api/live/open`, which enqueues a real order. Binding to loopback
  (`serve._is_loopback`) does not stop this: the browser is the one connecting.
- `POST /api/watch` with a non-JSON body → **500** (`TypeError` on
  `body["symbol"]`). 11 routes take an untyped `Body(...)` and index it by hand.

The rest is structure: business logic and raw SQL in `web/`, imports that
point upward (`domain → render/analytics`, `store → web`), `web/app.py` (1,125
lines, every route inside one `create_app` closure), `cli.py` (1,425 lines,
~230 of them thread orchestration for `journal live`), and `connect()`
running migrations on every web request.

## Changes, one commit each

1. **Local-only guard.** One HTTP middleware in `web/app.py`: a request whose
   `Host` is not loopback → 403; a state-changing method (`POST/PUT/PATCH/
   DELETE`) whose `Origin` is present and not loopback → 403. A missing
   `Origin` is allowed (curl, the CLI); browsers always send it cross-origin.
   One loopback set, shared with `serve`'s bind check.
2. **Typed request bodies.** Pydantic models (already installed with FastAPI —
   no new dependency) for every untyped `Body(...)`. Bad input → 422 from
   FastAPI, never 500.
3. **Layering.** `size_order` → `domain/risk.py`; `choose_timeframe`/
   `window_for` → `domain/`; `_MIN_N` → `domain/`; `stale_dist_reason` out of
   `web/app.py` so `store/health.py` stops importing the web; direct table
   writes in `web/` move into their `store/` module; the private
   `render.chart._server_offset_s` becomes public in one home.
4. **Routers.** `web/app.py` split into one `APIRouter` per area; `create_app`
   only wires them. Route paths, methods and payloads unchanged.
5. **Daemon out of the CLI.** `journal live`'s thread/queue/lock orchestration
   moves to `ingest/live.py` (`run_daemon`); the command parses flags and
   prints.
6. **Migrate once.** `connect()` stops migrating; `journal serve`/`live` and
   every CLI command migrate at start; the web refuses to run against a schema
   newer than its code.
7. **Chart page.** Split `Chart.tsx`/`CandleChart.tsx` along their existing
   modes (orders, paper, replay, drawings) without behaviour change.
8. **Tooling** (ruff/mypy) needs the human's OK (rule 8) — asked, not assumed.

## Non-goals

No new runtime dependency, no API shape change, no schema change, no ORM, no
async rewrite. Every existing test passes unchanged except where a test built
a client against Host `testserver` (now `127.0.0.1`).

## Verification

Per commit: full `pytest`, and for frontend commits `vitest` + `tsc -b`. At the
end: `journal rebuild` on a snapshot, `journal status`, frontend build.
