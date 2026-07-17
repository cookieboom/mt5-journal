"""`journal` CLI. M0 shipped `doctor`; M1 adds `sync` (ingest deals/orders →
`_raw`), `verify` (the read-only balance invariant), and `reconcile` (name a
residual — never swallow it in a tolerance).
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

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
    """
    from .adapter.live import LiveMT5Client
    from .ingest.deals import sync as run_sync

    client = LiveMT5Client()
    conn = connect(db)
    try:
        r = run_sync(client, conn)
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
    """Rebuild `trades` from the append-only `_raw` tables (M2).

    DELETEs every trade for the account and re-INSERTs from `deals_raw`/`orders_raw` —
    never UPDATE. `trades` is fully derived, so this is always safe to re-run. Needs
    no bridge; it reads the store `sync` already populated. Run `journal verify`
    afterwards to check the reconstruction partitions the deals (§6 identity 2).
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
    typer.echo("\nNext: `journal verify` — check identity 2 (trades partition the deals).")


# ------------------------------------------------------------------ candles


@app.command()
def candles(db: str = typer.Option(_DEFAULT_DB, help="SQLite DB path.")) -> None:
    """Fetch OHLC bars for every closed trade's chart window into `candles` (M3).

    Needs the live bridge (client-bearing, like `sync`). Idempotent: bars already
    stored are skipped (PK-deduped on `symbol, timeframe, time_msc`). Run this
    after `journal rebuild` and before `journal chart`.
    """
    from .adapter.live import LiveMT5Client
    from .ingest.candles import sync_candles

    client = LiveMT5Client()
    conn = connect(db)
    try:
        r = sync_candles(client, conn)
    finally:
        conn.close()

    typer.echo("== candles ==")
    typer.echo(f"account:        {r.account_login}")
    typer.echo(
        f"trades:         {r.trades_seen} closed windowed, "
        f"{r.trades_skipped_open} open/partial skipped (no close yet)"
    )
    typer.echo(f"bars:           {r.bars_new} new, {r.bars_seen - r.bars_new} already had")
    typer.echo(f"symbols:        {', '.join(r.symbols) or '(none)'}")


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
    from .render.chart import NoCandlesError, TradeNotFoundError, render_trade

    conn = connect(db)
    try:
        try:
            r = render_trade(conn, position_id, tf=tf, cache_dir=cache_dir)
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


if __name__ == "__main__":  # pragma: no cover
    app()
