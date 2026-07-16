#!/usr/bin/env python
"""Snapshot the live account into tests/fixtures/*.json for the fake adapter.

Records five streams — account, symbols, deals, orders, positions — dumping each
record's full raw `._asdict()` so fixtures survive MT5 adding fields, then
SANITISES the copy that goes into git. Run against the live bridge; review the
resulting diff by hand before committing (CLAUDE.md Rule 10).

This is a dev-only script under scripts/, not part of the importable package, so
it may touch the bridge — and it does so through adapter.LiveMT5Client, which is
the one module allowed to import it (Rule 1). We read `.raw` off each dataclass.

Sanitisation (what lands in git):
    login   -> 0
    server  -> "REDACTED"     company -> "REDACTED"     account name -> "REDACTED"
NEVER touched: ticket, order, position_id  (reconstruction keys on them);
    comment, external_id  (execution metadata, not PII — the "Archived deals"
    marker and [sl]/[tp] tags live here; see Trap 16). Symbol `name` is the
    instrument, not PII, so it survives too.

Usage:
    uv run python scripts/record_fixtures.py [--host localhost] [--port 8001]
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from journal.adapter.live import LiveMT5Client

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
    for sym in traded:
        info = client.symbol_info(sym)
        if info is not None:
            symbols.append(sanitise(info.raw))  # keeps `name` = the symbol
    write("symbols", symbols)

    # --- summary ---------------------------------------------------------
    print("recorded fixtures ->", FIXTURES)
    print(f"  account    : login=0 currency={acct.currency}")
    print(f"  deals      : {len(deals)}")
    print(f"  orders     : {len(orders)}")
    print(f"  positions  : {len(positions)}")
    print(f"  symbols    : {len(symbols)} ({', '.join(traded) or 'none'})")

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
