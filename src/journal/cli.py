"""`journal` CLI. M0 shipped `doctor`; M1 adds `sync` (ingest deals/orders →
`_raw`), `verify` (the read-only balance invariant), and `reconcile` (name a
residual — never swallow it in a tolerance).
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import typer

from .store.db import connect

app = typer.Typer(help="mt5-journal — automated trading journal.")


@app.callback()
def _main() -> None:
    """Keep `doctor` a named subcommand (`journal doctor`), not the root — Typer
    otherwise collapses a single-command app into the bare `journal` invocation.
    Future milestones add `rebuild`, `chart`."""


_MARGIN_MODE = {0: "NETTING", 1: "EXCHANGE", 2: "HEDGING"}

_XAU = "XAUUSDc"  # this account's gold symbol (broker `c` suffix)

# Local-only, single user. Never committed (CLAUDE.md rule 10). Override with --db.
_DEFAULT_DB = "data/journal.db"


@app.command()
def doctor() -> None:
    """Verify the adapter: account info, symbols, a live tick, and history shape.

    Needs the siliconmetatrader5 bridge up on localhost:8001.
    """
    # Imported here (not at module top) so the CLI module stays importable — and
    # unit-testable — without the bridge installed. Only `doctor` needs it.
    from .adapter.live import LiveMT5Client

    client = LiveMT5Client()

    # ---- account -------------------------------------------------------
    acct = client.account_info()
    if acct is None:
        typer.echo("account_info() returned None — bridge up but not logged in?")
        raise typer.Exit(code=1)

    mode = _MARGIN_MODE.get(acct.margin_mode, f"UNKNOWN({acct.margin_mode})")
    ccy = acct.currency
    typer.echo("== account ==")
    typer.echo(f"login:       {acct.login}")
    typer.echo(f"currency:    {ccy}")
    # Every money value carries the currency code, never '$' (trap 13).
    typer.echo(f"balance:     {acct.balance} {ccy}")
    typer.echo(f"margin_mode: {acct.margin_mode}  ({mode})")
    typer.echo(f"leverage:    {acct.leverage}")

    # ---- symbols -------------------------------------------------------
    xau_symbols = [s.name for s in client.symbols_get() if s.name and "XAU" in s.name]
    typer.echo("\n== symbols (containing XAU) ==")
    typer.echo(", ".join(xau_symbols) if xau_symbols else "(none)")

    # ---- XAUUSDc tick + specs -----------------------------------------
    typer.echo(f"\n== {_XAU} ==")
    tick = client.symbol_info_tick(_XAU)
    info = client.symbol_info(_XAU)

    if tick is None:
        typer.echo("symbol_info_tick returned None — not in Market Watch / market closed?")
    else:
        true_now = time.time()
        server_epoch = tick.time
        # Offset and tick-age are mutually circular: tick-age is only meaningful
        # once the offset is known, and the offset is only trustworthy on a fresh
        # tick. We break the circle empirically — this account's offset was
        # confirmed = 0 against a 0-second-old tick, so on a fresh tick both the
        # snapped offset (below) and the ABSOLUTE age read correctly. If you're
        # re-reading this in three months wondering why age isn't offset-adjusted:
        # that's why, and it stays honest only while the offset really is ~0.
        offset_s = round((server_epoch - true_now) / 900) * 900  # snap to 15 min
        tick_age = abs(true_now - server_epoch)

        typer.echo(f"bid:               {tick.bid}")
        typer.echo(f"server_utc_offset: {offset_s} s  (measured, not assumed)")
        typer.echo(f"tick_age:          {tick_age:.1f} s")

    if info is None:
        typer.echo("symbol_info returned None — cannot read specs.")
    else:
        typer.echo(f"tick_size:         {info.trade_tick_size}")
        # trap 14: tick_value's unit is the ACCOUNT currency, never currency_profit.
        typer.echo(f"tick_value:        {info.trade_tick_value} {ccy}"
                   f"   (<- always in account currency)")
        typer.echo(f"contract_size:     {info.trade_contract_size}")
        typer.echo(f"currency_profit:   {info.currency_profit}"
                   f"   (<- symbol quote currency, NOT the unit of tick_value)")

    # ---- history shape -------------------------------------------------
    date_from = datetime(2000, 1, 1, tzinfo=timezone.utc)
    date_to = datetime.now(timezone.utc)
    deals = client.history_deals_get(date_from, date_to)
    entries = sorted({d.entry for d in deals if d.entry is not None})
    typer.echo("\n== history ==")
    typer.echo(f"deal count:        {len(deals)}")
    typer.echo(f"distinct entry:    {entries}")

    # ---- warning -------------------------------------------------------
    if tick is not None and tick_age > 300:
        typer.echo(
            f"\nWARNING: tick is {tick_age:.0f}s old (> 300s). The measured "
            f"server_utc_offset of {offset_s}s is NOT trustworthy — re-run "
            f"during an active market session before believing it."
        )


# ------------------------------------------------------------------- sync


@app.command()
def sync(db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path.")) -> None:
    """Pull deals/orders/specs from the live bridge into the `_raw` tables.

    Append-only and idempotent — re-running only captures what is new. This is the
    archive the broker's own history is not (Trap 16): every unsynced day is a day
    something can vanish for good.

    Pulls the FULL history from 2000, unlike the windowed sync inside
    `journal live`: this is the manual, deliberate command, and a full pull is the
    only way the archive detector can speak about the whole journal. It is also the
    slow one (minutes on this bridge) — that is the trade.
    """
    from .adapter.live import LiveMT5Client
    from .ingest.deals import sync as run_sync

    client = LiveMT5Client()
    conn = connect(db)
    try:
        r = run_sync(client, conn, full=True)
    finally:
        conn.close()

    typer.echo("== sync ==")
    typer.echo(f"account:      {r.account_login}")
    typer.echo(
        f"deals:        {r.deals_new} new, {r.deals_existing} already had "
        f"({r.deals_seen} seen)"
    )
    typer.echo(
        f"orders:       {r.orders_new} new, {r.orders_existing} already had "
        f"({r.orders_seen} seen)"
    )
    typer.echo(f"symbol_specs: {', '.join(r.symbols_specced) or '(none)'}")
    if r.offset_measured:
        typer.echo(f"utc_offset:   {r.server_utc_offset_s} s  (measured this sync)")
    else:
        typer.echo("utc_offset:   (not measured — no fresh tick)")
    typer.echo(f"watermark:    deals={r.deals_watermark_msc} orders={r.orders_watermark_msc}")

    # Archive detector (Trap 16): tickets we still hold that the broker stopped
    # returning. NOT an error — it is the whole point of the project, and the only
    # surviving copy of those deals. Print it loud and positive.
    if r.archived_tickets:
        n = len(r.archived_tickets)
        typer.echo(
            f"\n!! ARCHIVE DETECTED: the broker no longer returns {n} deal(s) this "
            f"journal still holds.\n"
            f"   This is not an error — it is the journal doing its job. deals_raw is "
            f"now the ONLY surviving copy.\n"
            f"   tickets: {r.archived_tickets}"
        )
    else:
        typer.echo("archived:     none (broker still returns every deal we hold)")


# ----------------------------------------------------------------- verify


@app.command()
def verify(db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path.")) -> None:
    """Check the balance invariant (§6). Pure SQL, READ-ONLY — writes nothing, and
    needs NO bridge: it runs against the balance snapshot `sync` stored, so it works
    on a backup, in CI, or with the broker down (Trap 16 — this journal is the
    durable record, not MT5).

        sum(deal cash) - sum(reconciliations) - balance == 0   (within 0.01)

    On failure it prints the residual and the exact `reconcile add` command to name
    it. It never auto-records anything: a side effect inside a command called
    `verify` is the trap.
    """
    from .ingest.deals import verify as run_verify

    conn = connect(db)
    try:
        ccy_row = conn.execute("SELECT currency FROM accounts LIMIT 1").fetchone()
        ccy = (ccy_row[0] if ccy_row else "") or ""
        v = run_verify(conn)
    finally:
        conn.close()

    # Identity 1 — ingest integrity.
    typer.echo("== verify: identity 1 (ingest — deals vs balance) ==")
    typer.echo(f"sum(deal cash):   {v.deals_cash:.2f} {ccy}")
    typer.echo(f"reconciliations:  {v.reconciled:.2f} {ccy}")
    typer.echo(f"balance:          {v.balance:.2f} {ccy}")
    typer.echo(f"residual:         {v.residual:+.2f} {ccy}   "
               f"[{'PASS' if v.passed1 else 'FAIL'}]")

    # Identity 2 — reconstruction partition. Printed separately so a failure here vs
    # identity 1 tells you it is `reconstruct.py`, not ingest.
    typer.echo("\n== verify: identity 2 (reconstruct — trades partition the deals) ==")
    typer.echo(f"sum(trades.net):  {v.trades_net:.2f} {ccy}   "
               f"({v.trades_count} trades)")
    typer.echo(f"non-trade cash:   {v.nontrade_cash:.2f} {ccy}")
    typer.echo(f"reconciliations:  {v.reconciled:.2f} {ccy}")
    typer.echo(f"balance:          {v.balance:.2f} {ccy}")
    if v.id2_state == "not_run":
        typer.echo("residual:         N/A — no trades and no trade deals yet [NOT RUN]")
    elif v.id2_state == "fail" and v.residual2 is None:
        typer.echo(
            f"residual:         N/A [FAIL]\n\n"
            f"!! {v.trade_deals_count} trade deal(s) in deals_raw but 0 trades — "
            f"reconstruction produced nothing.\n"
            f"   Run `journal rebuild`. If it still shows 0 trades, that is a "
            f"reconstruct.py bug, not ingest."
        )
    else:
        typer.echo(f"residual:         {v.residual2:+.2f} {ccy}   "
                   f"[{'PASS' if v.id2_state == 'ok' else 'FAIL'}]")

    if v.passed:
        typer.echo("\nPASS — deals reconstruct to balance (both identities).")
        return

    if not v.passed1:
        typer.echo(
            f"\nFAIL (identity 1) — residual {v.residual:+.2f} {ccy} is unexplained. Do "
            f"NOT widen a tolerance. Record it as a named reconciliation:\n\n"
            f'  journal reconcile add --amount {v.residual:.2f} '
            f'--effective "YYYY-MM-DD HH:MM:SS" '
            f'--reason "why this gap exists" --evidence "deal ticket / report figures"\n'
        )
    elif v.id2_state == "fail" and v.residual2 is not None:
        # Only a genuine partition MISMATCH gets this explanation. The empty-trades /
        # no-rebuild case (residual2 is None) already told the user to run rebuild in
        # the identity-2 block above — repeating a "miscounted partition" cause here
        # would contradict it.
        typer.echo(
            "\nFAIL (identity 2) — identity 1 holds but the trades do not partition the "
            "deals. The bug is in reconstruct.py: a position_id skipped, a partial close "
            "miscounted, or a cost component dropped (Trap 9)."
        )
    raise typer.Exit(code=1)


# ---------------------------------------------------------------- rebuild


@app.command()
def rebuild(db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path.")) -> None:
    """Rebuild `trades` from the append-only `_raw` tables (M2, extended by M4/M5).

    DELETEs every trade for the account and re-INSERTs from `deals_raw`/`orders_raw` —
    never UPDATE. `trades` is fully derived, so this is always safe to re-run. Needs
    no bridge; it reads the store `sync`/`poll`/`candles` already populated. Run
    `journal verify` afterwards to check the reconstruction partitions the deals
    (§6 identity 2).

    MAE/MFE (M5) needs `candles`, which `journal candles` only fetches for trades
    that already exist in `trades` — so on a fresh account the order is
    `sync -> rebuild -> candles -> rebuild` (rebuild TWICE): the first run has
    nothing to compute excursion from yet, `candles` then fetches each closed
    trade's window, and a second `rebuild` picks the new coverage up. Safe either
    way — rebuild is idempotent.
    """
    from .domain.reconstruct import rebuild as run_rebuild

    conn = connect(db)
    try:
        r = run_rebuild(conn)
    finally:
        conn.close()

    typer.echo("== rebuild ==")
    typer.echo(f"account:      {r.account_login}")
    typer.echo(f"trades:       {r.n_trades}")
    typer.echo(
        f"  by status:  {r.n_closed} closed, {r.n_open} open, "
        f"{r.n_partial} partially_open"
    )
    # sl_initial is recoverable from orders_raw for only a minority of trades on this
    # account (docs §7) — most SLs were set after entry and are lost until the M4
    # poller. R can only be computed where sl_initial is known, hence the two counts.
    typer.echo(f"  sl_initial: {r.n_with_sl} known, {r.n_trades - r.n_with_sl} unknown")
    typer.echo(f"  r_multiple: {r.n_with_r} computable")
    # mae/mfe need candles (M3) to exist for a trade's window -- less than
    # n_closed just means `journal candles` hasn't (re-)run for every trade
    # yet. Not an error; only worth the hint when there's still a gap.
    mae_line = f"  mae/mfe:    {r.n_with_mae} computable"
    if r.n_with_mae < r.n_closed:
        mae_line += " (run `journal candles` for the rest)"
    typer.echo(mae_line)
    typer.echo("\nNext: `journal verify` — check identity 2 (trades partition the deals).")


# ------------------------------------------------------------------ migrate


@app.command()
def migrate(db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path.")) -> None:
    """Bring an existing database's schema up to date (M9). Pure DB, no bridge.

    Every other command already calls this via `connect()`, so you rarely need to
    run it by hand — it exists so a schema upgrade can be done, and SEEN, as a
    deliberate step rather than as a side effect of the next command.

    Idempotent: running it twice reports nothing to do. Migrations are additive
    only — no table is dropped and no row is rewritten.
    """
    from .store.db import SCHEMA_VERSION, current_version

    conn = connect(db)   # connect() itself applies anything pending
    try:
        version = current_version(conn)
    finally:
        conn.close()

    typer.echo("== migrate ==")
    typer.echo(f"db:      {db}")
    typer.echo(f"version: {version} (latest is {SCHEMA_VERSION})")
    if version == SCHEMA_VERSION:
        typer.echo("status:  up to date — nothing to apply.")
    else:
        # Unreachable in normal operation; a loud line beats a silent wrong state.
        typer.echo(
            f"status:  STILL BEHIND after migrating — expected {SCHEMA_VERSION}. "
            f"Do not run other commands against this DB; investigate first."
        )
        raise typer.Exit(code=1)


# ------------------------------------------------------------------- backup


@app.command()
def backup(
    dest: str = typer.Option(
        None, help="Write here instead of the auto-named snapshot. Never pruned."
    ),
    keep: int = typer.Option(
        7, help="Auto-named snapshots to keep, oldest deleted first. 0 keeps all."
    ),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """Snapshot the database to `<db dir>/backups/journal-<UTC>.db`. No bridge.

    Trap 16 — the broker deletes its own history — is why this journal exists,
    and it is also why this file is the ONLY copy of most of what is in it. A
    lost `journal.db` cannot be re-synced; the deals are gone from the server.

    Safe to run while `journal live` and `journal serve` are up. It uses
    SQLite's online backup API, which copies through the pager (so committed
    data still sitting in the `-wal` comes along) and restarts itself if a
    writer commits mid-copy. `cp data/journal.db somewhere` is NOT the same
    thing: it can hand you a file whose newest commits live only in the WAL it
    did not copy. The snapshot it writes is a single self-contained file — no
    `-wal`/`-shm` sidecars to keep with it.

    The copy is opened and `PRAGMA integrity_check`ed before this reports
    success, because a backup nobody has read back is a guess.

    `journal live` runs this same code on a daily timer, into the same folder
    under the same `--keep` — so the file you get by typing this and the file
    the daemon leaves behind are the same kind of thing.
    """
    from .store.backup import BackupError, snapshot

    try:
        s = snapshot(db, dest=dest, keep=keep)
    except BackupError as e:
        typer.echo(f"== backup ==\nsource:    {db}\nERROR:     {e}")
        raise typer.Exit(code=1)

    typer.echo("== backup ==")
    typer.echo(f"source:    {db} ({Path(db).stat().st_size / 1e6:.1f} MB)")
    typer.echo(f"snapshot:  {s.out} ({s.out.stat().st_size / 1e6:.1f} MB)")
    typer.echo(f"integrity: {s.integrity}")
    typer.echo(f"contents:  {s.n_deals} raw deals, {s.n_trades} trades")
    for p in s.pruned:
        typer.echo(f"pruned:    {p.name}")
    if s.integrity != "ok":
        typer.echo("\nThe SNAPSHOT is corrupt — do not delete anything, and check the source.")
        raise typer.Exit(code=1)


# ------------------------------------------------------------------ candles


@app.command()
def candles(db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path.")) -> None:
    """Fetch OHLC bars for every closed trade's chart window into `candles` (M3).

    Needs the live bridge (client-bearing, like `sync`). Run this after
    `journal rebuild` and before `journal chart`.

    Idempotent and cheap to re-run: `candle_coverage` is consulted first, so a
    window already stored is not re-fetched at all. Runs uncapped — unlike the
    same pipeline inside `journal live`, which limits itself to a few windows per
    position close so the forming bar keeps streaming.
    """
    from .adapter.live import LiveMT5Client
    from .ingest.candles import sync_candles

    client = LiveMT5Client()
    conn = connect(db)
    try:
        # No cap here: this is a deliberate foreground command a human is
        # watching, and it is how a large backlog gets primed in one run. The
        # cap exists to protect `journal live`'s serial cycle, which this is not.
        r = sync_candles(client, conn, max_windows=None)
    finally:
        conn.close()

    typer.echo("== candles ==")
    typer.echo(f"account:        {r.account_login}")
    typer.echo(
        f"trades:         {r.trades_seen} closed windowed, "
        f"{r.trades_skipped_open} open/partial skipped (no close yet)"
    )
    typer.echo(
        f"bars:           {r.bars_new} new from {r.windows_fetched} window(s) fetched"
    )
    typer.echo(f"pending:        {r.windows_pending} window(s) left for the next run")
    typer.echo(f"symbols:        {', '.join(r.symbols) or '(none)'}")


@app.command("candles-warm")
def candles_warm(
    symbol: str = typer.Argument(..., help="Exact MT5 symbol, e.g. XAUUSDc."),
    timeframe: str = typer.Argument(..., help="One of M1,M5,M15,H1,H4,D1."),
    from_ms: int = typer.Option(..., "--from", help="Range start, epoch ms (server time)."),
    to_ms: int = typer.Option(..., "--to", help="Range end, epoch ms (server time)."),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """Eagerly fill a candle range from the bridge into the store (pre-warm before
    an offline session). Needs the live bridge. Idempotent."""
    from .adapter.live import LiveMT5Client
    from .ingest.candle_fill import fill_range
    from .store.db import now_ms

    client = LiveMT5Client()
    conn = connect(db)
    try:
        n = fill_range(client, conn, symbol, timeframe, from_ms, to_ms, now_ms())
    finally:
        conn.close()
    typer.echo("== candles-warm ==")
    typer.echo(f"{symbol} {timeframe} [{from_ms}, {to_ms}]: {n} new bars")


@app.command("candles-coverage")
def candles_coverage(
    symbol: str = typer.Option(None, help="Filter to one symbol."),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """Print stored candle coverage ranges per (symbol, timeframe). No bridge."""
    from .store import candles_store as cs

    conn = connect(db)
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol, timeframe FROM candle_coverage "
            + ("WHERE symbol = ? " if symbol else "")
            + "ORDER BY symbol, timeframe",
            (symbol,) if symbol else (),
        ).fetchall()
        typer.echo("== candles-coverage ==")
        if not rows:
            typer.echo("(none)")
        for r in rows:
            ranges = cs.read_coverage(conn, r["symbol"], r["timeframe"])
            spans = ", ".join(f"[{a}, {b}]" for a, b in ranges)
            typer.echo(f"{r['symbol']:10} {r['timeframe']:4} {spans}")
    finally:
        conn.close()


# --------------------------------------------------------------------- chart


@app.command()
def chart(
    position_id: int = typer.Argument(
        ..., help="trades.position_id — the STABLE key (survives rebuild)."
    ),
    tf: str = typer.Option(None, help="Override the duration-based timeframe pick."),
    cache_dir: str = typer.Option("cache", help="Directory PNGs are written to."),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """Render one trade to a PNG in `cache/` (M3). Pure DB, no bridge needed —
    reads `trades` + `candles` (run `journal candles` first if the window is
    empty).

    Takes `position_id`, never `trades.id`: `trades.id` is AUTOINCREMENT and
    renumbers on every `rebuild` (docs/mt5-deal-model.md §5), so a saved command
    built on it could silently chart the WRONG trade after a rebuild. The cache
    filename is keyed the same way, so charts survive rebuilds too.
    """
    from .render.chart import NoCandlesError, RenderOpts, TradeNotFoundError, render_trade

    conn = connect(db)
    try:
        try:
            r = render_trade(
                conn, position_id, opts=RenderOpts(tf_override=tf), cache_dir=cache_dir
            )
        except (TradeNotFoundError, NoCandlesError) as e:
            typer.echo(str(e))
            raise typer.Exit(code=1)
    finally:
        conn.close()

    typer.echo("== chart ==")
    typer.echo(f"path:           {r.path}")
    typer.echo(f"timeframe:      {r.timeframe}")
    typer.echo(f"bars:           {r.n_bars} total ({r.n_trade_bars} span the trade)")
    if r.same_bar:
        typer.echo("note:           entry & exit fall within a single bar (sub-bar trade)")
    typer.echo(f"sl drawn:       {r.sl_drawn}   tp drawn: {r.tp_drawn}")


# ---------------------------------------------------------------------- poll


@app.command()
def poll(
    interval: float = typer.Option(5.0, help="Seconds between snapshot cycles."),
    once: bool = typer.Option(
        False, help="Run a single cycle and exit (cron-friendly / smoke test)."
    ),
    duration: float = typer.Option(
        None, help="Stop after this many seconds (default: run until Ctrl+C)."
    ),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """Snapshot live open positions' SL/TP into `sl_tp_snapshots` (M4).

    Needs the live bridge (client-bearing, like `sync`/`candles`) and an account
    already known to the store (`journal sync` first). `positions_get()` only
    returns CURRENTLY OPEN positions — this recovers `sl_initial` for the 62/68
    discretionary trades going forward only; it cannot retroactively help a
    trade that already closed (docs/mt5-deal-model.md Trap 6). Change-only
    logging: a row is written only when SL/TP/volume actually changes, so an
    idle account writes nothing. Ctrl+C stops cleanly.
    """
    from .adapter.live import LiveMT5Client
    from .ingest.poller import poll_loop

    def _echo_cycle(r) -> None:
        # `journal poll` (no --once) is a FOREGROUND command a human watches;
        # log.info alone is invisible with no handler configured, so without
        # this the process would look hung until Ctrl+C even while working.
        # Change-only philosophy carries over here too: stay silent on an idle
        # cycle, print only when something real happened.
        if r.snapshots_written:
            when = datetime.fromtimestamp(r.observed_msc / 1000, tz=timezone.utc)
            typer.echo(
                f"  [{when:%H:%M:%S} UTC] {r.snapshots_written} new snapshot(s), "
                f"{r.positions_seen} open position(s)"
            )

    conn = connect(db)
    try:
        login = _one_account_login(conn)  # friendly exit if `sync` never ran
        client = LiveMT5Client()
        typer.echo(
            f"polling every {interval}s"
            + ("" if once else " — Ctrl+C to stop" + (f", max {duration}s" if duration else ""))
        )
        r = poll_loop(
            client, conn, login, interval=interval, once=once, duration=duration,
            on_cycle=_echo_cycle,
        )
    finally:
        conn.close()

    typer.echo("== poll ==")
    typer.echo(f"cycles:         {r.cycles}")
    typer.echo(f"new snapshots:  {r.snapshots_written}")
    typer.echo(f"stopped by:     {r.stopped_by}")


# ---------------------------------------------------------------------- live


@app.command()
def live(
    interval: float = typer.Option(
        5.0, help="Idle seconds between cycles (drops to 1s while a command is queued)."
    ),
    no_trading: bool = typer.Option(
        False, "--no-trading", help="Ingest only — do NOT execute queued trade commands."
    ),
    once: bool = typer.Option(
        False, help="Run a single cycle and exit (cron-friendly / smoke test)."
    ),
    duration: float = typer.Option(
        None, help="Stop after this many seconds (default: run until Ctrl+C)."
    ),
    no_auto_backup: bool = typer.Option(
        False, "--no-auto-backup", help="Do NOT snapshot the DB once a day while running."
    ),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """The one process that owns the bridge (M9): mirror open positions, auto-
    ingest a trade when its position closes, and execute queued trade commands.

    Needs the live bridge and an account already known to the store (`journal
    sync` first). Trading is ON BY DEFAULT — this loop WILL send real orders that
    the web has queued; pass `--no-trading` for a pure-ingest run. A queued
    command that may have reached the broker is NEVER auto-retried; the next
    startup marks it failed and tells you to check MT5 by hand. Ctrl+C stops
    cleanly.

    Because this is the only thing here that runs all day, it also takes the
    `journal backup` snapshot once every 24 h (7 kept, skipped while a trade
    command is pending, never fatal) — `--no-auto-backup` turns that off.
    """
    import logging

    from .adapter.live import LiveMT5Client
    from .ingest.live import live_loop

    # The package logs but nothing ever configured a handler, so every `log.info`
    # in the ingest path went to /dev/null — the last ingest freeze had to be
    # reconstructed from row timestamps after the fact. This is the only long-lived
    # process, so it is the one that turns them on. Root logger, INFO, stderr.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    trading = not no_trading

    def _echo_cycle(r) -> None:
        # `journal live` is a foreground command a human WATCHES — unlike the M4
        # poller it must show a heartbeat every cycle, not stay silent on idle.
        # A silent terminal here reads as "hung / nothing happening" even while
        # the loop is correctly mirroring positions (the exact confusion reported
        # the first time this ran live). So print one line per cycle: at minimum
        # the open-position count, plus anything real that happened.
        when = datetime.fromtimestamp(r.observed_msc / 1000, tz=timezone.utc)
        parts = [f"{r.positions_seen} open"]
        if r.snapshots_written:
            parts.append(f"{r.snapshots_written} SL/TP snapshot(s)")
        if r.closed_ids:
            parts.append(
                f"closed {r.closed_ids}"
                + (" -> ingested" if r.ingest_ran else " (ingest FAILED — see log)")
            )
        if r.command_id is not None:
            parts.append(f"cmd {r.command_id} -> {r.command_status}")
        typer.echo(f"  [{when:%H:%M:%S} UTC] " + " · ".join(parts))

    def _echo_closing(closed_ids) -> None:
        # Fires the moment a close is detected, BEFORE the ingest pipeline blocks
        # the loop on a bridge round-trip (sync + candles) that can take several
        # seconds. Without this the heartbeat just goes quiet and reads as a
        # freeze — which is exactly how it was first misread when run live.
        typer.echo(
            f"  closed {closed_ids} — menjalankan ingest "
            f"(sync → rebuild → candles → rebuild), tunggu beberapa detik…"
        )

    conn = connect(db)
    try:
        login = _one_account_login(conn)  # friendly exit if `sync` never ran
        client = LiveMT5Client()
        mode = "TRADING ON — will send real orders" if trading else "ingest only (--no-trading)"
        typer.echo(
            f"live: {mode}; idle interval {interval}s"
            + ("; auto-backup off" if no_auto_backup else "; daily auto-backup")
            + ("" if once else " — Ctrl+C to stop" + (f", max {duration}s" if duration else ""))
        )
        r = live_loop(
            client, conn, login,
            interval_idle=interval, trading=trading,
            once=once, duration=duration,
            backup_every_s=None if no_auto_backup else 86_400.0,
            on_cycle=_echo_cycle, on_closing=_echo_closing,
        )
    except sqlite3.OperationalError as e:
        # WAL + busy_timeout (store/db.py) makes this rare, but two `journal live`
        # processes on one DB still contend past the timeout. Only ONE live loop
        # may own the bridge (plan §0.4) — say so plainly instead of a traceback.
        if "locked" in str(e).lower():
            typer.echo(
                "live: database TERKUNCI — kemungkinan ada `journal live` lain yang "
                "sedang menulis DB ini. Jalankan HANYA SATU `journal live` sekaligus "
                "(satu proses saja yang boleh memegang bridge). `journal serve` boleh "
                "jalan bersamaan."
            )
            raise typer.Exit(1)
        raise
    finally:
        conn.close()

    typer.echo("== live ==")
    typer.echo(f"cycles:         {r.cycles}")
    typer.echo(f"recovered:      {r.recovered} interrupted command(s) at startup")
    typer.echo(f"trading:        {'on' if trading else 'off (--no-trading)'}")
    typer.echo(f"stopped by:     {r.stopped_by}")


# -------------------------------------------------------------------- report


def _fmt(x: float | None, ccy: str = "", *, sign: bool = False) -> str:
    """One place money/ratio numbers turn into text. `None` always reads
    "n/a" — never 0, never blank — so a missing value can't be misread as a
    real zero (CLAUDE.md rule 4)."""
    if x is None:
        return "n/a"
    s = f"{x:+.2f}" if sign else f"{x:.2f}"
    return f"{s} {ccy}".strip()


def _gated(n: int, avg: float | None) -> str:
    """A statistic's display, honestly gated by docs §9: n<20 shows the count
    and says why there's no number, never a silently-omitted line."""
    if avg is None:
        return f"n/a (n={n}, need ≥20)"
    return f"{avg:.2f}  (n={n})"


