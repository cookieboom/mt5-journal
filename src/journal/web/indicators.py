"""Indicator service: scripts (library + user), validation, and computation over
stored candles. Pure DB — never the bridge (M9 boundary); an uncovered warm-up
range is queued for `journal live` like any chart range.

Replay safety lives HERE, not in the browser: with a `session_id` the request
is clipped to bars the session has already revealed — a bar is visible only if
it CLOSED by the end of the cursor bar, on whatever timeframe is asked for —
and the live forming bar is never included (spec 2026-10-07-indicators §inv. 2).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..adapter.base import TIMEFRAMES, Candle
from ..domain.indicators.engine import evaluate
from ..domain.indicators.frame import to_frame
from ..domain.indicators.lang import (
    Program, ScriptError, const_value, htf_lookback, lookback, parse, resolve_consts,
)
from ..domain.indicators.library import library
from ..domain.resample import timeframe_ms
from ..store import candle_queue, live_store
from ..store import candles_store as cs
from ..store import prefs_store
from ..store import training_store as ts
from ..store.db import now_ms

SCRIPTS_KEY = "indicator_scripts"
LAYOUT_KEY = "indicator_layout"
DAY_MS = 86_400_000


# --- scripts ----------------------------------------------------------------

def _user_blob(conn: sqlite3.Connection) -> dict:
    raw = prefs_store.get_pref(conn, SCRIPTS_KEY)
    blob = json.loads(raw) if raw else {}
    return {"next_id": int(blob.get("next_id", 1)), "scripts": list(blob.get("scripts", []))}


def list_scripts(conn: sqlite3.Connection) -> list[dict]:
    lib = [{"id": s.id, "name": s.name, "source": s.source, "readonly": True,
            "updated_ms": None} for s in library().values()]
    user = [{**s, "readonly": False} for s in _user_blob(conn)["scripts"]]
    return lib + user


def _source_of(conn: sqlite3.Connection, script_id: str) -> str:
    if script_id in library():
        return library()[script_id].source
    for s in _user_blob(conn)["scripts"]:
        if s["id"] == script_id:
            return str(s["source"])
    raise ValueError(f"no indicator script {script_id!r}")


def save_script(conn: sqlite3.Connection, script_id: str | None, *,
                name: str, source: str) -> dict:
    """Create (`script_id` None) or replace a user script. The source must
    parse — a broken script is never stored (ScriptError carries line/col)."""
    if script_id is not None and script_id.startswith("lib:"):
        raise ValueError("library scripts are read-only; save a copy instead")
    parse(source)
    blob = _user_blob(conn)
    if script_id is None:
        script_id = f"user:{blob['next_id']}"
        blob["next_id"] += 1
        blob["scripts"].append({"id": script_id})
    entry = next((s for s in blob["scripts"] if s["id"] == script_id), None)
    if entry is None:
        raise ValueError(f"no indicator script {script_id!r}")
    entry.update(name=name.strip() or "Untitled", source=source, updated_ms=now_ms())
    prefs_store.set_pref(conn, SCRIPTS_KEY, json.dumps(blob))
    return {**entry, "readonly": False}


def delete_script(conn: sqlite3.Connection, script_id: str) -> None:
    if script_id.startswith("lib:"):
        raise ValueError("library scripts are read-only")
    blob = _user_blob(conn)
    blob["scripts"] = [s for s in blob["scripts"] if s["id"] != script_id]
    prefs_store.set_pref(conn, SCRIPTS_KEY, json.dumps(blob))


# --- validation -------------------------------------------------------------

def _meta(p: Program, consts: dict[str, float | bool]) -> dict:
    return {
        "inputs": [{"name": i.name, "kind": i.kind, "default": i.default, "min": i.min,
                    "max": i.max, "step": i.step, "title": i.title} for i in p.inputs],
        "plots": [{"title": pl.title, "color": pl.color, "pane": pl.pane,
                   "style": pl.style, "width": pl.width} for pl in p.plots],
        "hlines": [{"value": float(const_value(h.value, consts)), "title": h.title,
                    "pane": h.pane, "color": h.color} for h in p.hlines],
        "signals": [s.side for s in p.signals],
    }


def error_payload(e: ScriptError) -> dict:
    return {"line": e.line, "col": e.col, "message": e.msg}


def validate(source: str) -> dict:
    try:
        p = parse(source)
        consts = resolve_consts(p, {})
        meta = _meta(p, consts)
        # Per chart TF; None where the script does not apply (tf() not higher).
        lbs: dict[str, int | None] = {}
        first: ScriptError | None = None
        for tf in TIMEFRAMES:
            try:
                lbs[tf] = lookback(p, consts, timeframe_ms(tf))
            except ScriptError as e:
                lbs[tf], first = None, first or e
        if first is not None and all(v is None for v in lbs.values()):
            raise first
        meta["lookback"] = lbs
    except ScriptError as e:
        return {"ok": False, "error": error_payload(e)}
    return {"ok": True, **meta}


# --- computation ------------------------------------------------------------

def _warm_bars(conn: sqlite3.Connection, symbol: str, tf: str, from_ms: int,
               need: int) -> list[Candle]:
    """Up to `need`+ stored bars before `from_ms`. Counts BARS, not time: the
    window doubles across weekend/holiday gaps until it holds enough or reaches
    the cap (`ponytail:` a large lookback on an M1-aggregated TF reads a lot of
    M1; a native store for that TF is the fix if it ever matters)."""
    if need <= 0:
        return []
    tf_ms = timeframe_ms(tf)
    span = 2 * (need + 1) * tf_ms
    cap = max(8 * need * tf_ms, 14 * DAY_MS)
    while True:
        bars = [b for b in cs.load_bars(conn, symbol, tf, from_ms - span, from_ms - 1)
                if b.time_msc is not None and b.time_msc < from_ms]
        if len(bars) >= need or span >= cap:
            return bars
        span *= 2


@dataclass
class Frames:
    """What `load_frames` read: the chart frame (warm-up included), tf() frames,
    the forming bar's open (if appended), whether a warm-up range was queued,
    and the first bar whose every input is warm."""
    f: pd.DataFrame
    htf: dict[str, pd.DataFrame]
    forming_msc: int | None
    pending: bool
    warm_from_msc: int | None


def load_frames(conn: sqlite3.Connection, p: Program, consts: dict[str, float | bool], *,
                symbol: str, timeframe: str, from_ms: int, to_ms: int, lb: int,
                reveal_end: int | None = None, include_forming: bool = False) -> Frames:
    """Chart bars [from_ms, to_ms] plus `lb` warm-up bars, tf() frames with their
    own warm-up, and the forming bar when asked. `reveal_end`: replay clip — only
    bars CLOSED by then, on every timeframe. Missing warm-up is queued."""
    tf_ms = timeframe_ms(timeframe)

    view = [b for b in cs.load_bars(conn, symbol, timeframe, from_ms, to_ms)
            if b.time_msc is not None and from_ms <= b.time_msc <= to_ms] \
        if to_ms >= from_ms else []
    warm = _warm_bars(conn, symbol, timeframe, from_ms, lb)
    bars = warm + view
    if reveal_end is not None:   # belt and braces over to_ms: closed by reveal_end
        bars = [b for b in bars if b.time_msc is not None and b.time_msc + tf_ms <= reveal_end]

    forming_msc: int | None = None
    if include_forming:
        fc = live_store.read_forming(conn, symbol, timeframe)
        last = bars[-1].time_msc if bars else None
        if fc is not None and fc.time_msc is not None and fc.time_msc >= from_ms \
                and (last is None or fc.time_msc > last):
            bars.append(fc)
            forming_msc = fc.time_msc

    pending = False
    if len(warm) < lb:
        pending = candle_queue.request_candles(
            conn, symbol, timeframe, from_ms - lb * tf_ms, from_ms - 1) != 0

    # tf() frames: from two HTF bars before the first chart bar (its mapped bar
    # opens after that), plus that TF's own warm-up.
    start = bars[0].time_msc if bars and bars[0].time_msc is not None else from_ms
    htf: dict[str, pd.DataFrame] = {}
    htf_ready: list[int | None] = []      # close time of each TF's first warm bar
    for x, need in htf_lookback(p, consts, tf_ms).items():
        h = timeframe_ms(x)
        hstart = start - 2 * h
        hview = [b for b in cs.load_bars(conn, symbol, x, hstart, to_ms)
                 if b.time_msc is not None and hstart <= b.time_msc <= to_ms]
        hwarm = _warm_bars(conn, symbol, x, hstart, need)
        hbars = hwarm + hview
        if reveal_end is not None:   # the mapping skips them too; never even load them
            hbars = [b for b in hbars if b.time_msc is not None and b.time_msc + h <= reveal_end]
        if len(hwarm) < need:
            pending = candle_queue.request_candles(
                conn, symbol, x, hstart - need * h, hstart - 1) != 0 or pending
        htf[x] = to_frame(hbars)
        htf_ready.append(int(htf[x].index[need]) + h if len(hbars) > need else None)

    f = to_frame(bars)
    times = [int(t) for t in f.index]
    warm_from: int | None = times[lb] if len(times) > lb else None
    for ready in htf_ready:   # the latest TF to become warm decides
        if warm_from is None or ready is None:
            warm_from = None
            break
        warm_from = next((t for t in times if t >= warm_from and t + tf_ms >= ready), None)
    return Frames(f, htf, forming_msc, pending, warm_from)


def compute(conn: sqlite3.Connection, *, inputs: dict[str, Any], symbol: str,
            timeframe: str, from_ms: int, to_ms: int, script: str | None = None,
            source: str | None = None, session_id: int | None = None,
            include_forming: bool = False) -> dict:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}")
    if source is None:
        if script is None:
            raise ValueError("give a script id or a source")
        source = _source_of(conn, script)
    tf_ms = timeframe_ms(timeframe)

    reveal_end: int | None = None
    if session_id is not None:
        s = ts.get_session(conn, session_id)
        if s is None:
            raise ValueError(f"no training session {session_id}")
        reveal_end = int(s["cursor_msc"]) + timeframe_ms(s["timeframe"])
        to_ms = min(to_ms, reveal_end - tf_ms)
        include_forming = False

    p = parse(source)
    consts = resolve_consts(p, inputs)
    lb = lookback(p, consts, tf_ms)
    fr = load_frames(conn, p, consts, symbol=symbol, timeframe=timeframe, from_ms=from_ms,
                     to_ms=to_ms, lb=lb, reveal_end=reveal_end,
                     include_forming=include_forming)
    f, forming_msc = fr.f, fr.forming_msc
    r = evaluate(p, f, inputs, tf_ms, htf=fr.htf, forming_msc=forming_msc)
    times = [int(t) for t in f.index]
    keep = [i for i, t in enumerate(times) if t >= from_ms]

    def values(s: Any) -> list[float | None]:
        arr = s.to_numpy(dtype=float)
        return [None if arr[i] != arr[i] else float(arr[i]) for i in keep]

    meta = _meta(p, consts)
    signals = []
    for i in keep:
        t = times[i]
        if t == forming_msc:
            continue   # never a signal on a bar that has not closed (inv. 5)
        for sig, series in zip(p.signals, r.signals, strict=True):
            if series.iloc[i] == 1.0:
                signals.append({"time_msc": t, "side": sig.side})

    return {
        "times": [times[i] for i in keep],
        "plots": [{**m, "values": values(s)} for m, s in zip(meta["plots"], r.plots, strict=True)],
        "hlines": [{**h, "value": v} for h, v in zip(meta["hlines"], r.hlines, strict=True)],
        "signals": signals,
        "lookback": lb,
        "warm_from_msc": fr.warm_from_msc,
        "forming_msc": forming_msc,
        "pending": fr.pending,
    }


# --- layout (client-owned blob, like the other chart prefs) ------------------

def get_layout(conn: sqlite3.Connection) -> Any | None:
    raw = prefs_store.get_pref(conn, LAYOUT_KEY)
    return json.loads(raw) if raw is not None else None


def set_layout(conn: sqlite3.Connection, layout: dict) -> int:
    return prefs_store.set_pref(conn, LAYOUT_KEY, json.dumps(layout))
