#!/usr/bin/env python
"""Snapshot the live account into tests/fixtures/*.json for the fake adapter.

Records six streams — account, symbols, deals, orders, positions, rates —
dumping each record's full raw `._asdict()` so fixtures survive MT5 adding
fields, then SANITISES the copy that goes into git. Run against the live
bridge; review the resulting diff by hand before committing (CLAUDE.md Rule 10).

This is a dev-only script under scripts/, not part of the importable package, so
it may touch the bridge — and it does so through adapter.LiveMT5Client, which is
the one module allowed to import it (Rule 1). We read `.raw` off each dataclass.

Sanitisation (what lands in git):
    login   -> 0
    server  -> "REDACTED"     company -> "REDACTED"     account name -> "REDACTED"
NEVER touched: ticket, order, position_id  (reconstruction keys on them);
    comment, external_id  (execution metadata, not PII — the "Archived deals"
    marker and [sl]/[tp] tags live here; see Trap 16). Symbol `name` is the
    instrument, not PII, so it survives too. `rates` (candles) carry no PII at
    all — no sanitisation needed.

Usage:
    uv run python scripts/record_fixtures.py [--host localhost] [--port 8001]
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal.adapter.fake import FakeMT5Client
from journal.adapter.live import LiveMT5Client
from journal.domain.reconstruct import SymbolSpec, reconstruct
from journal.domain.symbols import to_base
from journal.render.chart import choose_timeframe, window_for

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
EPOCH_START = datetime(2000, 1, 1)  # full backfill; take everything (Trap 8)


def sanitise(d: dict[str, Any]) -> dict[str, Any]:
    """Generic, key-based scrub applied to every record. Only touches keys that
    are unambiguously identifying wherever they appear. `name` is handled
    separately (symbol name must survive; account holder name must not).

    Deliberately does NOT touch `comment` or `external_id`: those are execution
    metadata, not PII (`[sl 4030.000]`, `[tp 4055.000]`, EA names, and the
    broker's own `"Archived deals"` marker — the literal explanation for the
    14.50 USC gap). The first recording blanked `comment` and destroyed exactly
    that evidence; see docs/mt5-deal-model.md §6 and Trap 16."""
    d = dict(d)
    if "login" in d:
        d["login"] = 0
    if "server" in d:
        d["server"] = "REDACTED"
    if "company" in d:
        d["company"] = "REDACTED"
    return d


def write(name: str, payload: Any) -> None:
    path = FIXTURES / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _num(x: Any) -> float:
    return float(x) if x is not None else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()

    # Read whatever is ALREADY committed on disk, before anything below
    # overwrites it -- this is the frozen anchor the rates section uses so
    # re-running this script never drifts rates.json away from the deals/orders
    # snapshot the M1/M2 test suite hardcodes. Empty on a first-ever run (no
    # fixtures committed yet); the rates section below degrades to writing an
    # empty rates.json in that case, which is fine -- a bootstrap run's job is to
    # seed deals.json first, then a SECOND run adds rates against it.
    frozen = FakeMT5Client(fixtures_dir=FIXTURES)
    frozen_deals = frozen.history_deals_get(None, None)
    frozen_orders = frozen.history_orders_get(None, None)
    frozen_symbols = frozen.symbols_get()
    frozen_specs = {
        s.name: SymbolSpec(
            symbol=s.name, symbol_base=to_base(s.name),
            tick_size=s.trade_tick_size, tick_value=s.trade_tick_value,
            contract_size=s.trade_contract_size,
        )
        for s in frozen_symbols
        if s.name
    }

    client = LiveMT5Client(host=args.host, port=args.port)
    now = datetime.now()

    # --- account ---------------------------------------------------------
    acct = client.account_info()
    if acct is None:
        raise SystemExit("account_info() returned None — is the bridge logged in?")
    acct_raw = sanitise(acct.raw)
    if "name" in acct_raw:  # account holder name is PII; the symbol name is not
        acct_raw["name"] = "REDACTED"
    write("account", acct_raw)

    # --- deals / orders / positions -------------------------------------
    deals = client.history_deals_get(EPOCH_START, now)
    orders = client.history_orders_get(EPOCH_START, now)
    positions = client.positions_get()

    write("deals", [sanitise(d.raw) for d in deals])
    write("orders", [sanitise(o.raw) for o in orders])
    write("positions", [sanitise(p.raw) for p in positions])

    # --- symbols: only those actually traded (distinct symbols in history) ---
    traded = sorted(
        {d.symbol for d in deals if d.symbol}
        | {p.symbol for p in positions if p.symbol}
    )
    symbols = []
    specs: dict[str, SymbolSpec] = {}
    for sym in traded:
        info = client.symbol_info(sym)
        if info is not None:
            symbols.append(sanitise(info.raw))  # keeps `name` = the symbol
            specs[sym] = SymbolSpec(
                symbol=sym, symbol_base=to_base(sym),
                tick_size=info.trade_tick_size, tick_value=info.trade_tick_value,
                contract_size=info.trade_contract_size,
            )
    write("symbols", symbols)

    # --- rates: one windowed chart per traded symbol ---------------------
    # Anchored to the FROZEN, already-committed deals/orders/symbols on disk
    # (read at the very top of main(), BEFORE this run's live fetch above
    # overwrote them) -- NOT to this run's fresh pull. The account trades
    # continuously; a fresh pull every run would pick a different "representative"
    # trade each time and drift deals.json/orders.json/etc away from the exact
    # frozen 2026-07-16 snapshot tests/test_ingest.py and tests/test_reconstruct.py
    # hardcode (140 deals, 68 trades, balance 6047.22, ...). Reusing the REAL
    # reconstruction (domain.reconstruct.reconstruct) on the FROZEN data -- no DB
    # needed, same pure function `journal rebuild` calls -- keeps rates.json
    # consistent with that same frozen snapshot across every re-run. Picks, per
    # symbol, the CLOSED trade whose duration is closest to that symbol's median
    # (a representative trade, not the extreme), then fetches exactly the render
    # window `journal chart` would ask for (choose_timeframe + window_for, M3) --
    # small and windowed, never a bulk unwindowed pull (220k+ M1 bars for
    # XAUUSDc alone). The live client is still used here, but ONLY for
    # `copy_rates_range` on that historical window -- never to re-select which
    # trade is "representative".
    frozen_orders_by_ticket = {
        o.ticket: o for o in frozen_orders if o.ticket is not None
    }
    frozen_trades = reconstruct(
        frozen_deals, frozen_orders_by_ticket, frozen_specs, account_login=acct.login
    )
    closed = [t for t in frozen_trades if t.status == "closed"]

    rates: dict[str, list[dict[str, Any]]] = {}
    rates_summary: list[str] = []
    for sym in sorted({t.symbol for t in closed}):
        sym_trades = [t for t in closed if t.symbol == sym]
        durations = [t.duration_s for t in sym_trades]
        median = statistics.median(durations)
        rep = min(sym_trades, key=lambda t: abs(t.duration_s - median))

        tf = choose_timeframe(rep.duration_s)
        from_msc, to_msc = window_for(rep.open_time_msc, rep.close_time_msc, tf)
        from_dt = datetime.fromtimestamp(from_msc / 1000, tz=timezone.utc)
        to_dt = datetime.fromtimestamp(to_msc / 1000, tz=timezone.utc)
        candles = client.copy_rates_range(sym, tf, from_dt, to_dt)

        # Fixture contract stores raw SECONDS (mirrors what the bridge returns
        # over the wire); FakeMT5Client re-applies the x1000 itself, exactly like
        # live.py does (Trap 15). `copy_rates_range` already gave us `time_msc`
        # in ms, so `// 1000` is the one exact inverse that restores the wire
        # value -- round-trips precisely since bridge time is integer seconds.
        rates[f"{sym}:{tf}"] = [
            {
                "time": c.time_msc // 1000,
                "open": c.open, "high": c.high, "low": c.low, "close": c.close,
                "tick_volume": c.tick_volume, "spread": c.spread,
                "real_volume": c.real_volume,
            }
            for c in candles
        ]
        rates_summary.append(
            f"{sym}: position_id={rep.position_id} duration={rep.duration_s}s "
            f"tf={tf} bars={len(candles)}"
        )
    write("rates", rates)

    # --- summary ---------------------------------------------------------
    print("recorded fixtures ->", FIXTURES)
    print(f"  account    : login=0 currency={acct.currency}")
    print(f"  deals      : {len(deals)}")
    print(f"  orders     : {len(orders)}")
    print(f"  positions  : {len(positions)}")
    print(f"  symbols    : {len(symbols)} ({', '.join(traded) or 'none'})")
    rates_path = FIXTURES / "rates.json"
    print(f"  rates      : {len(rates)} symbol:tf window(s), "
          f"{rates_path.stat().st_size} bytes")
    for line in rates_summary:
        print(f"    - {line}")

    # --- balance invariant, §6 first identity ---------------------------
    # sum(profit + commission + swap + fee) over ALL deals == account.balance
    # (includes deposits/credits, whose amount lives in `profit`). If this fails,
    # ingest dropped or duplicated a deal — nothing downstream can be trusted.
    total = sum(
        _num(d.profit) + _num(d.commission) + _num(d.swap) + _num(d.fee)
        for d in deals
    )
    balance = _num(acct.balance)
    delta = abs(total - balance)
    ok = delta < 0.01
    print()
    print("balance invariant (§6, all figures in account currency "
          f"{acct.currency}):")
    print(f"  sum(profit+commission+swap+fee) = {total:.2f}")
    print(f"  account.balance                 = {balance:.2f}")
    print(f"  |delta|                         = {delta:.4f}")
    print(f"  -> {'PASS' if ok else 'WARN'}")
    if not ok:
        # A miss is informative, not fatal, at capture time: this bridge reports
        # swap/commission as 0 on every deal, so a small overshoot may be
        # unreported holding cost rather than a dropped deal. Fixtures are still
        # written. Strict enforcement lives in `journal verify` (M2), where the
        # real ingest pipeline can investigate it (see docs/mt5-deal-model.md §6).
        print(
            f"\n  WARNING: invariant off by {delta:.2f} {acct.currency} "
            f"({delta / balance * 100:.2f}% of balance). Fixtures were still "
            "written. Confirm against MT5's Account History → Report before "
            "trusting P&L totals; enforcement is deferred to `journal verify`."
        )


if __name__ == "__main__":
    main()
