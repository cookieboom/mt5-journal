#!/usr/bin/env python
"""Dump the MT5 bridge's enum constants as paste-ready Python blocks.

Ground truth for `src/journal/adapter/base.py`. The doc was wrong once already
(COMMISSION=6 vs the bridge's 7) — do NOT copy enum values from memory or from
docs/mt5-deal-model.md §2. Run this, then paste the DEAL_TYPE / DEAL_REASON
blocks straight into base.py. See CLAUDE.md Rule 12 and doc §2.

Hard rule 1 (no `import siliconmetatrader5` outside adapter/) is about the
runtime codebase. This is a dev-only probe under scripts/, not part of the
importable package, so it is allowed to touch the bridge directly — the same
way scripts/record_fixtures.py does.

Usage:
    uv run python scripts/probe_enums.py [--host localhost] [--port 8001]
"""

from __future__ import annotations

import argparse

from siliconmetatrader5 import MetaTrader5

# Every constant family we care about. DEAL_* drive our IntEnums; ORDER_* are
# dumped for reference / the doc (we do not currently model them as enums).
PREFIXES = (
    "DEAL_TYPE_",
    "DEAL_ENTRY_",
    "DEAL_REASON_",
    "ORDER_REASON_",
    "ORDER_STATE_",
)


def dump(mt5: MetaTrader5, prefix: str) -> None:
    consts = {a: getattr(mt5, a) for a in dir(mt5) if a.startswith(prefix)}
    print(f"# --- {prefix}* ({len(consts)} constants) " + "-" * 24)
    if not consts:
        print(f"#   (bridge exposes no {prefix}* constants)")
        print()
        return
    # Sort by value so the paste-ready block reads like an IntEnum body. Strip
    # the prefix so `DEAL_TYPE_BUY = 0` becomes `BUY = 0`.
    for name, val in sorted(consts.items(), key=lambda kv: (kv[1], kv[0])):
        member = name[len(prefix):]
        print(f"{member} = {val}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()

    mt5 = MetaTrader5(host=args.host, port=args.port, keepalive=True)
    # Best-effort: constants are usually available without a live session, but
    # initialize() costs nothing and matches how the real client comes up.
    if not mt5.initialize():
        try:
            err = mt5.last_error()
        except Exception:
            err = "unavailable"
        print(f"# WARNING: initialize() failed (last_error={err}); "
              f"reading constants anyway.\n")

    print("# Probed from the live bridge — paste the DEAL_* blocks into")
    print("# src/journal/adapter/base.py. Values are authoritative over any doc.")
    print()
    for prefix in PREFIXES:
        dump(mt5, prefix)


if __name__ == "__main__":
    main()