def _bucket_line(stat, ccy: str) -> str:
    """One breakdown row (a session, or EA/discretionary). The bucket's `n` leads
    the line and is always shown — it's the diagnostic that explains any `n/a`
    that follows: win rate and expectancy are gated to None below §9's n≥20
    (build_report already did that), so here they simply read 'n/a' with the
    count sitting right beside them."""
    wr = "n/a" if stat.win_rate is None else f"{stat.win_rate * 100:.1f}%"
    exp = _fmt(stat.expectancy, ccy, sign=True)
    return (
        f"  {stat.label:<13} n={stat.n:<3}  win {wr:<6}  exp {exp:<12}  "
        f"R {_gated(stat.n_with_r, stat.avg_r)}"
    )


@app.command()
def report(db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path.")) -> None:
    """A first read of this account's performance (M5). Pure DB, no bridge —
    reads `trades` (run `journal rebuild` first; `journal candles` +
    `journal rebuild` again for MAE/MFE, see `journal rebuild --help`).

    Money-based stats (win rate, avg win/loss, profit factor, expectancy)
    have FULL coverage — every closed trade has a net_profit. R-based stats
    do not: sl_initial is recoverable for only 6/68 trades so far (docs §7),
    and MAE/MFE needs candle coverage on top of that. Every number carries
    its n; anything computed over n<20 shows why it's withheld instead of a
    misleadingly precise figure (docs §9).
    """
    from .analytics.report import build_report

    conn = connect(db)
    try:
        r = build_report(conn)
    finally:
        conn.close()

    typer.echo("== report ==")
    typer.echo(f"account:        {r.account_login}  ({r.currency})")
    typer.echo(f"trades:         {r.n_total} total, {r.n_closed} closed")
    typer.echo(
        f"  outcomes:     {r.n_wins} win, {r.n_losses} loss, "
        f"{r.n_breakeven} breakeven"
    )
    typer.echo()
    typer.echo(f"-- money (full coverage, n={r.n_closed}) --")
    win_rate_text = "n/a" if r.win_rate is None else f"{r.win_rate * 100:.1f}%"
    typer.echo(f"  win rate:     {win_rate_text}")
    typer.echo(f"  avg win:      {_fmt(r.avg_win, r.currency)}")
    typer.echo(f"  avg loss:     {_fmt(r.avg_loss, r.currency, sign=True)}")
    typer.echo(f"  profit factor: {_fmt(r.profit_factor)}")
    typer.echo(f"  expectancy:   {_fmt(r.expectancy, r.currency, sign=True)}")
    typer.echo()
    typer.echo("-- R-multiple (§9: needs n≥20) --")
    typer.echo(f"  avg R:        {_gated(r.n_with_r, r.avg_r)}")
    typer.echo()
    typer.echo("-- MAE/MFE (§9: needs n≥20; needs candle coverage AND known SL) --")
    typer.echo(f"  candle coverage: {r.n_with_mae}/{r.n_closed} closed trades")
    typer.echo(f"  avg MAE (R):  {_gated(r.n_with_mae_r, r.avg_mae_r)}")
    typer.echo(f"  avg MFE (R):  {_gated(r.n_with_mfe_r, r.avg_mfe_r)}")
    typer.echo()
    typer.echo("-- by session (UTC; §9: win/exp/R gated per bucket at n≥20) --")
    for b in r.by_session:
        typer.echo(_bucket_line(b, r.currency))
    typer.echo()
    typer.echo("-- by source (EA = magic≠0, docs §7; same per-bucket gating) --")
    for b in r.by_source:
        typer.echo(_bucket_line(b, r.currency))
    typer.echo()
    typer.echo("-- by symbol (grouped by symbol_base, rule 11; same per-bucket gating) --")
    for b in r.by_symbol:
        typer.echo(_bucket_line(b, r.currency))
    if r.n_with_r < 20 or r.n_with_mae_r < 20:
        typer.echo(
            "\nR-based sections are thin by design, not by bug: sl_initial only "
            "goes forward from `journal poll` (M4) plus 6 historical EA trades "
            "(docs §7) — they grow slowly as the poller covers new trades. The "
            "session/source win & expectancy figures are gated the same way: a "
            "bucket under 20 trades shows n/a, not a number pretending to be one."
        )


# -------------------------------------------------------------------- weekly


def _parse_iso_week(s: str) -> tuple[int, int]:
    """`YYYY-Www` (e.g. `2026-W28`) → (iso_year, iso_week). Validated via
    strptime's ISO directives so a bad week/year is rejected cleanly, the way
    `_parse_effective` guards `reconcile add`'s timestamp."""
    try:
        dt = datetime.strptime(f"{s}-1", "%G-W%V-%u")  # -1 = Monday of that ISO week
    except ValueError as e:
        typer.echo(f"--week must be ISO 'YYYY-Www' (e.g. 2026-W28): {e}")
        raise typer.Exit(code=1)
    y, w, _ = dt.isocalendar()
    return y, w


@app.command()
def weekly(
    week: str = typer.Option(
        None, help="ISO week 'YYYY-Www' (default: the last COMPLETE week)."
    ),
    cache_dir: str = typer.Option("cache", help="Directory the .md is written to."),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """Render one ISO week to a Markdown file in `cache/` (M6.1). Pure DB, no
    bridge — reads `trades` + annotations/tags (run `journal rebuild` first).

    A trade is attributed to the week it CLOSED in (realized P&L), over
    Mon–Sun UTC. Weekly rates/averages are §9-gated (a week rarely clears n≥20),
    but the raw counts, the realized net total, and the trades you annotated or
    tagged are always shown — that is what a weekly review is for. The file is
    reproducible from the DB (rule 6)."""
    from pathlib import Path

    from .analytics.weekly import build_weekly, last_complete_iso_week
    from .render.weekly import render_weekly_md

    iso_year, iso_week = _parse_iso_week(week) if week else last_complete_iso_week()

    conn = connect(db)
    try:
        result = build_weekly(conn, iso_year, iso_week)
    finally:
        conn.close()

    md = render_weekly_md(result)
    out_dir = Path(cache_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"weekly-{iso_year}-W{iso_week:02d}.md"
    path.write_text(md)

    typer.echo("== weekly ==")
    typer.echo(f"week:           {iso_year}-W{iso_week:02d}")
    typer.echo(f"path:           {path}")
    typer.echo(f"trades closed:  {result.n_closed}")
    typer.echo(f"realized:       {_fmt(result.net_total, result.currency, sign=True)}")
    typer.echo(f"annotated:      {len(result.notes)}")


# --------------------------------------------------------------------- serve


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_loopback(host: str) -> bool:
    """True only for a loopback bind address. `journal serve` refuses anything else
    because M9 exposes order-entry routes BY DEFAULT (no `--trading` flag, human
    decision 2026-07-23): binding to a LAN address would be an unauthenticated
    order-entry endpoint. Case-insensitive; surrounding brackets on '[::1]' are
    stripped so the IPv6 loopback matches whether or not it is bracketed."""
    return host.strip().strip("[]").lower() in _LOOPBACK_HOSTS


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address (localhost only by default)."),
    port: int = typer.Option(8000, help="Port to listen on."),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
    reload: bool = typer.Option(
        False,
        "--reload",
        help="Dev auto-reload on source edits. Off by default — without it, "
        "code/template changes need a manual restart to take effect. Watches .py "
        "always; also .html only if `watchfiles` is installed (uvicorn[standard]).",
    ),
) -> None:
    """Serve the web dashboard on localhost (M7). Pure DB, no bridge — a
    read-mostly HTML view over `journal report`/`weekly`/`chart` plus annotation
    and manual-tag writes. Bridge operations (`sync`, `candles`, `poll`,
    `rebuild`) stay in the CLI.

    Uvicorn imports the app factory by string, so the DB path is passed through
    the `JOURNAL_DB` env var (mirrors how `create_app` resolves it).

    `--reload` is a dev convenience: uvicorn watches the source tree and restarts
    on edits. Its fallback reloader only watches .py; template-only edits reload
    too, but ONLY if the optional `watchfiles` package is present (bundled with
    `uvicorn[standard]`) — otherwise .html watching silently no-ops, so we don't
    claim it. A .py edit still restarts the worker, which re-reads templates.
    Leave --reload OFF for normal use.
    """
    # M9: order-entry routes are exposed by default (no --trading flag). Binding
    # anywhere but loopback would put an unauthenticated order-entry endpoint on
    # the network, so refuse it outright — before uvicorn ever opens the socket.
    if not _is_loopback(host):
        typer.echo(
            f"Menolak bind ke {host!r}: sejak M9 halaman web mengekspos entri "
            f"order (tutup/ubah/tambah posisi) SECARA DEFAULT. Membuka itu ke "
            f"jaringan = endpoint order tanpa autentikasi. Hanya loopback "
            f"diizinkan: {', '.join(sorted(_LOOPBACK_HOSTS))}.",
            err=True,
        )
        raise typer.Exit(1)

    # Lazy import (like LiveMT5Client): keeps the CLI importable without the web
    # stack installed, and off the hot path of every other command.
    import importlib.util

    import uvicorn

    from .web.app import stale_dist_reason

    # watchfiles is what makes uvicorn's --reload-include actually fire; without
    # it the flag is a no-op that only prints a warning. Detect it so the .html
    # promise is honest and we never emit that warning (no new hard dependency —
    # rule 8: .py reload works regardless).
    has_watchfiles = importlib.util.find_spec("watchfiles") is not None

    os.environ["JOURNAL_DB"] = db
    typer.echo(f"mt5-journal web dashboard → http://{host}:{port}  (db={db})")
    if reload:
        watched = ".py/.html" if has_watchfiles else ".py only (install `uvicorn[standard]` for .html)"
        typer.echo(f"reload: ON — watching {watched} (dev only).")
    # The SPA is served from disk and never built here: say so BEFORE uvicorn
    # takes the terminal, or the human debugs Python for a bundle that predates
    # it. Warning only — an old bundle is still a working dashboard.
    stale = stale_dist_reason()
    if stale:
        typer.echo(f"WARNING: {stale} — run `npm --prefix frontend run build` "
                   f"and reload the page.", err=True)
    typer.echo("Ctrl+C to stop.")
    # Only pass reload_includes when watchfiles can honour it — otherwise uvicorn
    # warns "no effect unless watchfiles is installed".
    extra = {"reload_includes": ["*.py", "*.html"]} if (reload and has_watchfiles) else {}
    uvicorn.run(
        "journal.web.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        **extra,
    )


# -------------------------------------------------------------- reconcile

reconcile_app = typer.Typer(help="Name balance-invariant residuals (never swallow them).")
app.add_typer(reconcile_app, name="reconcile")


def _one_account_login(conn: sqlite3.Connection) -> int:
    """CLI wrapper over the single-source guard in store/db.py — translates its
    RuntimeError into a friendly typer.Exit so the selection logic lives in one place."""
    from .store.db import one_account_login

    try:
        return one_account_login(conn)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(code=1)


def _parse_effective(s: str) -> int:
    """`--effective` is a UTC wall-clock 'YYYY-MM-DD HH:MM:SS' → epoch ms."""
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError as e:
        typer.echo(f"--effective must be 'YYYY-MM-DD HH:MM:SS' (UTC): {e}")
        raise typer.Exit(code=1)
    return int(dt.timestamp() * 1000)


@reconcile_app.command("add")
def reconcile_add(
    amount: float = typer.Option(..., help="Residual to explain, in account currency (signed)."),
    effective: str = typer.Option(..., help="When the gap occurred, UTC 'YYYY-MM-DD HH:MM:SS'."),
    reason: str = typer.Option(..., help="Human explanation. Makes the row 'explained'."),
    evidence: str = typer.Option(None, help="Deal ticket, MT5 report figures, etc."),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """Record one named explanation for a residual. The gap does not disappear — it
    acquires a name and stays visible in `reconcile list`."""
    from .ingest.deals import add_reconciliation

    conn = connect(db)
    try:
        login = _one_account_login(conn)
        rid = add_reconciliation(
            conn, login, amount, _parse_effective(effective), reason, evidence
        )
    finally:
        conn.close()
    typer.echo(f"Recorded reconciliation #{rid}: {amount:+.2f} — {reason}")


@reconcile_app.command("list")
def reconcile_list(db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path.")) -> None:
    """List every reconciliation row."""
    conn = connect(db)
    try:
        ccy_row = conn.execute("SELECT currency FROM accounts LIMIT 1").fetchone()
        ccy = (ccy_row[0] if ccy_row else "") or ""
        rows = conn.execute(
            "SELECT id, amount, effective_msc, status, reason, evidence "
            "FROM reconciliations ORDER BY effective_msc, id"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        typer.echo("(no reconciliations)")
        return
    typer.echo("== reconciliations ==")
    for r in rows:
        eff = (
            datetime.fromtimestamp(r["effective_msc"] / 1000, tz=timezone.utc)
            .strftime("%Y-%m-%d %H:%M:%S")
            if r["effective_msc"] is not None
            else "(no date)"
        )
        typer.echo(
            f"#{r['id']}  {r['amount']:+.2f} {ccy}  {eff} UTC  [{r['status']}]  "
            f"{r['reason']}"
            + (f"  |  {r['evidence']}" if r["evidence"] else "")
        )


# ------------------------------------------------------------- annotate / tags


def _echo_tags(pairs) -> None:
    """Print a trade's tags, grouped source-first (`list_tags` already orders
    them). Auto and manual are labelled so it's clear which the auto pass owns."""
    if not pairs:
        typer.echo("tags:         (none)")
        return
    typer.echo("tags:")
    for tag, source in pairs:
        typer.echo(f"  {tag}  ({source})")


@app.command()
def annotate(
    position_id: int = typer.Argument(
        ..., help="trades.position_id — the STABLE key (survives rebuild)."
    ),
    setup: str = typer.Option(None, help="Setup name, e.g. 'breakout'."),
    confidence: int = typer.Option(None, help="Conviction, an integer 1-5."),
    emotion: str = typer.Option(None, help="How you felt taking the trade."),
    followed_plan: bool = typer.Option(
        None, "--followed-plan/--no-followed-plan",
        help="Whether you followed your plan (omit to leave unrecorded).",
    ),
    notes: str = typer.Option(None, help="Free-form notes."),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """Record the human layer for one trade — setup / confidence / emotion /
    plan / notes (M6). Keyed on `position_id` (never `trades.id`), so it survives
    every `rebuild`. Re-running updates in place. Pure DB, no bridge.
    """
    from .annotate import AnnotateError, set_annotation

    conn = connect(db)
    try:
        _one_account_login(conn)  # friendly exit if `sync` never ran
        try:
            row = set_annotation(
                conn, position_id, setup=setup, confidence=confidence,
                emotion=emotion, followed_plan=followed_plan, notes=notes,
            )
        except AnnotateError as e:
            typer.echo(str(e))
            raise typer.Exit(code=1)
    finally:
        conn.close()

    fp = row["followed_plan"]
    fp_text = "(not recorded)" if fp is None else ("yes" if fp else "no")
    typer.echo("== annotate ==")
    typer.echo(f"position_id:  {position_id}")
    typer.echo(f"setup:        {row['setup'] if row['setup'] is not None else '(none)'}")
    typer.echo(
        f"confidence:   {row['confidence'] if row['confidence'] is not None else '(none)'}"
    )
    typer.echo(f"emotion:      {row['emotion'] if row['emotion'] is not None else '(none)'}")
    typer.echo(f"followed plan: {fp_text}")
    typer.echo(f"notes:        {row['notes'] if row['notes'] is not None else '(none)'}")


tag_app = typer.Typer(help="Manual tags on a trade (source='manual'; auto tags are set by rebuild).")
app.add_typer(tag_app, name="tag")


@tag_app.command("add")
def tag_add(
    position_id: int = typer.Argument(..., help="trades.position_id — the STABLE key."),
    tag: str = typer.Argument(..., help="Tag to attach, e.g. 'revenge-trade'."),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """Attach a manual tag to a trade (idempotent). Manual tags survive `rebuild`."""
    from .annotate import AnnotateError, add_tag

    conn = connect(db)
    try:
        _one_account_login(conn)
        try:
            pairs = add_tag(conn, position_id, tag)
        except AnnotateError as e:
            typer.echo(str(e))
            raise typer.Exit(code=1)
    finally:
        conn.close()
    typer.echo(f"== tag add: {position_id} ==")
    _echo_tags(pairs)


@tag_app.command("rm")
def tag_rm(
    position_id: int = typer.Argument(..., help="trades.position_id — the STABLE key."),
    tag: str = typer.Argument(..., help="Manual tag to remove."),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """Remove a manual tag. Auto tags cannot be removed here — they are owned by
    the `rebuild` auto pass and regenerated on every rebuild."""
    from .annotate import list_tags, remove_tag

    conn = connect(db)
    try:
        _one_account_login(conn)
        n = remove_tag(conn, position_id, tag)
        pairs = list_tags(conn, position_id)
    finally:
        conn.close()
    if n == 0:
        typer.echo(f"no manual tag '{tag}' on position_id {position_id} (nothing removed).")
    typer.echo(f"== tag rm: {position_id} ==")
    _echo_tags(pairs)


@tag_app.command("ls")
def tag_ls(
    position_id: int = typer.Argument(..., help="trades.position_id — the STABLE key."),
    db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path."),
) -> None:
    """List every tag on a trade (auto + manual)."""
    from .annotate import list_tags

    conn = connect(db)
    try:
        _one_account_login(conn)
        pairs = list_tags(conn, position_id)
    finally:
        conn.close()
    typer.echo(f"== tags: {position_id} ==")
    _echo_tags(pairs)


if __name__ == "__main__":  # pragma: no cover
    app()
