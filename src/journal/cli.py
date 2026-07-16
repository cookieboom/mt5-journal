"""`journal` CLI. M0 ships one command: `doctor`, which proves the adapter can
reach the bridge and reports the account facts everything else depends on.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import typer

app = typer.Typer(help="mt5-journal — automated trading journal.")


@app.callback()
def _main() -> None:
    """Keep `doctor` a named subcommand (`journal doctor`), not the root — Typer
    otherwise collapses a single-command app into the bare `journal` invocation.
    Future milestones add `sync`, `rebuild`, `chart`."""


_MARGIN_MODE = {0: "NETTING", 1: "EXCHANGE", 2: "HEDGING"}

_XAU = "XAUUSDc"  # this account's gold symbol (broker `c` suffix)


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


if __name__ == "__main__":  # pragma: no cover
    app()
