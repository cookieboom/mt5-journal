# M9 — Live positions, trade interaction, auto-ingest on close, UI redesign

**Status:** plan, not yet approved. Nothing below has been implemented.

Three asks, in the user's words:

1. Track currently-open trades and interact with them (set SL/TP, close, add lots).
2. When a position closes, its trade data still is not added to the journal automatically.
3. The current frontend is below standard — make it modern and informative.

They are **not independent**. (1) and (2) are the same missing piece — a
long-running process that owns the bridge — and (3) is a consumer of both. The
phase order below reflects that; the redesign is deliberately last so it styles
the final data model, not the current one.

---

## Phase 0 — Discovery findings (already done; do not re-derive)

Everything here was read from the repo and the installed bridge on 2026-07-23.
Cite this section instead of guessing.

### 0.1 The bridge CAN trade. Sources verified.

`.venv/lib/python3.12/site-packages/siliconmetatrader5/__init__.py` (v1.2.3):

| API | Line | Notes |
|---|---|---|
| `order_send(request)` | 765 | takes ONE dict, no kwargs. Returns the terminal's `OrderSendResult` over rpyc — **not** obtained/copied (no `obtain=True`), so read its fields immediately and convert to our own dataclass inside `live.py`. |
| `order_check(request)` | 748 | same dict shape; returns `MqlTradeCheckResult`. The dry-run. |
| `order_calc_margin` / `order_calc_profit` | 742 / 745 | available if needed for a pre-trade margin display. |
| `positions_get()` | 783 | already wrapped by `LiveMT5Client.positions_get`. |

Constants (all in the same file, class-level, `__init__.py:142-147` and
`:222-251`) — **these are MT5 values and must never leave `adapter/`
(CLAUDE.md rule 12)**:

- `TRADE_ACTION_DEAL = 1` (market order — used for close and for add-lots),
  `TRADE_ACTION_SLTP = 6` (modify SL/TP of an open position).
- `ORDER_TYPE_BUY = 0`, `ORDER_TYPE_SELL = 1` (`:68-69`).
- `ORDER_FILLING_FOK = 0`, `IOC = 1`, `RETURN = 2` (`:88-91`).
- `TRADE_RETCODE_DONE = 10009`, `DONE_PARTIAL = 10010`, `PLACED = 10008`,
  plus the ~25 failure codes at `:222-251` (`INVALID_STOPS = 10016`,
  `MARKET_CLOSED = 10018`, `NO_MONEY = 10019`, `REQUOTE = 10004`,
  `INVALID_FILL = 10030`, `NO_CHANGES = 10025`, …).

**Anti-pattern guard:** MQL5 docs describe `order_send` with keyword arguments in
some examples. **This bridge does not accept them** — `order_send(self, request)`
is positional, single-argument (line 765). Pass one dict.

### 0.2 What the codebase does NOT have yet

Verified by reading, not assumed:

- **No migration mechanism.** `store/db.py:48` `connect()` applies `schema.sql`
  **only when the DB is fresh** (`_is_fresh` = no `schema_version` table).
  `SCHEMA_VERSION = 1`, and `src/journal/store/migrations/` **does not exist**
  despite being listed in CLAUDE.md's layout. Any new table in this plan will
  **silently not appear in the live `data/journal.db`** without Phase 1.
- **No trade-execution surface anywhere.** `adapter/base.py`'s `MT5Client`
  Protocol is read-only: `account_info`, `symbol_info`, `symbol_info_tick`,
  `symbols_get`, `copy_rates_range`, `history_deals_get`, `history_orders_get`,
  `positions_get`. Nothing writes to the broker.
- **The web layer never imports the adapter.** `web/app.py` module docstring
  states it, `web/views.py` restates it. This is an M7 invariant that Phase 5
  must preserve, not break.
- **`SymbolInfo` lacks every field order validation needs**: no `volume_min`,
  `volume_max`, `volume_step`, `trade_stops_level`, `trade_freeze_level`,
  `trade_mode`, `filling_mode`. `symbol_specs` (schema.sql) lacks them too.
  Validating a lot size or an SL distance today is impossible with stored data.
