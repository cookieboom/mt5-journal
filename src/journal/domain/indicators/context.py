"""Trade context: did a same-side `signal()` fire in the `window` closed bars
before a trade? (spec 2026-10-08-indicators-trade-context, §2 A).

Pure — series in, label out. Descriptive of past trades (rule 9): the answer
is "agreed / did not / unknown", never advice."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
import pandas as pd

# A last closed bar further back than this is a store hole, not a closure: the
# longest genuine one (weekend + holiday) is well under 4 days.
MAX_GAP_MS = 4 * 86_400_000

_SIDE = {"buy": "long", "sell": "short"}


def agreement(index: pd.Index, tf_ms: int, signals: Sequence[tuple[str, pd.Series]],
              ref_msc: int, direction: str, window: int) -> Literal["true", "false"] | None:
    """`"true"` when a signal on the trade's side is 1.0 on any of the `window`
    stored bars ending at the last bar closed by `ref_msc` (bar open `t` closed
    when `t + tf_ms <= ref_msc` — the forming bar is never read). Bars are
    counted in the store, not in time, so a weekend inside the window is fine.

    `None` (unknown, rule 4) when fewer than `window` bars exist, when the last
    closed bar is more than `MAX_GAP_MS` old, or when no same-side signal fires
    and any of their values in the window is NaN (warm-up) — never `"false"`."""
    t = index.to_numpy(dtype="int64")
    k = int(np.searchsorted(t + tf_ms, ref_msc, side="right")) - 1
    if k + 1 < window or ref_msc - (t[k] + tf_ms) > MAX_GAP_MS:
        return None
    side = _SIDE[direction]
    unknown = False
    for s, values in signals:
        if s != side:
            continue
        w = values.to_numpy(dtype="float64")[k - window + 1:k + 1]
        if np.any(w == 1.0):
            return "true"
        unknown = unknown or bool(np.isnan(w).any())
    return None if unknown else "false"
