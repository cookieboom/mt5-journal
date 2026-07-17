#!/usr/bin/env python
"""Measure what `copy_rates_range` can ACTUALLY return from this container.

Answers the M3 open questions with behaviour, not settings:

  1. `MaxBars` actually in effect (docs/mt5-deal-model.md §7 "Still open").
  2. How far back each timeframe really reaches — i.e. how many of the 68 trades
     can be charted at all, per TF.
  3. Whether `copy_rates_range` needs `symbol_select()` the way `symbol_info` and
     `symbol_info_tick` do (trap 12). `live.py` currently does NOT select before
     fetching rates; if rates share the trap, that is a latent silent-empty-chart
     bug and M3 must not be designed on top of it.

WHY BEHAVIOUR, NOT THE SETTING: reading `terminal_info().maxbars` tells you what
the config says. It does not tell you what the broker actually still serves for a
219-day-old M1 bar. M3's failure mode is an empty chart with no error, and three
different causes produce it (Trap 15, MaxBars, unselected symbol). This probe
separates them by measurement. See doc §7 "Three different causes of empty chart".

Hard rule 1 (no `import siliconmetatrader5` outside adapter/) governs the runtime
package. This is a dev-only probe under scripts/, not importable, so it touches
the bridge directly — exactly as scripts/probe_enums.py and the live paths of
scripts/record_fixtures.py do.

Trade windows come from tests/fixtures/deals.json (sanitised; timestamps intact),
so the probe needs no DB and no prior sync.

NOTE ON UNITS: `copy_rates_*` returns `time` in epoch SECONDS (Trap 15). This
script prints raw seconds deliberately — it probes the bridge, which is below the
×1000 boundary. Do not copy its arithmetic into ingest/.

Usage:
    uv run python scripts/probe_rates.py [--host localhost] [--port 8001]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from siliconmetatrader5 import MetaTrader5

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
TFS = ("M1", "M5", "M15", "H1", "H4", "D1")


def utc(ts: float) -> dt.datetime:
    """Epoch seconds -> naive UTC. server_utc_offset_s measured 0 on this broker
    (doc §7); re-measure rather than inherit if that ever changes."""
    return dt.datetime.fromtimestamp(ts, dt.UTC).replace(tzinfo=None)


def load_trades() -> list[dict[str, Any]]:
    """Reconstruct (position_id, symbol, open_msc, close_msc) from the fixture.

    Deliberately a throwaway re-implementation of the Trap 1 filter, NOT an import
    of domain/reconstruct.py: this probe must keep working even if the package is
    broken, and a probe that depends on the code it is meant to inform is not an
    independent measurement.
    """
    deals = json.loads((FIXTURES / "deals.json").read_text())
    trade = [d for d in deals if d["type"] in (0, 1) and d["position_id"] != 0]
    by_pos: dict[int, list[dict]] = defaultdict(list)
    for d in trade:
        by_pos[d["position_id"]].append(d)

    out = []
    for pid, ds in by_pos.items():
        ins = [d for d in ds if d["entry"] == 0]
        outs = [d for d in ds if d["entry"] == 1]
        if not ins:
            continue  # orphan (trap 8); irrelevant to this probe
        out.append(
            {
                "position_id": pid,
                "symbol": ins[0]["symbol"],
                "open_msc": min(d["time_msc"] for d in ins),
                "close_msc": max(d["time_msc"] for d in outs) if outs else None,
            }
        )
    return sorted(out, key=lambda t: t["open_msc"])


def probe_select_dependency(mt5: MetaTrader5, symbol: str, when: dt.datetime) -> None:
    """Does copy_rates_range need symbol_select()? live.py assumes not.

    KNOWN-WEAK EXPERIMENT — read the verdict accordingly. Ordering within this
    session is controlled (rates are fetched before we select), but Market Watch
    PERSISTS IN THE CONTAINER'S TERMINAL ACROSS SESSIONS, and every symbol in the
    fixture is a traded symbol that record_fixtures.py selects on every run via
    symbol_info(). So the "without select" arm is almost certainly measuring an
    already-selected symbol: both arms are the same arm.

    A conclusive test needs a symbol that exists on the server and has NEVER been
    selected. Until then this probe can only report a POSITIVE (select is
    required) — it can never earn a negative. The 2026-07-17 run printed a
    negative anyway; see docs/HANDOFF.md error log.
    """
    print("=" * 72)
    print("1. DOES copy_rates_range NEED symbol_select()?  (trap 12 on rates)")
    print("=" * 72)
    print(f"   symbol={symbol}  window=24h around {when:%Y-%m-%d}")
    print("   live.py.copy_rates_range() does NOT call symbol_select().")
    print("   symbol_info()/symbol_info_tick() both DO. Measuring the difference.\n")

    frm, to = when - dt.timedelta(hours=12), when + dt.timedelta(hours=12)

    before = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, frm, to)
    n_before = 0 if before is None else len(before)
    print(f"   WITHOUT symbol_select : {n_before:6} bars")

    ok = mt5.symbol_select(symbol, True)
    after = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, frm, to)
    n_after = 0 if after is None else len(after)
    print(f"   symbol_select()       : {ok}")
    print(f"   WITH symbol_select    : {n_after:6} bars\n")

    if n_before == 0 and n_after > 0:
        print("   >>> POSITIVE RESULT (this probe CAN earn this one):")
        print("   >>> rates DO require symbol_select(). live.py.copy_rates_range()")
        print("   >>> has a latent bug — it returns [] for any symbol not already")
        print("   >>> in Market Watch, and the renderer draws an empty chart with")
        print("   >>> no error. This is a src/ fix and belongs to Claude Code.")
    elif n_before > 0 and n_before == n_after:
        print("   >>> INCONCLUSIVE — NOT a clean bill of health.")
        print(f"   >>> {symbol} is a traded symbol and is almost certainly already")
        print("   >>> in the container's persistent Market Watch, so both arms")
        print("   >>> measured the same state. This probe cannot earn a negative.")
        print("   >>> Treat the question as OPEN. A conclusive test needs a symbol")
        print("   >>> that exists on the server and has never been selected.")
    else:
        print(f"   >>> AMBIGUOUS ({n_before} -> {n_after}). Do not guess.")
        print("   >>> Re-run during an open session before concluding.")
    print()


def probe_reach(mt5: MetaTrader5, symbols: list[str], oldest: dt.datetime) -> dict:
    """For each symbol/TF: how far back does history actually reach?

    One call per (symbol, TF) over the full span — cheaper than 68 per-trade calls
    and it yields the same decisive number: the earliest bar that exists.
    """
    print("=" * 72)
    print("2. HOW FAR BACK DOES EACH TIMEFRAME REACH?")
    print("=" * 72)
    frm = oldest - dt.timedelta(days=7)  # pad before the oldest trade
    to = dt.datetime.now(dt.UTC).replace(tzinfo=None) + dt.timedelta(days=1)
    print(f"   requesting {frm:%Y-%m-%d} -> {to:%Y-%m-%d} "
          f"({(to - frm).days} days) per symbol/TF\n")

    reach: dict[tuple[str, str], dt.datetime | None] = {}
    for sym in symbols:
        mt5.symbol_select(sym, True)  # select first; §1 above tells us if it matters
        print(f"   {sym}")
        for tf in TFS:
            const = getattr(mt5, f"TIMEFRAME_{tf}")
            rows = mt5.copy_rates_range(sym, const, frm, to)
            if rows is None or len(rows) == 0:
                reach[(sym, tf)] = None
                print(f"     {tf:3}: {'0':>7} bars   <- NOTHING RETURNED")
                continue
            first, last = utc(rows[0]["time"]), utc(rows[-1]["time"])
            reach[(sym, tf)] = first
            days = (last - first).days
            print(f"     {tf:3}: {len(rows):>7} bars   {first:%Y-%m-%d %H:%M} "
                  f"-> {last:%Y-%m-%d %H:%M}  ({days}d)")
        print()
    return reach


def probe_coverage(trades: list[dict], reach: dict) -> None:
    """The number that decides M3: how many of the 68 trades are chartable."""
    print("=" * 72)
    print("3. HOW MANY OF THE 68 TRADES ARE CHARTABLE, PER TF?")
    print("=" * 72)
    print("   A trade is chartable at TF if its OPEN time is at/after the")
    print("   earliest bar that exists for its symbol at that TF.\n")
    print(f"   {'TF':4} {'chartable':>11}  {'lost':>5}   oldest chartable trade")
    print("   " + "-" * 60)
    for tf in TFS:
        ok, lost, first_ok = 0, 0, None
        for t in trades:
            earliest = reach.get((t["symbol"], tf))
            if earliest is None:
                lost += 1
                continue
            if utc(t["open_msc"] / 1000) >= earliest:
                ok += 1
                if first_ok is None:
                    first_ok = utc(t["open_msc"] / 1000)
            else:
                lost += 1
        stamp = f"{first_ok:%Y-%m-%d}" if first_ok else "—"
        flag = "  <-- " + ("ALL" if lost == 0 else f"{lost} MISSING") if lost else ""
        print(f"   {tf:4} {ok:>6}/{len(trades):<4}  {lost:>5}   {stamp}{flag}")
    print()
    print("   Read this against doc §7 'Trade duration profile': the median trade")
    print("   is 6m13s, so M15 shows it as ONE candle and M1 is the only TF with")
    print("   real structure. If M1 coverage here is poor, `journal chart` cannot")
    print("   meaningfully render most of the history — and that is an M3 design")
    print("   input, not a detail.")
    print()


def probe_maxbars(mt5: MetaTrader5) -> None:
    """The literal open question. Reported, but subordinate to the measurements
    above — a config value is a claim; bars returned are a fact."""
    print("=" * 72)
    print("4. terminal_info() — the MaxBars SETTING (secondary to §2/§3 above)")
    print("=" * 72)
    try:
        info = mt5.terminal_info()
    except Exception as e:
        print(f"   terminal_info() raised: {e!r}")
        print("   -> The setting is unavailable. §2/§3 already answered the")
        print("      question that matters; do not block M3 on this.\n")
        return
    if info is None:
        print("   terminal_info() returned None — bridge may not expose it.")
        print("   -> Rely on §2/§3.\n")
        return
    d = info._asdict() if hasattr(info, "_asdict") else dict(info)
    for k in ("maxbars", "max_bars", "MAXBARS"):
        if k in d:
            print(f"   {k} = {d[k]}")
            break
    else:
        print("   no maxbars-like key found. Full terminal_info() keys:")
        print("   " + ", ".join(sorted(d)))
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()

    trades = load_trades()
    symbols = sorted({t["symbol"] for t in trades})
    oldest = utc(min(t["open_msc"] for t in trades) / 1000)
    newest = utc(max(t["open_msc"] for t in trades) / 1000)

    print()
    print(f"trades in fixture : {len(trades)}")
    print(f"symbols           : {', '.join(symbols)}")
    print(f"oldest trade open : {oldest:%Y-%m-%d %H:%M} UTC")
    print(f"newest trade open : {newest:%Y-%m-%d %H:%M} UTC")
    print(f"span              : {(newest - oldest).days} days")
    print()

    mt5 = MetaTrader5(host=args.host, port=args.port, keepalive=True)
    if not mt5.initialize():
        try:
            err = mt5.last_error()
        except Exception:
            err = "unavailable"
        raise SystemExit(
            f"initialize() failed on {args.host}:{args.port} "
            f"(last_error={err}) — is the Docker container up and logged in?"
        )

    # ORDER IS LOAD-BEARING: the select-dependency probe must run before anything
    # else selects a symbol, or it reports a false negative.
    probe_select_dependency(mt5, symbols[0] if symbols else "XAUUSDc", newest)
    reach = probe_reach(mt5, symbols, oldest)
    probe_coverage(trades, reach)
    probe_maxbars(mt5)

    print("=" * 72)
    print("Paste this whole output back. It decides three things in M3:")
    print("  - whether live.py.copy_rates_range() needs a symbol_select() fix")
    print("  - which TF `journal chart` can default to, per trade age")
    print("  - whether candle backfill is urgent (bars ageing out = Trap 16 for")
    print("    market data: what the container no longer serves is gone)")
    print("=" * 72)


if __name__ == "__main__":
    main()