- **Open trades already exist in `trades`.** `reconstruct.py:290` sets
  `status='open'` when a position has IN deals and no OUT. So "open trade" is a
  known concept — but it is only populated from *history*, and a live open
  position produces **no deals until it closes**, so an open position that has
  never been synced is invisible. Live state must come from `positions_get()`.
- **Auto-ingest is genuinely absent.** `sync`/`rebuild`/`candles` are all manual
  CLI commands (`cli.py:125/260/310`). `journal poll` (`cli.py:383`) runs
  continuously but writes **only** `sl_tp_snapshots` — it never triggers
  ingest. That is exactly the gap in ask (2).
- **The documented fresh-account order is `sync → rebuild → candles → rebuild`**
  (rebuild twice — HANDOFF.md, M5 section). Any automation must reproduce it.

### 0.3 The house rules this plan is bound by

Re-read before each phase. Named here so no phase has to guess:

- Rule 1 / 12: MT5 import **and values** only in `adapter/`. Trade actions and
  retcodes become our own `IntEnum`s in `adapter/base.py`, asserted against the
  bridge in `live.py._assert_enums_match` (the existing mechanism — extend it).
- Rule 2: `deals_raw`/`orders_raw` append-only; `trades` fully derived.
- Rule 3: epoch ms, integer, UTC. Poller wall-clock (`now_ms`) is true UTC;
  `open_time_msc` is broker server time (Trap 7). **Never compare them.**
- Rule 4: `NULL` = unknown, `0` = none set. Applies directly to the SL/TP a
  command carries: "clear the SL" is `0.0`, "don't touch the SL" is `NULL`.
- Rule 5: money/prices are `REAL`; compare with tolerance.
- Rule 7: tests before implementation for `domain/` and `analytics/`.
- Rule 8: **do not add dependencies without asking.** This plan adds none.
- **Rule 9 — read this one carefully.** "This tool describes patterns in past
  data. It never generates trade signals or recommendations." Executing an order
  the human explicitly typed and confirmed is **not** a signal or a
  recommendation, so this plan does not violate rule 9. But it moves the project
  from read-only to a surface that can lose real money, which is a bigger change
  than any milestone so far. **Phase 2 must not start until the human says so
  explicitly, in writing, in the session.** The plan does not treat approval of
  this document as approval to trade.
- Rule 10: never commit `data/`, `cache/`, or a real login.

### 0.4 Architecture decision — one process owns the bridge

**Recommended: the web NEVER calls the bridge. It writes an intent row; a
separate long-running process executes it.**

```
browser ──POST──> web (no adapter import)
                    └─ INSERT INTO trade_commands (status='pending')
                                         ▲
                                         │ claim + execute + write result
                    journal live ────────┘   (the ONLY bridge connection)
                       ├─ poll_once()            → sl_tp_snapshots  (M4, reused)
                       ├─ open-position snapshot → open_positions
                       └─ close detected         → sync → rebuild → candles → rebuild
```

Why, concretely:

1. **The bridge is one rpyc connection to one terminal.** `order_send` holds an
   `__rpc_lock` (line 767). Two processes sending orders concurrently — a web
   worker and a poller — is a hazard with no upside.
2. **It keeps the M7 invariant intact.** Rules 1 and 12 stay literally true in
   `web/`; no adapter import appears there, ever.
3. **The audit log is free.** `trade_commands` is append-only and is the
   answer to "what did I send, when, and what did the broker say" — the same
   archival argument that justifies `deals_raw` (Trap 16).
4. **A crashed/restarted web process can never leave a half-sent order.** The
   intent either got committed or it did not.
5. It is the natural home for ask (2) as well — the process that already sees
   positions disappear is the one that should trigger the ingest.

Cost: latency of up to one claim interval (recommend **1 s** while any command
is pending, backing off to the 5 s poll interval when idle). For a manual
"close this position" click that is acceptable; the UI shows the command's
lifecycle (`pending → sent → done/failed`) rather than pretending it is instant.

**Rejected alternative:** letting `web/app.py` hold a `LiveMT5Client`. Simpler by
one table, but breaks rule 1 in the one package written specifically to prove it
could be obeyed, and puts a blocking RPC inside a request handler.

