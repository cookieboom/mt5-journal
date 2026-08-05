# Handoff — read this first

> **Update 2026-07-24 (Phase 5 cutover):** the web UI is now the React SPA
> served at `/`; the Jinja2 templates, `/static/app.css`, the form-POST write
> routes, and the `jinja2`/`python-multipart` deps have been retired. `journal
> serve` and the loopback/WAL coexistence notes below are unchanged.

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

**Last updated:** 2026-08-05

**2026-08-05 — nothing is pending a human run any more.** The human confirmed,
in person and against the live bridge, every item this file and the project
memory had been carrying as PENDING HUMAN:

- The on-close ingest freeze is gone (gap-aware `sync_candles` + capped fetches
  + post-ingest beat, and two-phase `deals.sync` with a windowed history pull —
  measured 243 s → 49 s, 124 bridge round trips → 0). Watched on a real close.
- SL/TP drag on `/chart` with the bridge running: the whole click-through,
  including a live order reaching the broker.
- Risk-based auto lot sizing opening a real position with the SL attached from
  the first tick — which also settles the older "an accepted order has never
  landed" note below (AutoTrading is on).
- The live-bar rollover fixes: the stale-`now_msc` one in `serve_watches`
  (backend) and, found the same day, a second FRONTEND cause of the *identical*
  symptom — `useChartData.loadUpTo` fetched from a mid-bucket cursor, so the bar
  that was forming when `/chart` opened could never be returned by
  `time_msc BETWEEN from AND to` and was lost for good (one bar, once per page
  open). Fixed by flooring the forward `from` to the bucket start (`219d95e`).
  **If "the live bar vanishes at rollover" is ever reported again, check BOTH
  layers** — the symptom does not tell you which one it is.

Remember `frontend/dist` is gitignored and is what `journal serve` ships: after
any frontend merge, run `npm run build` in the main checkout or the browser
keeps the old bundle.

**Previous entry — 2026-07-23:**

**M9 in one line (MERGED to main; branch `claude/trading-system-plan-2959b7` since deleted):** the
journal became able to *act*, not just describe. Six phases: (1) a real
migration runner in `store/db.py` + `migrations/002_live_trading.sql` (bumps
`SCHEMA_VERSION=2`, applied automatically by `connect()`); (2) trade ops at the
adapter boundary (`order_check`/`order_send` on the Protocol, new `TradeAction`/
`OrderType`/`OrderFilling`/`TradeRetcode` enums, a scriptable `FakeMT5Client`
write side); (3) a pure command layer (`domain/commands.py` validate/
build_request/classify + `execute.py` enqueue/claim/record) with the human's
1.00-lot hard cap unit-tested; (4) `journal live` — the single process that owns
the bridge: mirrors `open_positions`, **auto-ingests on close** (sync→rebuild→
candles→rebuild, ask 2), and executes queued commands, never auto-retrying a
`sent` order; (5) the web live view + a mandatory two-step confirm before any
order (`/live`, `/live/commands`), with `serve` refusing any non-loopback
`--host`; (6) a frontend redesign — live strip, an inline-SVG equity/cumulative-R
tape, design tokens with light+dark, self-explaining `n/a` cells.

**M9 decisions (human, 2026-07-23):** execution is GO; trading is **ON BY
DEFAULT** (`--no-trading` opts out of command execution; the UI confirm step and
the loopback bind-check are the primary guards, not a flag); **1.00-lot hard cap**
per command, enforced in `domain/commands.py`; a real account is acceptable (no
demo gate). Rule 9 still binds: the human types every number; the system only
validates, sends, and reports the broker's verbatim answer — no suggested SL, no
auto-breakeven, no sizing.

