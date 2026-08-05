# Gap-aware `sync_candles` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `journal live` freezing its forming bar, heartbeat, and command queue for ~5 minutes on every position close, by making `sync_candles` fetch only the candle ranges it does not already have.

**Architecture:** `sync_candles` currently issues one `copy_rates_range` per closed trade — 123 bridge round trips per close on this account — because it writes candle coverage but never reads it. It delegates instead to `ingest/candle_fill.py:fill_range`, which already reads coverage, fetches only `missing_ranges`, and keeps the SQLite write lock off the network path. A hard cap on how many windows one invocation may fetch bounds the worst case; the CLI runs uncapped so a backlog can be primed in one go.

**Tech Stack:** python 3.12, sqlite3 (stdlib), pytest, typer. No new dependencies (CLAUDE.md rule 8).

**Spec:** `docs/superpowers/specs/2026-08-05-gap-aware-sync-candles-design.md`

## Global Constraints

- CLAUDE.md rule 1: never `import MetaTrader5` outside `src/journal/adapter/`. Every test here runs under `FakeMT5Client` with no bridge.
- CLAUDE.md rule 3: all timestamps are epoch **milliseconds**, integer, UTC.
- CLAUDE.md rule 5: money and prices are `REAL`; compare with tolerance, never `==`.
- CLAUDE.md rule 7: tests before implementation for `domain/` and `analytics/`; this plan applies the same discipline to `ingest/`.
- CLAUDE.md rule 8: do not add dependencies.
- Definition of done for every task: `uv run pytest` passes and the **actual output is pasted**. At the end of the plan, `uv run journal rebuild` must still succeed.
- `web/` must never import `ingest/candle_fill.py` or `ingest/live_candles.py`. This plan does not change that; do not add such an import.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/journal/ingest/candles.py` | Per-trade candle window ingest | Modify — delegate to `fill_range`, add cap, swap report fields |
| `src/journal/cli.py` | `journal candles` command output | Modify — uncapped call + two report lines |
| `src/journal/ingest/live.py` | On-close ingest pipeline | Modify — log a remaining backlog |
| `tests/test_candles.py` | Candle ingest tests | Modify — one new fixture helper + three new tests |

No new files. No new tables, no migration, no schema change.

## Reference: the code as it stands today

`src/journal/ingest/candles.py` — the loop being replaced (lines 68–91):

```python
    for r in rows:
        tf = choose_timeframe(r["duration_s"])
        from_msc, to_msc = window_for(r["open_time_msc"], r["close_time_msc"], tf)
        candles = client.copy_rates_range(
            r["symbol"], tf, _ms_to_dt(from_msc), _ms_to_dt(to_msc)
        )
        symbols_touched.add(r["symbol"])
        stored: list[int] = []
        for c in candles:
            bars_seen += 1
            bars_new += candles_store.insert_candle(conn, r["symbol"], tf, c)
            if c.time_msc is not None:
                stored.append(c.time_msc)
        candles_store.record_fetch(conn, r["symbol"], tf, from_msc, to_msc, stored, now)
```

`src/journal/ingest/candle_fill.py:fill_range` — the replacement, already written and already tested:

```python
def fill_range(client, conn, symbol, timeframe, from_ms, to_ms, now_msc) -> int:
    cur_bucket = bucket_start(now_msc, timeframe)
    covered = cs.read_coverage(conn, symbol, timeframe)
    # Phase 1 — network only, no write lock held.
    fetched = []
    for lo, hi in cs.missing_ranges(covered, (from_ms, to_ms)):
        bars = client.copy_rates_range(symbol, timeframe, _ms_to_dt(lo), _ms_to_dt(hi))
        fetched.append((lo, hi, bars))
    # Phase 2 — local writes only, one short transaction.
    ...
    conn.commit()
    return bars_new