---

## Phase 1 — Migrations (blocker for everything else)

Nothing else in this plan can reach the live DB without this.

**Implement**

1. `src/journal/store/migrations/002_live_trading.sql` and a runner in
   `store/db.py`: read the current `schema_version.version`, apply every
   numbered file above it in order inside one transaction, stamp the new
   version. Bump `SCHEMA_VERSION = 2`.
2. Keep `schema.sql` authoritative for fresh DBs — add the same tables there
   too, so a fresh DB and a migrated DB are identical. **Do not edit existing
   table definitions in `schema.sql` in place** (CLAUDE.md, "Read before you
   edit"); only append new ones.
3. New tables (full DDL is the implementer's, but these columns are required):
   - `open_positions` — the live snapshot the web reads. Replaced wholesale each
     cycle (it is a *current state* mirror, not history; the history is
     `sl_tp_snapshots`, which stays append-only). Columns: `account_login`,
     `position_id`, `symbol`, `symbol_base`, `direction`, `volume`,
     `open_price`, `price_current`, `sl`, `tp`, `profit`, `swap`, `magic`,
     `open_time_msc`, `observed_msc`. PK `(account_login, position_id)`.
   - `trade_commands` — append-only intent + outcome. Columns: `id` PK,
     `account_login`, `position_id`, `kind` CHECK in
     `('modify_sltp','close','close_partial','add_volume')`, `sl`, `tp`,
     `volume`, `requested_msc`, `status` CHECK in
     `('pending','claimed','sent','done','failed','rejected')`,
     `claimed_msc`, `completed_msc`, `retcode`, `result_deal`, `result_order`,
     `result_volume`, `result_price`, `broker_comment`, `error`, `raw_json`.
     **Never UPDATE `kind`/`sl`/`tp`/`volume` after insert** — only the status
     and result columns move.
   - `symbol_specs` gains `volume_min`, `volume_max`, `volume_step`,
     `stops_level`, `freeze_level`, `trade_mode`, `filling_mode` (all nullable —
     rule 4, an un-refetched spec is unknown, not zero).
4. `journal migrate` CLI command (idempotent; prints from→to version), and
   `connect()` applies pending migrations automatically so no command can run
   against a stale schema.

**Verify**

- New test file `tests/test_migrations.py`: a v1 DB built from a *frozen copy* of
  the v1 schema, migrated, ends byte-equivalent in table/column set to a fresh
  v2 DB (compare `PRAGMA table_info` for every table).
- Migration is idempotent: running `journal migrate` twice is a no-op.
- `uv run pytest` fully green (202 tests today — no test may regress).
- Against a **copy** of the live `data/journal.db`, not the original.

**Anti-pattern guards**

- Do not `DROP`/recreate any existing table. Do not touch `deals_raw`,
  `orders_raw`, `trades`, `annotations`, `tags`.
- Do not add an ORM or a migration library (rule 8).

---

## Phase 2 — Adapter boundary: trade operations

**Gated on the explicit human go-ahead described in §0.3.** This is the phase
where the project stops being read-only.

**Implement**

1. `adapter/base.py` — new `IntEnum`s, values copied from §0.1 (cite the line
   numbers in a comment, as `DealType` already does):
   `TradeAction` (`DEAL=1`, `SLTP=6`), `OrderType` (`BUY=0`, `SELL=1`),
   `OrderFilling` (`FOK=0`, `IOC=1`, `RETURN=2`), `TradeRetcode` (the full
   `:222-251` set — **complete, exactly like `DealType`'s comment demands**, or
   `TradeRetcode(result.retcode)` will raise on the first unlisted code).
2. New frozen dataclasses: `TradeRequest` (our vocabulary — `action`,
   `position_id`, `symbol`, `order_type`, `volume`, `price`, `sl`, `tp`,
   `deviation`, `filling`, `magic`, `comment`) and `TradeResult` (`retcode`,
   `deal`, `order`, `volume`, `price`, `comment`, `request_id`, `raw`).
3. `MT5Client` Protocol gains exactly two methods:
   `order_check(req: TradeRequest) -> TradeResult` and
   `order_send(req: TradeRequest) -> TradeResult`.
4. `SymbolInfo` gains the seven fields from Phase 1.3; `ingest/deals.py`'s spec
   writer persists them.
5. `adapter/live.py` — build the bridge dict from `TradeRequest` (this is the
   ONLY place a `TRADE_ACTION_*` integer exists), call the bridge, map the
   result back. Extend `_assert_enums_match` to cover the new enums with the
   same "mismatch = hard failure, missing = logged warning" policy.
6. `adapter/fake.py` — a scriptable fake: a queue of canned `TradeResult`s plus
   a recorded list of every `TradeRequest` it received, so every downstream test
   asserts on **what would have been sent** without a bridge.

**Verify**

- Tests run entirely on `FakeMT5Client`. **No test may require a live bridge.**
- `grep -rn "siliconmetatrader5\|TRADE_ACTION\|TRADE_RETCODE\|ORDER_TYPE_" src/ --include=*.py | grep -v "adapter/live.py"` → **empty**.
- `isinstance(FakeMT5Client(), MT5Client)` still True (the Protocol is
  `runtime_checkable`).

**Anti-pattern guards**

- Do not invent `positions_modify()`, `position_close()`, or
  `order_send(action=..., symbol=...)`. The bridge has exactly `order_send(dict)`
  and `order_check(dict)` (§0.1).
- Do not let `TradeRequest` carry an MT5 integer for `action`/`type`/`filling`.
  Use the enums; `live.py` maps them.
- Do not add a `login()`/credential path. The container is already logged in.

---

## Phase 3 — The command layer (pure logic, no bridge)

**Implement** — new `src/journal/domain/commands.py`, TDD (rule 7), plus
`src/journal/execute.py` for the DB-facing enqueue/claim helpers.

1. `enqueue(conn, kind, position_id, *, sl, tp, volume) -> int` — validates and
   inserts a `pending` row. Validation, all against `open_positions` +
   `symbol_specs`, all pure and unit-tested:
   - position exists and is open;
   - `volume` respects `volume_min`/`volume_max`/`volume_step` — and when the
     spec is **unknown (NULL)**, the command is **rejected**, never assumed
     valid (rule 4);
   - `add_volume` is capped: hard ceiling of the position's current volume, and
     never above `volume_max`;
   - `close_partial` volume < current volume;
   - SL/TP respect `stops_level` distance from `price_current` where known, and
     are on the correct side for the direction;
   - `sl=None`/`tp=None` mean "leave unchanged"; `0.0` means "clear it". Both
     are representable and they are different (rule 4).
2. `build_request(cmd, position, spec) -> TradeRequest` — the pure mapping.
   `modify_sltp` → `TradeAction.SLTP`. `close`/`close_partial` →
   `TradeAction.DEAL` with the **opposite** `OrderType`. `add_volume` →
   `TradeAction.DEAL` with the **same** `OrderType`. On a hedging account
   (`margin_mode=2`, CLAUDE.md) a close **must** carry `position_id`, or the
   broker opens a second, opposite position instead of closing.
3. `classify(result) -> ('done'|'failed'|'rejected')` — `DONE`/`PLACED`
   → done; `DONE_PARTIAL` → done **with the actual filled volume recorded**,
   never assumed equal to the request; everything else → failed, with the
   retcode name preserved.

**Verify**

- Test written and seen **failing** before each implementation (the project's
  stated TDD discipline).
- Explicit regression tests for: unknown spec → rejected; `sl=0.0` vs `sl=None`;
  hedging close carries `position_id`; partial fill records filled volume;
  `add_volume` ceiling.
- Float comparisons use tolerance, never `==` (rule 5) — grep the new files.

**Anti-pattern guards**

- No net-position or averaging logic. This account is hedging; one order = one
  position.
- No "suggested SL", no auto-breakeven, no trailing stop, no risk-sizing
  *recommendation*. Rule 9. The human types the number; the system validates it
  and reports what the broker said.

---

## Phase 4 — `journal live`: the process that owns the bridge

**Implement** — extend `ingest/poller.py` (or a sibling `ingest/live.py`;
implementer's call, but `poll_once` must be reused, not reimplemented).

Each cycle:

1. `positions_get()` once. Feed it to the existing `poll_once` logic
   (`sl_tp_snapshots`, change-only) **and** replace `open_positions` wholesale.
2. **Close detection** = a `position_id` present in the previous cycle's
   `open_positions` and absent now. On any close:
   `sync → rebuild → candles → rebuild` (the documented order, §0.2) —
   **this is ask (2)**. Log which positions closed and what the ingest returned.
   Debounce: if several close together, coalesce into one ingest pass.
3. **Command execution**: claim `pending` rows oldest-first
   (`UPDATE ... SET status='claimed' WHERE status='pending' AND id=?`, check
   `rowcount` — that is the lock), `order_check` first, then `order_send`, then
   write the outcome. One command per cycle keeps it serial and auditable.
4. Cycle interval: 5 s idle, 1 s while any command is `pending`.
5. Crash recovery on startup: any `claimed`/`sent` row older than N seconds is
   marked `failed` with `error='interrupted — verify in MT5 before retrying'`.
   **It is never auto-retried.** An order that may have reached the broker must
   not be re-sent by a machine.
6. CLI: `journal live [--interval] [--no-trading] [--duration] [--once]`.
   **Command execution is ON only when the flag says so** — decide the default
   with the human. Recommended: `--trading` opt-in, i.e. off by default, so
   running `journal live` for ask (2) alone can never send an order.

**Verify**

- All of it testable against `FakeMT5Client` with an injectable clock, exactly
  like `poll_loop` already is (`sleep`/`monotonic` parameters).
- A test where a position disappears between two cycles asserts the ingest
  pipeline ran (spy on the callables, don't hit a bridge).
- A test that a `pending` command is claimed exactly once when two loops race.
- A test that `--no-trading` leaves `pending` rows untouched.
- Live smoke, human-run, on a **1-lot-minimum, smallest possible position**:
  one `modify_sltp` end to end, verified in MT5 itself. Paste the output.

**Anti-pattern guards**

- Do not auto-retry a `sent` command. Ever.
- Do not compare `observed_msc` (true UTC) with `open_time_msc` (server time) —
  Trap 7, and M4 already refused to do this once.
- Do not let a failed ingest kill the loop; log it and keep polling. Losing the
  poller loses SL history that cannot be recovered.

---

## Phase 5 — Web: live view + command submission

**Implement**

1. `GET /live` — open positions from `open_positions` (**not** the bridge),
   with floating P&L, current SL/TP, duration, and the age of the snapshot
   ("data 3 s ago") so a stale view is never mistaken for a live one. If
   `journal live` is not running, say so plainly.
2. `POST /live/{position_id}/sltp`, `/close`, `/close-partial`, `/add-volume` —
   each calls `execute.enqueue`, then redirects (303) to `/live`. **No bridge
   call in the request path.**
3. `GET /live/commands` (and an inline panel) showing recent commands with their
   status and retcode name — the audit log made visible.
4. Safety in the web layer:
   - Trading routes exist **only** when `journal serve --trading` is passed;
     otherwise they return 404 and the UI renders read-only.
   - Every destructive action is a POST form with an explicit confirm step
     showing the exact parsed intent ("close 0.01 lot of XAUUSDc, position
     123456") — never a bare one-click button, never a GET.
   - Bind stays `127.0.0.1` by default. Document loudly that `--host 0.0.0.0`
     with `--trading` exposes an unauthenticated order-entry endpoint on the
     LAN; consider refusing that combination outright.

**Verify**

- `grep -rn "adapter" src/journal/web/` → only comments, no imports. The M7
  invariant survives.
- `tests/test_web.py` gains: `/live` renders from a seeded DB with no bridge;
  a POST inserts exactly one `pending` row and sends nothing; trading routes
  404 when the flag is off.

**Anti-pattern guards**

- Do not import `LiveMT5Client` in `web/`.
- Do not show a floating P&L as if it were realized. Label it, and keep money
  in `USC` with the currency suffix (CLAUDE.md).
- Do not display an unknown SL as `0` (rule 4).

---

## Phase 6 — Frontend redesign

**Do this last**, so it styles the finished information architecture. Invoke the
`frontend-design` skill at the start of this phase.

Current state, for reference: `web/static/app.css` is 130 lines of hand-rolled
CSS variables; templates are 5 small Jinja files. The screenshots show the real
problem — it is not ugly so much as **low-density and low-signal**: four cards,
a flat table, no time dimension anywhere, `n/a` repeated down a whole column
with no explanation of why.

**Implement**

1. **Information architecture first, pixels second.** The redesign brief:
   - A **live strip** at the top of the dashboard (open positions, floating
     P&L, exposure) — the thing a trader actually looks at.
   - An **equity / cumulative-R curve**. The data exists (`trades` ordered by
     `close_time_msc`); the dashboard currently shows no time dimension at all.
     Inline SVG, no charting dependency (rule 8).
   - Trades list: sticky header, per-row R and duration, a compact
     win/loss bar, filter chips instead of three bare selects.
   - The `n/a` cells must **explain themselves** (a tooltip/footnote: "needs
     n≥20, have 6") rather than reading as broken output. That single change
     does more for "informative" than any amount of restyling.
2. **Design tokens** in `app.css` — a real scale for space/type/radius/color,
   light+dark, replacing the ad-hoc values. One stylesheet, no build step, no
   framework (rule 8).
3. Responsive down to a phone: this gets read on a phone mid-session.
4. Preserve, non-negotiably: money always carries `USC`; unknown reads `n/a` /
   "unknown", never `0`; §9-gated buckets stay visually distinct; times shown in
   WIB, labelled, converted only at display time (rule 3).

**Verify**

- Every page renders with the live DB and with an empty DB (no crash on zero
  trades).
- Contrast checked in both light and dark.
- No new dependency in `pyproject.toml`.
- Screenshots before/after, pasted.

**Anti-pattern guards**

- Do not add Tailwind/React/a build step. This is a local single-user tool.
- Do not add a green "profit" color that makes a `USC` figure look like dollars.
- Do not remove the honesty features to make the page look fuller. An `n/a` that
  explains itself is the feature.

---

## Phase 7 — Verification

1. `uv run pytest` — full suite green, output pasted. (Baseline: 202 tests.)
2. `uv run journal rebuild` still succeeds; trade count and `mae/mfe`
   computable count unchanged from before the branch (currently 72 / 72).
3. `uv run journal verify` — both balance identities still pass.
4. Boundary greps, all expected empty:
   - `grep -rn "siliconmetatrader5" src/ --include=*.py | grep -v adapter/live.py`
   - `grep -rn "TRADE_ACTION\|TRADE_RETCODE\|ORDER_TYPE_\|ORDER_FILLING" src/ --include=*.py | grep -v adapter/`
   - `grep -rn "^from ..adapter\|^from journal.adapter\|import adapter" src/journal/web/`
5. Migration replay: fresh DB v2 vs migrated v1→v2 have identical schemas.
6. Live smoke, human-run, in this order, output pasted for each:
   `journal migrate` → `journal live --once` (no trading) → open a position
   manually in MT5, confirm it appears in `/live` → close it manually, confirm
   the journal ingested it with **no manual command** (ask 2 proven) → one
   `modify_sltp` from the UI on the smallest possible position, confirmed in
   MT5 itself (ask 1 proven).
7. Update `docs/HANDOFF.md` current-state section and `CLAUDE.md`'s milestone
   line. Record what was *measured*, not what was intended.

---

## Open questions for the human — answer before Phase 2

1. **Go/no-go on execution.** Do you want the journal to be able to send orders
   at all? This plan is built so Phases 1, 4 (ingest half), 5 (read half) and 6
   deliver asks (2) and (3) **with no execution capability whatsoever** if you
   say no.
2. **Default for trading**: opt-in (`--trading`) or on by default? Recommended
   opt-in.
3. **Hard caps**: max lot per command, max commands per hour, and whether
   `add_volume` should exist at all (it is the one that can grow risk without
   bound).
4. **Demo first?** `accounts.is_demo` is stored. Recommended: refuse to execute
   on a live account until one full session has run clean on demo.
