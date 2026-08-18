"""Summary statistics shared by every SIMULATED account — replay/training and
paper trading. Pure: mappings in, a dict out.

NOT §8-gated, unlike `analytics/report`. A replay session or a paper account is a
handful of trades, so a 20-sample floor blanked every rate permanently and the
panel carried no information at all. Every metric ships with its own `n`; the
reader judges the sample.

Only CLOSED, resolved rows (non-null `net_profit`) count. An unfilled or
unresolved position is excluded — unknown outcome, rule 4.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def summary(rows: Sequence[Mapping[str, Any]]) -> dict:
    """Aggregate resolved rows. A metric is null only when it has NO input
    (rule 4 — unknown, never zero)."""
    resolved = [r for r in rows if r["net_profit"] is not None]
    n = len(resolved)
    r_vals = [r["r_multiple"] for r in resolved if r["r_multiple"] is not None]
    mae_vals = [r["mae_r"] for r in resolved if r["mae_r"] is not None]
    mfe_vals = [r["mfe_r"] for r in resolved if r["mfe_r"] is not None]
    total_r = sum(r_vals)
    wins = sum(1 for r in resolved if r["net_profit"] > 0)
    return {
        "n": n,
        "win_rate": (wins / n) if n else None,
        "avg_r": (total_r / len(r_vals)) if r_vals else None,
        "total_r": total_r,
        "avg_mae_r": (sum(mae_vals) / len(mae_vals)) if mae_vals else None,
        "avg_mfe_r": (sum(mfe_vals) / len(mfe_vals)) if mfe_vals else None,
    }
