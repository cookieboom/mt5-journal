"""Pin the constants the browser duplicates from the server.

Four numbers exist twice — once in Python, once in `frontend/src/lib/candles.ts`
— because the browser has to disarm a button BEFORE the click while the server
is what actually refuses the write. Both sides carry a comment saying "change
one, change the other" and nothing enforced it. A drift is silent: the button
arms, the human clicks, and the open comes back 400 from a chart that still
looks alive (the bug fixed on 2026-08-12), or a window computed in the browser
stops lining up with the stored bar times.

This reads the TypeScript as text. No node, no build, no `npm install` — so it
runs in `uv run pytest` on a machine that has never touched the frontend.
`domain/resample` already asserts its own table against `adapter.base`; this is
the same guard extended across the language boundary.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from journal import execute
from journal.adapter.base import TIMEFRAMES
from journal.domain.resample import timeframe_ms

CANDLES_TS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "candles.ts"


def _source() -> str:
    assert CANDLES_TS.exists(), f"{CANDLES_TS} is gone — was the mirror moved?"
    return CANDLES_TS.read_text(encoding="utf-8")


def _const(src: str, name: str) -> str:
    """The right-hand side of `const NAME[: type] = ...;`, exported or not."""
    m = re.search(rf"^(?:export )?const {name}\s*(?::[^=]+)?=([^;]+);", src, re.M)
    assert m, f"const {name} not found in {CANDLES_TS.name} — renamed or removed?"
    return m.group(1)


def _num(expr: str, min_ms: float | None = None) -> float:
    """Evaluate the literal forms these constants actually use: `15_000`,
    `0.25`, `240 * MIN`. Anything else fails loudly rather than being guessed
    at — a silently mis-parsed mirror is worse than no mirror check."""
    e = expr.strip().replace("_", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(\s*\*\s*MIN)?", e)
    assert m, f"cannot parse {expr!r} in {CANDLES_TS.name}; teach _num the new form"
    value = float(m.group(1))
    if m.group(2):
        assert min_ms is not None, "MIN used before it was read"
        value *= min_ms
    return value


def _tf_ms() -> dict[str, float]:
    src = _source()
    min_ms = _num(_const(src, "MIN"))
    body = _const(src, "TF_MS")
    body = body[body.index("{") + 1 : body.rindex("}")]
    pairs = [p for p in body.split(",") if p.strip()]
    return {k.strip(): _num(v, min_ms) for k, v in (p.split(":", 1) for p in pairs)}


def test_feed_stale_ms_matches() -> None:
    assert _num(_const(_source(), "FEED_STALE_MS")) == execute.FEED_STALE_MS


def test_price_ref_stop_fraction_matches() -> None:
    assert _num(_const(_source(), "PRICE_REF_STOP_FRACTION")) == execute.PRICE_REF_STOP_FRACTION


def test_timeframes_list_matches() -> None:
    listed = re.findall(r'"([^"]+)"', _const(_source(), "TIMEFRAMES"))
    assert tuple(listed) == tuple(TIMEFRAMES)


@pytest.mark.parametrize("tf", TIMEFRAMES)
def test_timeframe_ms_matches(tf: str) -> None:
    assert _tf_ms()[tf] == timeframe_ms(tf)


def test_timeframe_ms_table_has_no_extra_entries() -> None:
    assert set(_tf_ms()) == set(TIMEFRAMES)