**M9 verification — MEASURED so far:** `uv run pytest` **375 green** (was 202 at
M8's baseline; +173 across the six phases). Boundary greps clean: no
`import MetaTrader5` and no `TRADE_*`/`ORDER_*` value outside `adapter/`; `web/`
imports no adapter. Migration replay test passes (fresh-v2 == migrated-v1→v2).
On the live DB (migrated in place, backup kept): `migrate`→v2, `rebuild`→72/72
mae-mfe, `verify`→**both identities PASS**, residual +0.00, the 14.50 USC archived
reconciliation intact.

**Live smoke — MEASURED 2026-07-23 (real account, real bridge):**
- **Auto-ingest on close (ask 2) — PROVEN.** `journal live --no-trading` running,
  a real XAUUSDc position (#1582918124, 0.01 lot) opened → heartbeat went
  `0 open` → `1 open · 1 SL/TP snapshot(s)`; closed → `closed [1582918124] —
  menjalankan ingest… -> ingested`. `trades` grew 72→82 and `verify` still PASSED
  both identities afterward — the close-triggered pipeline ran and left the DB
  consistent, with no manual command.
- **Web live view — PROVEN.** `/live` rendered the open position live (floating
  P&L −0.90 USC labelled floating, SL/TP shown as `0`=none-set, "data 3s ago").
- **Two real bugs found and fixed by running it live** (regression-tested):
  `database is locked` (connect() now WAL + busy_timeout so live+serve coexist),
  and a silent heartbeat that read as a freeze (per-cycle heartbeat + an
  `on_closing` notice before the blocking ingest). **Footgun learned:** run
  `live` AND `serve` with the SAME absolute `--db`; `serve` without `--db` from
  the worktree makes a stray empty `data/journal.db` and `/live` looks empty.
- **Order SEND path (ask 1) — REACHED THE BROKER, verdict recorded faithfully.**
  A `modify_sltp` (SL 4090, TP left unchanged) typed in `/live` on a real 0.01-lot
  XAUUSDc position went UI → `pending` → claimed → `order_check` → `order_send` →
  **the broker answered**. The loop recorded it `failed`, retcode **10027
  (`TRADE_RETCODE_CLIENT_DISABLES_AT` — "AutoTrading disabled by client")**, with
  the broker comment, and did NOT retry. So the whole plumbing AND the failure
  path are proven; the rejection is a TERMINAL SETTING (the container's MT5 has
  Algo/AutoTrading turned OFF), not a code fault. This also surfaced and fixed a
  real honesty bug: the audit log rendered a left-unchanged `TP` (NULL) as
  "unknown"; it now reads "(tetap)" via a shared `format.level_word` — a modify's
  NULL level is a deliberate "leave it", not ignorance (rule 4).
- ~~**STILL NOT measured:** a `done` order that actually LANDS~~ — **MEASURED
  2026-08-05.** The risk-based auto-lot-sizing live pass (its section below)
  opened a real position with the SL attached from the first tick, so AutoTrading
  is on and an accepted order landing is proven. The browser visual/contrast pass
  of the redesign is confirmed too.

M9 is now *live-verified for ingest, the read/observe surface, and the full
order-send plumbing up to the broker's verdict; a successful (accepted) order has
not landed yet only because the terminal's AutoTrading is off.*

**CORRECTION (2026-07-25) — kill a stale-doc misunderstanding.** Earlier phrasings
(this file's own status table, and handoffs that quoted it) said the "M9 live
smoke is pending a human run," which later work read as "the live/bridge path is
unproven." That is wrong and has been for a long time. The live round trip —
`journal live` owning the bridge, mirroring `open_positions`, serving `/api/live`,
and the browser UI reading it — WORKS and was measured 2026-07-23 (above). The web
layer never touches the bridge directly (rule 1 / M9 boundary); it goes through the
`journal live` process + the command/candle queues, and that whole path is proven.
Both items that section once listed as pending — (1) an *accepted* order actually
changing the SL in MT5, (2) a browser visual/contrast pass of the SPA — were
**CONFIRMED by the human 2026-08-05**. M9 has no pending human verification left.
Chart Phase B's live-position overlay consumes the same proven `/api/live` data
path — its "positive path" is verifiable exactly the way `/live` already is; only
the chart-specific line rendering is new frontend, not a bridge concern.

**Done:** M0 (adapter + store + doctor) · M0.1 (Candle→ms, enums probed from the
bridge) · M0.2 (fixtures re-recorded with `comment` preserved, `a15cc5e`) ·
M1 + M1.1 + M1.2 (ingest, archive detector, bridge-free `verify`, reconcile,
`equity` modelled — `1d086c2` / `10d9141`) · M2 + M2.1 (`reconstruct.py`:
deals → trades, `journal rebuild`, `journal verify` §6 identity 2 — 55 tests
green, `48a4cc7`) · M3 (candle store + mplfinance renderer, `journal chart
<position_id>` — 83 tests green, `797849b`) · M4 (SL/TP poller, `journal poll`
— 110 tests green, `0f1b088`) · M5 (MAE/MFE + `journal report` — 138 tests
green, `11cac94`) · M5.1 (session + EA/discretionary breakdowns in
`journal report` — 150 tests green, `3a5d198`) · M6 (annotations +
manual/auto tags, `journal annotate`/`tag` — 179 tests green, `24ce64b`) ·
M6.1 (weekly Markdown report, `journal weekly` — 188 tests green,
`a989eac`) · **M7** (web dashboard, `journal serve` — 202 tests green).

**M7 in one line:** a read-mostly FastAPI/Jinja2 dashboard on `localhost`
(`journal serve`, default `127.0.0.1:8000`) sitting entirely on top of the
existing pure functions — Dashboard (`build_report`), Trades list + detail with
on-demand chart PNG (`render_trade`), and Weekly (`build_weekly`), plus the only
web writes: annotation + manual-tag forms (`set_annotation`/`add_tag`/
`remove_tag`). New package `src/journal/web/` (`app.py` factory, `views.py`
context builders, `format.py` Jinja filters, `templates/`, `static/app.css`).
It never imports the MT5 adapter (rules 1 & 12) — `sync`/`candles`/`poll`/
`rebuild` stay CLI-only. Same display discipline as the CLI: money always
carries `USC`, unknown reads "n/a"/"unknown" (never 0), n<20 buckets greyed
(§9), URLs key on `position_id`. New deps (rule 8, approved): `fastapi`,
`jinja2`, `uvicorn`, `python-multipart`. Verified live: dashboard figures match
`journal report`; annotation/tag written from the UI survive `journal rebuild`.

**M5 in one line:** `trades.mae`/`mfe`/`mae_r`/`mfe_r` (NULL since M2) are now
filled by `rebuild()`, and `journal report` gives a first honest read of the
account — money stats at full coverage, R-stats correctly gated as
"insufficient" at today's `n=6`. Session bucketing and EA/discretionary
breakdowns were scoped **out** to M5.1 (the roadmap's one-line M5 description
bundled 4 features, ~4x M3/M4's size — split mirrors how M1→M1.1/M1.2 and
M2→M2.1 actually happened).

**The plan went through a validation pass before any code was written, and it
caught three real bugs, plus a fourth surfaced while fixing the third — all
four are now regression-tested, not just fixed:**

- **No money conversion needed at all.** `mae_r = mae / |open_price -
  real_sl|` — `risk_amount`'s `tick_size`/`tick_value`/`volume` cancel
  algebraically against the same terms in `mae_money`. First draft added a
  `distance_to_money()` helper to `domain/risk.py`; that file stays completely
  untouched instead — a stricter version of M4's own "don't modify
  `domain/risk.py`" precedent (M4 solved a *different* problem there; here
  there was no risk.py-shaped problem to solve).
- **A bar-open-time filter would have silently dropped most short trades.**
  `candles.time_msc` is a bar's *open* time; requiring it to fall inside
  `[open,close]` returns nothing for a trade that doesn't contain a bar-open
  boundary — true of most of the 11 sub-M1 trades (min 1s). Fixed with
  covering-bar semantics, mirroring `render/chart.py::_nearest_bar_index`
  (the same problem, already solved once for chart markers): the bar
  *containing* open through the bar *containing* close.
- **Scanning every timeframe for a symbol is unsafe on this hedging account.**
  Two overlapping trades of different durations can pick different TFs
  (`choose_timeframe`); a coarser trade's wider bar would leak into a shorter
  trade's excursion if the TF column were ignored. Excursion is scoped to
  **the trade's own TF**, not the symbol alone.
- **The fix for that surfaced a fourth issue:** a bulk in-memory preload
  (mirroring M4's `sl_tp_snapshots` pattern) risks a short trade silently
  matching a *different, disjoint* trade's stale cluster on the same
  symbol+TF, since `candles` pools every trade's window (schema.sql: "Dedupes
  across trades on the same symbol/day"). Fixed with a **scoped SQL query per
  trade** (symbol + that trade's own TF + its own `window_for` window) instead
  of a bulk scan — which also meant excursion couldn't thread through
  `reconstruct()` the way M4's `snapshots` did (a trade's open/close only
  exist *after* `reconstruct()`'s loop runs). It's a post-processing step in
  `rebuild()` instead: `Trade` is a mutable dataclass, so `_fill_excursions`
  sets `mae`/`mfe`/`mae_r`/`mfe_r` in place before the INSERT loop.
  `reconstruct()`'s signature and pure logic are untouched by M5.
- **The SL-exactly-at-entry ZeroDivisionError guard (Trap 6/M2.1) recurred a
  third time**, now in `mae_r`/`mfe_r`: `real_sl == open_price` gives a
  *known* zero `risk_distance`, not an unknown one — gate on it being truthy.
  Three occurrences of one bug shape in this codebase now (`r_multiple`,
  `mae_r`/`mfe_r`, and `profit_factor` in the report below) — worth watching
  for as a pattern, not three unrelated bugs.
- **A real workflow wrinkle, documented rather than hidden:** MAE/MFE needs
  `candles`, which `journal candles` only fetches for trades already in
  `trades` — so the order is `sync → rebuild → candles → rebuild` (rebuild
  **twice**) on a fresh account. Safe (`rebuild` is idempotent) and
  unavoidable even in steady state.
- **`journal report`'s win/loss/breakeven classification uses tolerance**
  (`abs(net_profit) <= 1e-9`), never `==`/`>`/`<` on a raw float (rule 5) —
  every downstream count depends on getting this comparison right.
- **`n_with_mae` is a plain diagnostic, never gated** — "how much of the
  account has candle coverage yet" isn't itself an average, so it's shown
  regardless of `n`, unlike `avg_r`/`avg_mae_r`/`avg_mfe_r`.

**Live smoke:** `journal candles → rebuild → report` against the live
account. `candles`: 2494 new bars, 72/72 trades windowed. `rebuild`: `mae/mfe`
went from 0 to **72 computable**. `report`:
```
win rate: 34.7%   avg win: 9.92 USC   avg loss: -3.75 USC
profit factor: 1.41   expectancy: +1.00 USC
R-multiple: n/a (n=6, need ≥20)      -- correctly withheld, not a bug
MAE/MFE:    candle coverage 72/72; n/a (n=6, need ≥20) -- same reason
```
Net profitable despite a sub-40% win rate (cuts losses short: avg loss
magnitude less than half avg win) — and the report correctly refuses to
average 6 R-multiple data points as if they were reliable.

**Not blocked.**

**M5.1 in one line:** `journal report` gained two behaviour breakdowns —
`by session` (five fixed UTC trading-session buckets) and `by source` (EA vs
discretionary) — each reusing M5's §9 `n≥20` gate per bucket, so a thin bucket
reads `n/a` (with its count beside it) instead of a number pretending to be
reliable. No schema change, no migration, `domain/reconstruct.py` untouched —
`open_time_msc` and `magic` were already on `trades`. New pure module
`analytics/sessions.py` (`session_of` + `SESSION_ORDER`); `build_report` gained
`BucketStat` + `by_session`/`by_source`. 150 tests green (was 138). Followed the
4-phase plan in `docs/plans/M5.1-sessions-ea-breakdown.md` verbatim, TDD each
phase (test written and seen failing before the code).

M5.1 decisions worth knowing:

- **Session model = fixed UTC trading-session windows**, half-open `[start,end)`,
  tiling the whole day: Asian 00–07 · London 07–12 · LDN/NY 12–16 · New York
  16–21 · Late 21–24. Server clock IS UTC (`server_utc_offset_s=0`, docs §7),
  so the hour is read with no offset — via the repo's canonical
  `datetime.fromtimestamp(ms/1000, tz=timezone.utc)`, never the naive
  `utcfromtimestamp` (rule 3).
- **Counts, not denominators.** The per-symbol hours caveat (BTC 24/7 but
  XAU/EUR are not — docs §7) is handled by reporting *raw bucket counts* and
  gating averages; the report never divides by a "hours available" figure we
  have not built. A low bucket count may just mean the symbol was shut.
- **EA split classifies on `magic` alone** (docs §7: `magic!=0` ⟺ EXPERT ⟺ the
  same 6 trades). A truthy magic is EA; `0` **and** `NULL` both fall to
  discretionary — rule 4: an unknown magic is not evidence of EA.
- **Live read (72 trades):** sessions partition exactly (23+35+5+3+6=72), source
  splits 6 EA / 66 discretionary — matching §7's measured EA count. All six EA
  trades opened in the London session (EA and London share `n_with_r=6`), a
  consistency cross-check the data surfaced on its own. Every session but Asian
  (23) and London (35) sits under the gate and reads `n/a`, by design.
- **`journal rebuild` still succeeds** post-change (breakdowns are read-only);
  the DoD run showed 72 trades / mae-mfe 72 computable, unchanged.

**M6 in one line:** the human layer landed. `journal annotate <position_id>`
captures setup/confidence/emotion/plan/notes; `journal tag add/rm/ls` manages
manual tags; `rebuild` now also writes auto tags; and `journal weekly` renders
one ISO week to a Markdown file in `cache/`. The storage (`annotations`, `tags`,
`v_trades_annotated`) already existed in `schema.sql` and the live DB, so M6 was
**wiring only — no schema change, no migration**. 188 tests green (was 150).
Shipped as two commits at the natural split, mirroring M5→M5.1: **M6**
(annotations + tags, `24ce64b`) and **M6.1** (weekly report, `a989eac`).
Followed `docs/plans/M6-annotations-weekly-report.md` with TDD each phase.

M6 decisions worth knowing:

- **The human layer is keyed on `position_id`, never `trades.id`** (which
  renumbers every rebuild — schema comment). Annotations and manual tags live in
  the "never rebuilt" section and **survive `rebuild`** — verified live: a
  manual tag + annotation set before a rebuild both persisted while the 34 auto
  tags regenerated around them.
- **The auto-tag pass (`_fill_auto_tags` in `rebuild`, mirroring
  `_fill_excursions`) deletes ONLY `source='auto'`** before re-inserting — that
  one WHERE clause is what keeps manual tags safe. Idempotent across rebuilds.
- **Auto tags are structural facts, not opinions:** `sub-1min`,
  `held-overnight`, `weekend`. The value-laden `big-win`/`big-loss` are gated
  off below n=20 (§9) and computed from account deciles by the caller, so no
  outlier label is applied against a sample too small to define one.
- **Weekly attributes a trade to the week it CLOSED in** (realized P&L),
  Mon–Sun UTC, half-open. Weekly rates/averages are §9-gated (a week rarely
  clears n≥20, so they usually read `n/a`); the raw counts, the realized net
  total (a sum), and the annotated/manually-tagged trades are always shown —
  that is what a weekly review is for. Reuses M5.1's `bucket_stat` (promoted
  from private) so weekly and account reports share one definition of "a win".
- **Weekly output is a reproducible `cache/` artifact** (rule 6) — verified
  byte-identical on regeneration; `cache/` is gitignored.

**Next: roadmap complete through M7.** The original ask (M0–M3) plus the
poller, analytics, the human layer, and the M7 web dashboard (`journal serve`,
`cec87d9`) are all shipped. No milestone is currently
scheduled; natural follow-ups if the tool earns daily use: auto-tag rule
expansion (the `source='auto'` pipeline is built), a multi-week/trend view
(the weekly builder generalises), and richer annotation querying/filtering.

---

**Evidence from earlier milestones, kept for reference:**

**M4 in one line:** `journal poll` snapshots live open positions'
`positions_get()` SL/TP into `sl_tp_snapshots` on change; `journal rebuild`
consults that data whenever `orders_raw` gives nothing, closing (going
forward only) the gap M2 measured — only 6/68 trades had a recoverable
`sl_initial` from the order alone.

M4 decisions still worth knowing:

- Forward-only by the nature of the MT5 API — `positions_get()` only returns
  open positions, so the 62 historical discretionary trades stay
  `sl_initial IS NULL` forever; M4 only helps trades open *while polling*.
- A confirmed-`0.0` (poller-observed "no SL ever") is a real, auditable fact
  (rule 4) but must never reach `risk_amount()` as a price — `_real_sl_price()`
  is the guard M5 reused three paragraphs above.
- The "all-zero → confirmed" coverage caveat is accepted, not solved: a
  proximity safeguard would itself be a latent Trap-7 bug (`observed_msc` is
  poller wall-clock UTC; `open_time_msc` is broker server time).
  Blast radius is contained regardless — a wrong `0.0` still yields
  `risk=None`, never a poisoned statistic.
- Change-only logging, not per-tick (11h25m at 5s intervals would be ~8200
  rows/trade otherwise).
- Two bugs caught before commit, both now regression-tested: a same-millisecond
  PK collision that silently dropped a real SL observation (fixed by forcing
  strictly-increasing `observed_msc`), and `journal poll`'s activity being
  invisible in a terminal with no logging handler configured (fixed with an
  `on_cycle` CLI callback).

**M3 in one line:** trades became visible. `journal candles` fetches each
closed trade's render window into the central `candles` table; `journal chart
<position_id>` reads it back and writes a PNG to `cache/`.

M3 decisions still worth knowing:

- TF picked by a duration ladder (finest TF where the trade spans ≤60 bars,
  floor M1, padded 15 bars each side) — M15 was rejected as a default because
  it draws the *median* trade as a single candle (doc §7).
- 11/68 trades are sub-M1 (min 1s) — rendered honestly, both markers on the
  one bar, title says so, never a fabricated intrabar line.
- Cache keys on `position_id`, never `trades.id` (which renumbers every
  rebuild) — CLI takes `position_id` only, no `--trade-id` alias.
- SL/TP hlines and R display both gate on the VALUE (rule 4), not on
  `is not None` — the exact guard shape M4 later needed again for
  `risk_amount()`, above.
- Axis reads `sync_state.server_utc_offset_s`, never hardcodes 0, and displays
  WIB consciously (chart = primary display surface, rule 3).
- `live.py.copy_rates_range` calls `symbol_select` first (item 0) — insurance
  against Trap 12, not a proven bug.
- Self-inflicted bug: `record_fixtures.py`'s rates addition initially sourced
  trade selection from the live pull, drifting the frozen M1/M2 fixture
  snapshot and breaking 8 tests. Fixed by anchoring rates selection to the
  already-committed fixtures on disk.

**M2 closed the milestone everything since M0 was built to make verifiable.**
Reconstruction is a *partition* of the deals, and the §6 identity-2 invariant
now proves it lost or double-counted nothing:

```
offline (140-deal fixture):  sum(trades.net) 63.72 + non-trade 5998.00 = 6061.72
live (traded since):         sum(trades.net) 71.72 + non-trade 5998.00 = 6069.72
```

Both partition the balance exactly. Offline drive: rebuild → 68 trades →
reconcile 14.50 → verify PASS both identities → rebuild idempotent. The live
identity-2 check passed too (71.72 + 5998.00 = 6069.72) — the invariant held on
data that did not exist when the code was written.

Two facts M2 *measured* (numbers in `docs/mt5-deal-model.md` §7):
- `sl_initial` is recoverable from `orders_raw` for only **6 of 68** trades, and
  those six are exactly the EA set: `{sl!=0} == {magic!=0} == {reason==EXPERT}`.
  Discretionary R-coverage is **0 of 62**. So one side of the EA/discretionary
  split M5 requires is empty until the M4 poller records SLs going forward —
  another way M4 is load-bearing, not a nicety.
- 62 of 68 trades therefore carry `sl_initial IS NULL` / `r_multiple IS NULL`,
  correctly excluded from R stats (never coerced to 0 — Trap 6).

**The M1.2 live smoke still stands as the strongest ingest evidence:**

```
sum(deal cash):  6061.72 → 6069.72   (+8.00, traded since the fixtures)
balance:         6047.22 → 6055.22   (+8.00)
residual:          14.50 →   14.50   (unmoved)
```

The broker returned 148 deals, not the 140 in the fixtures. Both sides of the
identity moved by exactly the same amount and the gap did not budge — the
prediction held against real money. `archived: none` (the Trap 16 tripwire is
armed and quiet). Offset measured 0 that sync, not inherited.

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
| A second `schema.sql` at repo root, frozen since M0.1 (`e653905`), diverged from `src/journal/store/schema.sql` — missing `accounts.balance`/`equity`, and a `reconciliations` table pre-dating the M2 review fix (3 statuses instead of 2, different column order) | **Nobody's edit — an old tracked file nobody deleted.** `db.py` only ever reads `src/journal/store/schema.sql`; the root copy was dead but readable, and reading it first gives you wrong facts about the schema with no error to warn you. | A fresh reviewer session diffing both files byte-for-byte before trusting either |
| `probe_rates.py` printing "VERDICT: no dependency. live.py is correct as written" | **The reviewer's own probe.** It tested `symbol_select` on `BTCUSDc` — a traded symbol already in the container's persistent Market Watch — so both arms of the experiment were the same arm. The script asserted a conclusion its design could not reach, in the confident voice reserved for measurements. A probe that overclaims is worse than no probe: it closes a question that is still open. | Re-reading the probe's own method after seeing the result it wanted |
| `record_fixtures.py`'s M3 rates-recording addition sourced trade selection from the *live* pull the script was already doing, to pick which trade's candle window to fetch | **Claude Code's M3 implementation, first pass.** The script has always refreshed *every* fixture on each run (its original, correct job); adding rates on top of that fresh pull meant a routine re-run silently drifted `deals.json`/`orders.json`/`account.json`/`symbols.json` away from the frozen 2026-07-16 snapshot 8 M1/M2 tests hardcode (140 deals, 68 trades, balance 6047.22, …) — the account had genuinely traded more since. | `pytest` — 8 tests went red immediately after a live re-run, before any commit |
| M4's `poll_once` silently dropped a real SL observation: two DIFFERENT states for the same position landing in the same millisecond collided on the `sl_tp_snapshots` primary key, and `INSERT OR IGNORE` kept only the first | **Claude Code's M4 implementation, first pass.** The bug wouldn't fire at a real 5s poll interval, but did fire immediately under a fast test loop — exactly the gap between "works in the demo" and "works under load" this project's testing culture exists to close. | An ad-hoc verification script run before the formal test suite existed, asserting on the actual row count in `sl_tp_snapshots` rather than trusting the reported `snapshots_written` count |
| M4's `journal poll` (no `--once`) reported cycle activity only via `logging.info`, invisible in a terminal with no handler configured | **Claude Code's M4 implementation, first pass.** A long-running foreground command a human is meant to watch would have looked hung the entire time even while working correctly — the CLI's only feedback was a single summary line printed after Ctrl+C. | Self-review of the diff before commit, not a test — logging visibility isn't something `pytest` checks by default; worth remembering next time a command runs in the foreground indefinitely |
| M5's first MAE/MFE draft added a `distance_to_money()` helper and refactored `domain/risk.py` to use it | **Claude Code's M5 plan, first draft.** Unnecessary: `risk_amount`'s `tick_size`/`tick_value`/`volume` cancel algebraically in `mae_money/risk_amount`, leaving `mae_r = mae / abs(open_price - real_sl)` — no money conversion, no risk.py change, ever needed. | A design-review pass (Plan agent) done deliberately *before* writing code, working the algebra through by hand |
| M5's first MAE/MFE draft filtered candles by "bar open time falls inside `[open,close]`" | **Claude Code's M5 plan, first draft.** `candles.time_msc` is a bar's OPEN time; the filter would have returned `(None,None)` for most of the 11 sub-M1 trades (min 1s), since a fast trade rarely contains a bar-open boundary at all — a coverage gap silently misreported as "no data". | The same design-review pass, cross-checked against the measured duration profile (docs §7) instead of assuming candles align to trade windows |
| M5's first MAE/MFE draft scanned every timeframe stored for a symbol, reasoning "OHLC bars preserve true extremes at any granularity" | **Claude Code's M5 plan, first draft.** True for one timeframe alone, but this account is hedging (CLAUDE.md line 26): two overlapping trades of different durations can sit at different TFs, and a coarser trade's much wider bar would leak into a shorter trade's excursion if the TF column were ignored. | The same design-review pass, reasoning through what "hedging + per-trade TF choice" implies for a symbol-wide scan |
| M5's *corrected* design still risked a bulk in-memory candle preload (mirroring M4's `sl_tp_snapshots` pattern) picking up a different, disjoint trade's stale cluster on the same symbol+TF | **Claude Code's M5 implementation, working through the TF fix.** The central `candles` table pools every trade's window (schema.sql: "Dedupes across trades on the same symbol/day") — a "nearest preceding row anywhere" scan isn't scoped to one trade the way a bounded SQL query is. | Reasoning through the bulk-preload approach's failure mode before implementing it, not after a test caught it — the regression test (`test_excursion_scoped_per_trade_not_contaminated_across_timeframes`) was written to prove the FIX, not to find the bug |

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
| M2 | `reconstruct.py`: deals → trades, `rebuild`, §6 identity 2 | done (`48a4cc7`) |
| M2.1 | Review fixes: zero-risk R guard, NULL time_msc reject, guard dedup | done (`48a4cc7`) |
| M3 | Candle store + mplfinance renderer (`journal chart <position_id>`) | done (`797849b`) |
| M4 | SL/TP poller — makes `sl_initial` knowable, and outruns the archiver | done (`0f1b088`) |
| M5 | MAE/MFE + core `journal report` (money stats + gated R-stats) | done (`11cac94`) |
| M5.1 | Session bucketing + EA/discretionary behaviour breakdowns | done (`3a5d198`) |
| M6 | Annotations + manual/auto tags (`journal annotate`/`tag`) | done (`24ce64b`) |
| M6.1 | Weekly Markdown report (`journal weekly`) | done (`a989eac`) |
| M7 | Web dashboard on localhost (`journal serve`) — read-mostly + annotation/tag writes | done |
| M8 | Per-symbol breakdown (`by_symbol`) + dedicated `/report` web page | done |
| M9 | Live positions + trade interaction + auto-ingest on close + UI redesign (`journal live`, `/live`) | **done — merged to main.** Live-verified 2026-07-23 (real account/bridge): auto-ingest-on-close, `/live` observe, and the order-send path to the broker all proven. The browser UI → live data (`open_positions`/`/api/live`) → `journal live` → bridge round trip WORKS and has for a long time. Only unmeasured: an *accepted* order landing — blocked solely by the MT5 container's AutoTrading toggle (a terminal setting, not code) — plus a browser visual/contrast pass. |

M0–M3 delivers the original ask: an automatic journal with charts. **Done.**
M4 onward — poller, analytics, annotations — is what makes the journal worth
returning to daily rather than a one-shot report.

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
  with reason EXPERT). At M5.1, EA and discretionary trades must be separated
  or both populations are meaningless.

---

## Open questions

- [ ] Was `symbol_select` ever actually *needed* in `copy_rates_range`? Still
      unproven either way — the underlying probe was and remains inconclusive
      (it tested an already-selected symbol). M3 added the call as insurance
      regardless (`live.py`, item 0): idempotent, one call per windowed fetch,
      matches its two neighbours. The code question is closed; the empirical
      one — does this bridge actually need it — is not, and may never be worth
      resolving now that the insurance is cheap and in place.
- [ ] `tests/test_storage_api.py` imports httpx via `TestClient`, but httpx is
      absent from `pyproject.toml`/`uv.lock` — a clean `uv sync` breaks the
      whole web test suite. Pre-existing, found during the 2026-08-04
      risk-auto-size review; fix on its own commit to `main`, not bundled
      into a feature branch.
- [ ] Funding-deal comments (`D-IDQRISGT-…`, `W-ALLINT-…`) are payment
      references, now committed to git. Zero analytical value. If this repo is
      ever pushed anywhere public, redact `comment` on funding deals only
      (`DEAL_TYPE_BALANCE/CREDIT/CHARGE/BONUS`) — never on trades, never on the
      correction. Already in history, so the cost of deciding rises with time.

**Closed:** the 14.50 gap (archived deals — see CURRENT STATE) · standalone
commission deals (none; MT5's report confirms `commission = 0.00`) ·
`BTCUSDc`/`EURUSDc` specs (M1 `symbol_specs`: tick_value 0.1 / 0.01 / 1.0 —
genuinely distinct, gold's transfer nowhere) · `MaxBars` (1,000,000 — doc §7) ·
per-symbol session hours (BTC 24/7, EUR ≈24h×5d, XAU ≈23h×5d — doc §7) ·
chart timeframe selection (duration ladder, ≤60 trade-bars, floor M1 — M3,
CURRENT STATE above) · chart cache identity (`position_id`, never `trades.id`
— M3, CURRENT STATE above).

## ~~PENDING HUMAN~~ — risk-based auto lot sizing (2026-08-04)

**CONFIRMED BY THE HUMAN 2026-08-05: all five steps below ran against the real
broker and behaved as described.** Kept as the record of what was checked. The
OPEN QUESTION at the end of this section is still open — it is a product
decision, not something a run can confirm.

The five steps, as run:

1. Start the MT5 bridge and `uv run journal live`. Confirm `journal doctor`
   reports the account and a recent tick.
2. On `/chart`, drag the SL line below the current price. Confirm the panel
   shows a lot, a risk in USC, and a Buy label — and that dragging above the
   price flips it to Sell.
3. Set the risk to the smallest workable value and open ONE position on the
   smallest symbol. Confirm: the ConfirmModal shows the intent sentence; the
   command appears in the audit log **showing the symbol and the direction**
   (not `#null` — see Finding 2 of the 2026-08-04 fix wave); `journal live`
   sends it; MT5 shows the position WITH the SL attached from the first tick.
4. Confirm the realised risk matches the panel's figure within the entry
   slippage, using `risk_amount` on the resulting trade after `journal sync`.
5. Try to open with an SL far enough away to exceed 5% of balance. Confirm the
   panel refuses and no command row is written.

**ANSWERED 2026-08-05 — the human chose option A: block the open on a stale
feed.** `lib/candles.staleEntryReason` gates the live panel's button on
`journal live`'s heartbeat AND on the age of the bar the entry price is
actually read off (stale past 2× the timeframe), with the reason shown in the
panel. Deliberately NOT wired to `views.positions_context.stale`, which the
original note suggested: that field is computed from `open_positions`, and with
no rows it returns `stale=False` (`views.py`, "cannot tell 'flat' from 'live
never ran'") — so it is always False in exactly the case that matters, the
first open on a flat account. The gate is frontend-only; the server still
accepts a stale `entry` if something else posts one. The original note, kept
for the reasoning:

**OPEN QUESTION — stale feed can size against a stale price (2026-08-04 review):**
Volume is frozen at enqueue by design; the executor's fresh-tick
re-validation (`_check_level`) catches a stop on the wrong *side* but not a
changed *size*. If `plannedEntry` (the last shown bar's close, `Chart.tsx`
`shownCandles` tail) is stale — `journal live` down, or a stale feed — the
human can size 0.10 lot against a 4035 close with a 4030 stop (50 USC
intended), the market gaps to 4060 before the command executes,
`_check_level` still passes on the SL side, and ~300 USC goes out instead of
50. Still bounded by `MAX_RISK_PCT` (5% of balance), but that can be many
multiples of the stated budget. `RiskSizePanel`'s live gate is
`disabled={!live}`, which only checks that `/api/live` responded, not that
the data is fresh. `views.positions_context` already returns `stale`/`age_s`
and `LiveData` already carries them, so if the answer is "block it" the data
is already there to wire up. If the answer is "allow it", the panel should
at least show the feed age next to the price so the divergence is visible
before the human commits size. No guard implemented — this is a product
decision, not a bug.
