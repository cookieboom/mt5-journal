# Graph Report - mt5-journal  (2026-07-23)

## Corpus Check
- 62 files · ~70,800 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 729 nodes · 1743 edges · 39 communities (32 shown, 7 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 66 edges (avg confidence: 0.67)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `cec87d92`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Reporting & Session Analytics
- Candles Ingest & Chart Rendering
- Deal Ingest & Balance Reconciliation
- Trade Reconstruction Tests
- Domain Rules & MT5 Traps
- SL/TP Poller (M4)
- Report Builder Tests
- Annotations & Manual Tags
- Reconstruction Internals
- CLI Commands
- Auto-Tagging (M6)
- Web Display Formatters
- Web Context & Tests
- Fixtures & Symbol Normalisation
- Rate/History Probes
- Fake Adapter & Protocol Tests
- DB Connection & Command Wrappers
- CLI Human-Layer Wrappers
- Adapter Boundary & Enums
- Live MT5 Adapter
- MAE/MFE Excursion (M5)
- Fake Adapter Builders
- Web App Factory & Weekly
- MT5Client Protocol
- Risk Amount (R-multiple base)
- Enum Probe
- Rebuild Post-Processing Concepts
- Process & Improvement Concepts
- Analytics Package
- Ingest Package
- Hedging Account Model
- Project Root Concept
- Cleanup plan — repo hygiene, stale docs, fixture sanitization (2026-07-23)
- reconcile_add
- Charts are cache, not data

## God Nodes (most connected - your core abstractions)
1. `sync()` - 45 edges
2. `reconstruct()` - 44 edges
3. `FakeMT5Client` - 39 edges
4. `rebuild()` - 34 edges
5. `connect()` - 34 edges
6. `build_report()` - 32 edges
7. `render_trade()` - 32 edges
8. `LiveMT5Client` - 29 edges
9. `_deal()` - 29 edges
10. `one_account_login()` - 27 edges

## Surprising Connections (you probably didn't know these)
- `test_builds_declared_types_from_fixtures()` --indirect_call--> `Account`  [INFERRED]
  tests/test_adapter.py → src/journal/adapter/base.py
- `test_builds_declared_types_from_fixtures()` --indirect_call--> `SymbolInfo`  [INFERRED]
  tests/test_adapter.py → src/journal/adapter/base.py
- `test_builds_declared_types_from_fixtures()` --indirect_call--> `Tick`  [INFERRED]
  tests/test_adapter.py → src/journal/adapter/base.py
- `DropDealClient` --uses--> `Tick`  [INFERRED]
  tests/test_ingest.py → src/journal/adapter/base.py
- `TickClient` --uses--> `Tick`  [INFERRED]
  tests/test_ingest.py → src/journal/adapter/base.py

## Import Cycles
- None detected.

## Communities (39 total, 7 thin omitted)

### Community 0 - "Reporting & Session Analytics"
Cohesion: 0.06
Nodes (69): bucket_stat(), BucketStat, Row, `journal report` — a first, honest read of this account's performance (M5).  Mon, One row of a behaviour breakdown (a session, or EA/discretionary). Same     gati, Aggregate one bucket's closed-trade rows into a `BucketStat`, reusing the     ex, ReportResult, Trading-session bucketing (M5.1).  Maps a trade's open time to one of five fixed (+61 more)

### Community 1 - "Candles Ingest & Chart Rendering"
Cohesion: 0.06
Nodes (66): chart(), Render one trade to a PNG in `cache/` (M3). Pure DB, no bridge needed —     read, CandlesReport, _insert_candle(), _ms_to_dt(), Connection, datetime, Ingest OHLC candles for reconstructed trades into the central `candles` store. (+58 more)

### Community 2 - "Deal Ingest & Balance Reconciliation"
Cohesion: 0.05
Nodes (66): DELETE + re-INSERT `trades` for the account from the append-only `_raw` tables., rebuild(), RebuildReport, add_reconciliation(), _detect_archived(), _ingest_deals(), _ingest_orders(), _ingest_symbol_specs() (+58 more)

### Community 3 - "Trade Reconstruction Tests"
Cohesion: 0.14
Nodes (45): The earliest KNOWN state of `field` ('sl' or 'tp') from a chronological     list, Group trade deals by `position_id` and fold each group into a `Trade`.      `ord, One poller observation of a live position's SL/TP (`sl_tp_snapshots`, M4).     `, reconstruct(), _resolve_poller_price(), SlTpSnapshot, _deal(), _dt_ms() (+37 more)

### Community 4 - "Domain Rules & MT5 Traps"
Cohesion: 0.12
Nodes (17): MT5Client Protocol adapter boundary, Timestamps are epoch milliseconds UTC, Sample-size honesty rule (n<20 suppressed), Symbols stored twice (symbol / symbol_base), USC cent currency unit rule, M7 web dashboard (journal serve), Milestone roadmap M0–M7, Risk calculation reference figure (+9 more)

### Community 5 - "SL/TP Poller (M4)"
Cohesion: 0.13
Nodes (35): _changed(), _floats_equal(), _last_snapshot(), LoopReport, poll_loop(), poll_once(), PollReport, Connection (+27 more)

### Community 6 - "Report Builder Tests"
Cohesion: 0.20
Nodes (29): build_report(), Connection, Pure DB read, no client — mirrors `verify`/`rebuild`. Resolves the     account l, _bucket(), _ms(), M5 `journal report` — `build_report()`. Money-based stats at full closed- trade, Epoch ms (UTC) at a fixed date and the given UTC hour — for placing a     seeded, _seed_account() (+21 more)

### Community 7 - "Annotations & Manual Tags"
Cohesion: 0.17
Nodes (27): add_tag(), AnnotateError, get_annotation(), list_tags(), Connection, Row, M6 human layer — annotation + manual-tag writes.  This is user input, NOT MT5 in, Attach a `source='manual'` tag (idempotent — INSERT OR IGNORE on the PK).     Re (+19 more)

### Community 8 - "Reconstruction Internals"
Cohesion: 0.11
Nodes (27): Deal, Order, _fill_auto_tags(), _fill_excursions(), _is_trade_deal(), _load_deals(), _load_orders(), _load_sl_snapshots() (+19 more)

### Community 9 - "CLI Commands"
Cohesion: 0.10
Nodes (25): _bucket_line(), doctor(), _fmt(), _gated(), _main(), _parse_iso_week(), `journal` CLI. M0 shipped `doctor`; M1 adds `sync` (ingest deals/orders → `_raw`, Pull deals/orders/specs from the live bridge into the `_raw` tables.      Append (+17 more)

### Community 10 - "Auto-Tagging (M6)"
Cohesion: 0.20
Nodes (22): compute_auto_tags(), datetime, M6 auto-tagging — the STRUCTURAL half of the tag system.  `compute_auto_tags` is, Epoch ms -> aware UTC datetime. The one conversion point, so the rule-3     'nev, The auto-tags a single closed `trade` earns. Structural facts only:        sub-1, _utc(), _ms(), M6 auto-tag computation — `domain/tags.py`.  Written before the implementation ( (+14 more)

### Community 11 - "Web Display Formatters"
Cohesion: 0.09
Nodes (22): dur(), gated(), is_gated(), money(), num(), pct(), price(), Connection (+14 more)

### Community 12 - "Web Context & Tests"
Cohesion: 0.16
Nodes (14): M7 web dashboard — a read-mostly HTML layer over the existing analytics.  This p, conn(), _ms(), M7 web dashboard — pure formatters (`web/format.py`) and the DB→context builders, _seed_account(), _seed_trade(), test_account_header(), test_dashboard_context_agrees_with_build_report() (+6 more)

### Community 13 - "Fixtures & Symbol Normalisation"
Cohesion: 0.17
Nodes (17): main(), _num(), Any, Scrub the payment-provider reference off a funding-class deal's `comment`.     A, Generic, key-based scrub applied to every record. Only touches keys that     are, redact_funding_comment(), sanitise(), write() (+9 more)

### Community 14 - "Rate/History Probes"
Cohesion: 0.22
Nodes (16): load_trades(), main(), probe_coverage(), probe_maxbars(), probe_reach(), probe_select_dependency(), Any, datetime (+8 more)

### Community 15 - "Fake Adapter & Protocol Tests"
Cohesion: 0.14
Nodes (15): _build(), FakeMT5Client, Any, Path, Same contract as live.py's _build: keep declared fields, stash `raw`., Implements `MT5Client` over JSON fixtures. Missing/empty fixture -> None/[]., FakeMT5Client must satisfy the MT5Client Protocol and return the declared types, test_account_equity_maps_from_fixture_and_defaults_none() (+7 more)

### Community 16 - "DB Connection & Command Wrappers"
Cohesion: 0.13
Nodes (16): candles(), Fetch OHLC bars for every closed trade's chart window into `candles` (M3)., List every reconciliation row., reconcile_list(), connect(), _is_fresh(), Connection, Path (+8 more)

### Community 17 - "CLI Human-Layer Wrappers"
Cohesion: 0.15
Nodes (15): annotate(), _echo_tags(), _one_account_login(), poll(), Connection, Snapshot live open positions' SL/TP into `sl_tp_snapshots` (M4).      Needs the, CLI wrapper over the single-source guard in store/db.py — translates its     Run, Print a trade's tags, grouped source-first (`list_tags` already orders     them) (+7 more)

### Community 18 - "Adapter Boundary & Enums"
Cohesion: 0.18
Nodes (10): IntEnum, Account, Candle, DealEntry, DealReason, DealType, Position, The MT5 boundary.  Everything the rest of the codebase is allowed to know about (+2 more)

### Community 19 - "Live MT5 Adapter"
Cohesion: 0.19
Nodes (8): RuntimeError, Tick, _build(), LiveMT5Client, Any, Map a bridge `._asdict()` into our dataclass: keep declared fields, stash     th, Implements `MT5Client` over the siliconmetatrader5 bridge., Our IntEnums are authoritative for the codebase; where the bridge         expose

### Community 20 - "MAE/MFE Excursion (M5)"
Cohesion: 0.23
Nodes (12): compute_excursion(), MAE/MFE — how far price ran against you and for you during a trade (M5).  `compu, rows: (time_msc, low, high) tuples, already scoped to one trade's own     padded, M5 MAE/MFE — `compute_excursion()`, pure and fixture-tested (CLAUDE.md rule 7)., test_buy_direction(), test_empty_rows_is_no_coverage(), test_every_row_after_open_time_is_no_coverage(), test_floors_at_zero_never_negative() (+4 more)

### Community 21 - "Fake Adapter Builders"
Cohesion: 0.18
Nodes (12): NULL means unknown, 0 means none set, deals_raw/orders_raw append-only source of truth, 14.50 USC gap resolved (archived deals), M4 SL/TP poller (sl_tp_snapshots), Truthiness guard pattern (known-zero vs unknown), Balance invariant (partition check), SL provenance is EA-only (6 of 68), Order / Deal / Position mental model (+4 more)

### Community 22 - "Web App Factory & Weekly"
Cohesion: 0.27
Nodes (9): FastAPI, last_complete_iso_week(), datetime, The most recent ISO week entirely before `now` (default: current UTC time)., Remove a MANUAL tag. The `source='manual'` filter means an auto tag can     neve, remove_tag(), create_app(), FastAPI app factory for the M7 web dashboard.  `create_app(db_path)` builds the (+1 more)

### Community 23 - "MT5Client Protocol"
Cohesion: 0.27
Nodes (5): Protocol, MT5Client, Any, The one interface the rest of the codebase depends on.      `adapter/live.py` im, SymbolInfo

### Community 24 - "Risk Amount (R-multiple base)"
Cohesion: 0.27
Nodes (9): Risk in account currency — the one number every R-multiple rests on.  `risk_amou, `(|open_price - sl_initial| / tick_size) * tick_value * volume`, in account, risk_amount(), M2 risk_amount — the §8 reference figure, computed by hand.  `risk_amount` is th, test_direction_does_not_matter(), test_missing_spec_gives_null_risk(), test_null_sl_gives_null_risk(), test_reference_figure_is_50_usc() (+1 more)

### Community 25 - "Enum Probe"
Cohesion: 0.83
Nodes (3): dump(), main(), MetaTrader5

### Community 36 - "Cleanup plan — repo hygiene, stale docs, fixture sanitization (2026-07-23)"
Cohesion: 0.18
Nodes (10): Cleanup plan — repo hygiene, stale docs, fixture sanitization (2026-07-23), CRITICAL: what is NOT junk (do not remove), Out of scope (noted, not part of this cleanup), Phase 0 — Baseline (prove green before touching anything), Phase 1 — On-disk junk (zero risk, no git-tracked files touched), Phase 2 — .gitignore + tracking decisions, Phase 3 — Resolve uncommitted working-tree changes, Phase 4 — Stale docstrings/comments (functional but misleading) (+2 more)

### Community 37 - "reconcile_add"
Cohesion: 0.50
Nodes (4): _parse_effective(), `--effective` is a UTC wall-clock 'YYYY-MM-DD HH:MM:SS' → epoch ms., Record one named explanation for a residual. The gap does not disappear — it, reconcile_add()

## Knowledge Gaps
- **18 isolated node(s):** `mt5-journal`, `CRITICAL: what is NOT junk (do not remove)`, `Phase 0 — Baseline (prove green before touching anything)`, `Phase 1 — On-disk junk (zero risk, no git-tracked files touched)`, `Phase 2 — .gitignore + tracking decisions` (+13 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `one_account_login()` connect `Reporting & Session Analytics` to `Candles Ingest & Chart Rendering`, `Deal Ingest & Balance Reconciliation`, `Report Builder Tests`, `Annotations & Manual Tags`, `Reconstruction Internals`, `CLI Commands`, `DB Connection & Command Wrappers`, `CLI Human-Layer Wrappers`, `Live MT5 Adapter`, `Web App Factory & Weekly`?**
  _High betweenness centrality (0.081) - this node is a cross-community bridge._
- **Why does `FakeMT5Client` connect `Fake Adapter & Protocol Tests` to `Candles Ingest & Chart Rendering`, `Deal Ingest & Balance Reconciliation`, `SL/TP Poller (M4)`, `Report Builder Tests`, `Annotations & Manual Tags`, `Reconstruction Internals`, `Fixtures & Symbol Normalisation`, `Adapter Boundary & Enums`, `Live MT5 Adapter`, `MT5Client Protocol`?**
  _High betweenness centrality (0.051) - this node is a cross-community bridge._
- **Why does `LiveMT5Client` connect `Live MT5 Adapter` to `Reconstruction Internals`, `CLI Commands`, `Fixtures & Symbol Normalisation`, `DB Connection & Command Wrappers`, `CLI Human-Layer Wrappers`, `Adapter Boundary & Enums`, `MT5Client Protocol`?**
  _High betweenness centrality (0.041) - this node is a cross-community bridge._
- **Are the 12 inferred relationships involving `FakeMT5Client` (e.g. with `Account` and `Candle`) actually correct?**
  _`FakeMT5Client` has 12 INFERRED edges - model-reasoned connections that need verification._
- **What connects `mt5-journal`, `CRITICAL: what is NOT junk (do not remove)`, `Phase 0 — Baseline (prove green before touching anything)` to the rest of the system?**
  _18 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Reporting & Session Analytics` be split into smaller, more focused modules?**
  _Cohesion score 0.05661005661005661 - nodes in this community are weakly interconnected._
- **Should `Candles Ingest & Chart Rendering` be split into smaller, more focused modules?**
  _Cohesion score 0.057902973395931145 - nodes in this community are weakly interconnected._