```

Facts verified against the code that the tests below depend on:

- `render/chart.py:PAD_BARS = 15`. For an M1 trade opened at `open_msc` lasting 373 s, `window_for` returns `(open_msc - 900_000, open_msc + 1_273_000)`.
- `adapter/fake.py:FakeMT5Client.copy_rates_range` **ignores `date_from`/`date_to`** and returns every bar stored under the `"SYMBOL:TF"` fixture key. So a fixture whose bars stop short of the window end can never seal that window under the fake — `record_fetch` claims only to the last bar returned, and the leftover tail is re-offered forever. Tests that need a window to seal must write bars past `to_msc`.
- `store/candles_store.py:record_fetch` claims to `min(to_ms, current_bucket - 1, max(bar_times) + timeframe_ms - 1)`, and a **zero-bar** response claims the whole requested range.
- `store/candles_store.py:insert_candle` raises `ValueError` mentioning `"Trap 15"` when handed seconds instead of milliseconds. `fill_range` does not catch it, so the existing tripwire test keeps working unchanged.

---

### Task 1: Make `sync_candles` gap-aware

**Files:**
- Modify: `src/journal/ingest/candles.py:39-101`
- Test: `tests/test_candles.py`

**Interfaces:**
- Consumes: `journal.ingest.candle_fill.fill_range(client, conn, symbol, timeframe, from_ms, to_ms, now_msc) -> int`, `journal.store.candles_store.read_coverage(conn, symbol, timeframe) -> list[tuple[int, int]]`, `journal.store.candles_store.missing_ranges(covered, want) -> list[tuple[int, int]]`
- Produces: `sync_candles(client, conn) -> CandlesReport` with `CandlesReport` fields `account_login`, `trades_seen`, `trades_skipped_open`, `bars_new`, `windows_fetched`, `symbols`. The field `bars_seen` is **removed**. Task 2 adds `windows_pending`; Task 3 reads `windows_fetched` and `windows_pending`.

- [ ] **Step 1: Add the multi-symbol fixture helper to the test file**

Append next to the existing `_write_rates` helper in `tests/test_candles.py` (after line 67). The existing `_write_rates` stays exactly as it is — other tests depend on its defaults.

```python
def _write_rates_multi(fx_dir, anchors, n_before=20, n_after=22, tf_seconds=60):
    """One rates.json holding several `SYMBOL:TF` keys at once.

    `anchors` maps a fixture key to the anchor ms the bars are centred on.
    `n_after=22` is deliberate: for an M1 trade of 373 s the render window ends
    at `open_msc + 1_273_000` (PAD_BARS=15), and the last bar written here opens
    at `open_msc + 1_260_000`, so `record_fetch` claims the window to its very
    end. A shorter tail leaves the window permanently unsealed under
    FakeMT5Client, which ignores the requested date range.
    """
    fx_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for key, anchor_msc in anchors.items():
        base_s = anchor_msc // 1000
        out[key] = [
            {"time": base_s + i * tf_seconds, "open": 4035.0, "high": 4035.2,
             "low": 4034.8, "close": 4035.1, "tick_volume": 10, "spread": 1,
             "real_volume": 0}
            for i in range(-n_before, n_after)
        ]
    (fx_dir / "rates.json").write_text(json.dumps(out))


class CountingClient(FakeMT5Client):
    """FakeMT5Client that records every bridge fetch, so a test can assert that
    an already-covered window costs ZERO round trips."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fetches: list[tuple[str, str]] = []

    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        self.fetches.append((symbol, timeframe))
        return super().copy_rates_range(symbol, timeframe, date_from, date_to)
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_candles.py`, in the `sync_candles` section:

```python
def test_sync_candles_refetches_nothing_once_covered(conn, tmp_path):
    """The five-minute stall: sync_candles wrote coverage but never read it, so
    every position close re-fetched all 123 closed-trade windows from the bridge.
    Once a window is covered it must cost zero round trips."""
    open_msc = 1_700_000_000_000
    close_msc = open_msc + 373_000
    _insert_trade(conn, position_id=555, open_msc=open_msc, close_msc=close_msc,
                  duration_s=373)
    fx = tmp_path / "fixtures"
    _write_rates_multi(fx, {"XAUUSDc:M1": open_msc})
    client = CountingClient(fixtures_dir=fx)

    first = sync_candles(client, conn)
    assert first.windows_fetched == 1
    assert len(client.fetches) == 1

    client.fetches.clear()
    second = sync_candles(client, conn)
    assert client.fetches == []          # the whole point
    assert second.windows_fetched == 0
    assert second.bars_new == 0
    assert second.trades_seen == 1       # still processed, just for free
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_candles.py::test_sync_candles_refetches_nothing_once_covered -v`

Expected: FAIL with `AttributeError: 'CandlesReport' object has no attribute 'windows_fetched'`.

- [ ] **Step 4: Rewrite `sync_candles`**

In `src/journal/ingest/candles.py`, add the import next to the existing ones:

```python
from .candle_fill import fill_range
```

Replace the `CandlesReport` dataclass (lines 39-46) with:

```python
@dataclass(frozen=True)
class CandlesReport:
    account_login: int | None = None
    trades_seen: int = 0            # closed trades processed this run
    trades_skipped_open: int = 0    # open/partially_open -- no close_time yet
    bars_new: int = 0               # bars actually inserted (post PK-dedupe)
    windows_fetched: int = 0        # windows that hit the bridge this run
    symbols: list[str] = field(default_factory=list)
```

`bars_seen` is gone on purpose: with gap-aware fetching, "bars the bridge
returned" no longer approximates "bars we looked at". Its only consumer is one
CLI line, fixed in Task 3. No test asserts it.

Replace everything in `sync_candles` from the `rows = conn.execute(...)` query
down to and including the closing `return CandlesReport(...)` with:

```python
    rows = conn.execute(
        "SELECT symbol, open_time_msc, close_time_msc, duration_s FROM trades "
        "WHERE account_login = ? AND status = 'closed' "
        "ORDER BY close_time_msc DESC",
        (login,),
    ).fetchall()

    trades_seen = 0
    bars_new = 0
    windows_fetched = 0
    symbols_touched: set[str] = set()
    # A window runs to close + PAD_BARS, so a trade that closed moments ago has a
    # window reaching into the future: the bridge answers with bars only up to
    # the present and `record_fetch` claims no further (2026-08-05 hole — a trade
    # closed 21:34, sync ran 21:43, and the whole-range claim sealed 21:44-21:49
    # as fetched forever).
    now = now_ms()

    for r in rows:
        tf = choose_timeframe(r["duration_s"])
        from_msc, to_msc = window_for(r["open_time_msc"], r["close_time_msc"], tf)
        trades_seen += 1
        symbols_touched.add(r["symbol"])
        # Coverage decides whether this window costs a bridge round trip at all.
        # This USED to fetch unconditionally: 123 closed trades on this account
        # meant 123 round trips on every single position close, ~5 minutes, and
        # `journal live` is one serial loop — the forming bar, the liveness beat,
        # and any queued order all sat behind it.
        covered = candles_store.read_coverage(conn, r["symbol"], tf)
        if not candles_store.missing_ranges(covered, (from_msc, to_msc)):
            continue
        # fill_range re-reads coverage internally. That duplicate SELECT is the
        # price of keeping its signature honest, and it is cheap next to the
        # network call it replaces.
        bars_new += fill_range(client, conn, r["symbol"], tf, from_msc, to_msc, now)
        windows_fetched += 1

    conn.commit()

    return CandlesReport(
        account_login=login,
        trades_seen=trades_seen,
        trades_skipped_open=total_trades - len(rows),
        bars_new=bars_new,
        windows_fetched=windows_fetched,
        symbols=sorted(symbols_touched),
    )
```

`ORDER BY close_time_msc DESC` has no effect yet — Task 2's cap is what makes it
matter (the freshly closed trade must be served first). It goes in now so the
query is written once.

The module docstring's second paragraph still describes the old inline fetch.
Replace the sentence beginning "For each CLOSED trade, fetches the render
window" with:

```
For each CLOSED trade, fetches the render window (`render.chart.choose_timeframe`
/ `window_for`) at the trade's own chosen timeframe via `candle_fill.fill_range`,
which consults `candle_coverage` first and asks the bridge only for the ranges
not already stored. A trade whose window is fully covered costs one SELECT and
no bridge call at all.
```

Finally, delete the now-dead local helper and its import — `fill_range` does its
own ms→datetime conversion, so nothing in this module builds a `datetime` any
more:

```python
from datetime import datetime, timezone      # delete this line


def _ms_to_dt(msc: int) -> datetime:         # delete this function
    return datetime.fromtimestamp(msc / 1000, tz=timezone.utc)
```

- [ ] **Step 5: Run the new test to verify it passes**

Run: `uv run pytest tests/test_candles.py::test_sync_candles_refetches_nothing_once_covered -v`

Expected: PASS.

- [ ] **Step 6: Run the whole candle suite — the existing tests are the regression net**

Run: `uv run pytest tests/test_candles.py -v`

Expected: PASS, including all six pre-existing `sync_candles` tests. Two are worth
watching because they encode subtle contracts:

- `test_sync_candles_rejects_seconds_leaked_as_msc` — `fill_range` does not catch
  exceptions, so `insert_candle`'s Trap-15 `ValueError` still propagates.
- `test_sync_candles_does_not_claim_coverage_past_the_bars_it_got` — uses the
  original `_write_rates` (bars stop 20 min after open, short of the window end),
  so exactly one coverage range is recorded and its `hi` stays below `to_msc`.

If either fails, stop and read the failure before changing anything: they are the
two behaviours most likely to break under this refactor.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`

Expected: PASS. `src/journal/cli.py:369` still reads `r.bars_seen` and will raise
`AttributeError` **only when `journal candles` is actually run** — no test covers
that line, so the suite stays green. Task 3 fixes it. Do not fix it here.

- [ ] **Step 8: Commit**

```bash
git add src/journal/ingest/candles.py tests/test_candles.py
git commit -m "perf(candles): fetch only uncovered windows in sync_candles"
```

---

### Task 2: Cap the work one invocation may do

**Files:**
- Modify: `src/journal/ingest/candles.py`
- Test: `tests/test_candles.py`

**Interfaces:**
- Consumes: everything Task 1 produced.
- Produces: `sync_candles(client, conn, *, max_windows: int | None = _MAX_FETCH_WINDOWS) -> CandlesReport`, `CandlesReport.windows_pending: int`, module constant `_MAX_FETCH_WINDOWS: int = 5`. Passing `max_windows=None` disables the cap entirely — Task 3's CLI relies on that.

- [ ] **Step 1: Write the failing cap test**

Append to `tests/test_candles.py`:

```python
def test_sync_candles_caps_fetches_and_serves_newest_first(conn, tmp_path):
    """Backlog protection. With 12 uncovered windows and a cap of 5, one run may
    hit the bridge 5 times and no more — a restart after `journal live` was down
    for days must not turn a position close into a multi-minute stall. The 5 it
    picks are the most recent closes, so the trade that just closed always gets
    its candles (and therefore its MAE/MFE) in the same run."""
    from journal.ingest import candles as candles_mod

    base = 1_700_000_000_000
    anchors = {}
    for i in range(12):
        open_msc = base + i * 86_400_000          # one trade per day, no overlap
        _insert_trade(conn, position_id=600 + i, open_msc=open_msc,
                      close_msc=open_msc + 373_000, duration_s=373,
                      symbol=f"T{i:02d}c")
        anchors[f"T{i:02d}c:M1"] = open_msc
    fx = tmp_path / "fixtures"
    _write_rates_multi(fx, anchors)
    client = CountingClient(fixtures_dir=fx)

    r = sync_candles(client, conn, max_windows=5)

    assert r.windows_fetched == 5
    assert len(client.fetches) == 5
    assert r.windows_pending == 7
    # newest close first: T11..T07
    assert sorted(sym for sym, _ in client.fetches) == [f"T{i:02d}c" for i in range(7, 12)]
    assert candles_mod._MAX_FETCH_WINDOWS == 5      # the default the live loop uses
```

- [ ] **Step 2: Write the failing drain test**

Append to `tests/test_candles.py`:

```python
def test_sync_candles_backlog_drains_without_refetching(conn, tmp_path):
    """Repeated capped runs finish the backlog, and no window is ever fetched
    twice. 12 windows at a cap of 5 = 3 runs, 12 fetches total."""
    base = 1_700_000_000_000
    anchors = {}
    for i in range(12):
        open_msc = base + i * 86_400_000
        _insert_trade(conn, position_id=700 + i, open_msc=open_msc,
                      close_msc=open_msc + 373_000, duration_s=373,
                      symbol=f"U{i:02d}c")
        anchors[f"U{i:02d}c:M1"] = open_msc
    fx = tmp_path / "fixtures"
    _write_rates_multi(fx, anchors)
    client = CountingClient(fixtures_dir=fx)

    runs = 0
    while True:
        r = sync_candles(client, conn, max_windows=5)
        runs += 1
        if r.windows_pending == 0 and r.windows_fetched == 0:
            break
        assert runs < 10, "backlog is not draining — a window is being re-offered"

    assert runs == 4                      # 5 + 5 + 2 fetched, then one clean pass
    assert len(client.fetches) == 12      # every window fetched exactly once
    assert len(set(client.fetches)) == 12
```

- [ ] **Step 3: Run both tests to verify they fail**

Run: `uv run pytest tests/test_candles.py -k "caps_fetches or backlog_drains" -v`

Expected: FAIL with `TypeError: sync_candles() got an unexpected keyword argument 'max_windows'`.

- [ ] **Step 4: Add the cap**

In `src/journal/ingest/candles.py`, add the constant below the imports:

```python
# How many windows ONE invocation may fetch from the bridge. `journal live` runs
# this pipeline inside its serial cycle, so an unbounded backlog (first run after
# coverage was introduced; a restart after days offline) would stall the forming
# bar and the liveness beat for as long as the backlog takes. Five windows is
# roughly twelve seconds against a ~2.5 s round trip — a knob to tune against
# measured bridge latency, not a derived constant. `journal candles` passes
# max_windows=None to prime a backlog in one deliberate, foreground run.
_MAX_FETCH_WINDOWS = 5
```

Add `windows_pending` to `CandlesReport`, after `windows_fetched`:

```python
    windows_pending: int = 0        # windows left untouched because the cap closed
```

Change the signature:

```python
def sync_candles(
    client: MT5Client,
    conn: sqlite3.Connection,
    *,
    max_windows: int | None = _MAX_FETCH_WINDOWS,
) -> CandlesReport:
```

Initialise the counter next to the others:

```python
    windows_pending = 0
```

Replace the coverage check inside the loop with:

```python
        covered = candles_store.read_coverage(conn, r["symbol"], tf)
        needs_fetch = bool(candles_store.missing_ranges(covered, (from_msc, to_msc)))
        if needs_fetch and max_windows is not None and windows_fetched >= max_windows:
            # Deferred, not skipped: the next invocation picks it up. Counted so
            # the caller can say a backlog remains instead of looking complete.
            windows_pending += 1
            continue
        trades_seen += 1
        symbols_touched.add(r["symbol"])
        if needs_fetch:
            bars_new += fill_range(
                client, conn, r["symbol"], tf, from_msc, to_msc, now
            )
            windows_fetched += 1
```

Note the `trades_seen += 1` and `symbols_touched.add(...)` lines move **into**
this block, below the cap check — a deferred trade was not processed and must
not be counted. Delete the two copies that Task 1 placed above the coverage
read.

Add `windows_pending=windows_pending` to the returned `CandlesReport`.

Update the `sync_candles` docstring to:

```python
    """Fetch and store candles for every closed trade's render window. Idempotent
    and additive: coverage is consulted first, so a window already stored costs no
    bridge call. Newest close first, and at most `max_windows` windows are fetched
    per invocation (`None` = no cap) — the rest are reported as
    `windows_pending` and picked up next run."""
```

- [ ] **Step 5: Run both tests to verify they pass**

Run: `uv run pytest tests/test_candles.py -k "caps_fetches or backlog_drains" -v`

Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`

Expected: PASS. Existing tests call `sync_candles(client, conn)` with one trade,
so the default cap of 5 is never reached and their behaviour is unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/journal/ingest/candles.py tests/test_candles.py
git commit -m "feat(candles): cap bridge fetches per sync_candles run"
```

---

### Task 3: Wire the cap and the new counters into the CLI and the live loop

**Files:**
- Modify: `src/journal/cli.py:345-371`
- Modify: `src/journal/ingest/live.py:170-190`

**Interfaces:**
- Consumes: `sync_candles(client, conn, *, max_windows=...) -> CandlesReport` with `bars_new`, `windows_fetched`, `windows_pending`, `trades_seen`, `trades_skipped_open`, `symbols`.
- Produces: nothing later tasks depend on. This is the last task.

- [ ] **Step 1: Fix the `journal candles` command**

In `src/journal/cli.py`, inside the `candles` command, change the call so the
foreground command runs uncapped:

```python
        # No cap here: this is a deliberate foreground command a human is
        # watching, and it is how a large backlog gets primed in one run. The
        # cap exists to protect `journal live`'s serial cycle, which this is not.
        r = sync_candles(client, conn, max_windows=None)
```

Replace the `bars:` echo line (currently `f"bars:           {r.bars_new} new, {r.bars_seen - r.bars_new} already had"`) with:

```python
    typer.echo(
        f"bars:           {r.bars_new} new from {r.windows_fetched} window(s) fetched"
    )
    typer.echo(f"pending:        {r.windows_pending} window(s) left for the next run")
```

Also update the command's docstring line "Idempotent: bars already stored are
skipped (PK-deduped on `symbol, timeframe, time_msc`)." to:

```
    Idempotent and cheap to re-run: `candle_coverage` is consulted first, so a
    window already stored is not re-fetched at all. Runs uncapped — unlike the
    same pipeline inside `journal live`, which limits itself to a few windows per
    position close so the forming bar keeps streaming.
```

- [ ] **Step 2: Report a remaining backlog from the live pipeline**

In `src/journal/ingest/live.py`, `_run_ingest_pipeline` currently discards the
report. Capture it and log a backlog, so a human running `journal live` can see
that older trades still have candles pending:

```python
    run_sync(client, conn)
    run_rebuild(conn)
    report = sync_candles(client, conn)
    if report.windows_pending:
        log.info(
            "live: %d candle window(s) still pending after this ingest — capped at "
            "%d per close so the forming bar keeps streaming; run `journal candles` "
            "to drain the backlog in one go",
            report.windows_pending,
            report.windows_fetched,
        )
    run_rebuild(conn)
```

Append to that function's docstring:

```
    `sync_candles` caps how many candle windows it fetches per run, so a large
    backlog drains across several closes rather than stalling this loop. What is
    left is logged, never re-raised.
```

- [ ] **Step 3: Verify the live tests still pass**

Run: `uv run pytest tests/test_live.py -v`

Expected: PASS. `tests/test_live.py:115` and `:255` monkeypatch
`journal.ingest.candles.sync_candles` with `lambda client, conn: ...` returning a
list or `None`, and the new code calls `.windows_pending` on that return value.
If either test now fails with `AttributeError`, fix the **test doubles**, not the
production code — give them a return value that carries the field:

```python
monkeypatch.setattr(
    "journal.ingest.candles.sync_candles",
    lambda client, conn: (calls.append("candles"), CandlesReport())[1],
)
```

with `from journal.ingest.candles import CandlesReport` added to that test
module's imports. Keep each double's existing call-recording behaviour intact.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`

Expected: PASS. Paste the actual output (definition of done).

- [ ] **Step 5: Verify `journal candles` no longer crashes on the removed field**

`r.bars_seen` was the one line no test covered. Confirm it is gone:

Run: `rg "bars_seen" src tests`

Expected: no matches.

- [ ] **Step 6: Verify the rebuild still succeeds**

Run: `uv run journal rebuild`

Expected: completes without error. Paste the output.

- [ ] **Step 7: Commit**

```bash
git add src/journal/cli.py src/journal/ingest/live.py tests/test_live.py
git commit -m "feat(candles): run journal candles uncapped, log live backlog"
```

---

## After the plan

1. **Prime the backlog once.** On the real DB, coverage holds 11 rows against 123 closed trades, so the first drain is the expensive one. Run it deliberately, outside trading hours, with `journal live` stopped:

   ```bash
   uv run journal candles
   ```

   Expect it to take minutes on that first run and report a large `windows_fetched`. Every run after it should report `0 window(s) fetched` for trades that have not changed.

2. **Restart `journal live`.** It is a long-running process and will not pick up the new code otherwise. Two earlier fixes (`ae82991`, `b96e220`) are waiting on the same restart.

3. **Confirm the fix live.** Close a position and watch the `journal live` heartbeat. Before this change the per-cycle line went quiet for minutes after `closed [...] — menjalankan ingest`; it should now resume within seconds, and `/chart`'s live bar should keep moving through the ingest.

4. Then: `superpowers:requesting-code-review` on the branch, fix wave, `superpowers:verification-before-completion`, `superpowers:finishing-a-development-branch`.

## Self-review notes

- **Spec coverage.** Spec §1 (delegate to `fill_range`) → Task 1. §2 (ordering + `_MAX_FETCH_WINDOWS`) → Task 2. §3 (report fields + CLI lines) → Tasks 1–3. Testing section → Task 1 Step 2, Task 2 Steps 1–2. Nothing in the spec is unimplemented.
- **One deviation from the spec, deliberate.** The spec did not mention a `max_windows` parameter. Without it the cap applies to `journal candles` too, and priming the existing 118-window backlog would need ~24 separate invocations. The parameter keeps a single fetch code path (what the spec's "no CLI-vs-live split" decision was protecting) while letting the foreground command drain in one run. Spec should be amended to record this.
- **Spec test 1 amended.** The spec's "second run makes zero bridge calls" assumed the existing `_write_rates` fixture. `FakeMT5Client` ignores the requested date range, and that fixture's bars stop short of the window end, so the tail never seals and the second run would fetch once. `_write_rates_multi` writes bars past `to_msc` to make the window sealable. The reason is documented in the helper's docstring.
