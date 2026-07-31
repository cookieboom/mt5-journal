"""Chart Phase D orchestration — the impure glue that turns replay decisions into
persisted, scored fake trades. It composes the PURE evaluator (domain/replay_eval),
the pure-DB store (store/training_store), the cached candle reader
(store/candles_store.load_bars — never the bridge, M9 boundary), and the fill
queue (store/candle_queue). Money is USC; R and MAE/MFE reuse the same pure
helpers the real pipeline uses (domain/excursion).

Fill/exit TIMING and PRICE come from replay_eval.step_bar; this module adds the
money (needs symbol_specs) and the excursion (needs candle rows) at close time.
"""
from __future__ import annotations

import sqlite3

from ..adapter.base import TIMEFRAMES
from ..domain.excursion import compute_excursion
from ..domain.resample import timeframe_ms
from ..domain.symbols import to_base
from ..domain import replay_eval as ev
from ..store import candle_queue
from ..store import candles_store as cs
from ..store import training_store as ts
from ..store.db import now_ms


def _check_direction(direction: str, entry_price: float | None,
                     sl: float | None, tp: float | None) -> None:
    """Reject SL/TP on the wrong side of entry for the position's direction.
    Skips whichever side is None (unchanged) or 0 (removed, rule 4) — there's
    nothing to be inconsistent with. If entry_price is unknown yet (position
    still pending), falls back to checking sl vs tp against each other."""
    def is_set(v: float | None) -> bool:
        return v is not None and abs(v) > 1e-9

    ref = entry_price if entry_price is not None else None
    if ref is None:
        if is_set(sl) and is_set(tp):
            if direction == "buy" and not (sl < tp):
                raise ValueError("SL must be below TP for a buy position")
            if direction == "sell" and not (sl > tp):
                raise ValueError("SL must be above TP for a sell position")
        return

    if is_set(sl):
        if direction == "buy" and not (sl < ref):
            raise ValueError("SL must be below entry price for a buy position")
        if direction == "sell" and not (sl > ref):
            raise ValueError("SL must be above entry price for a sell position")
    if is_set(tp):
        if direction == "buy" and not (tp > ref):
            raise ValueError("TP must be above entry price for a buy position")
        if direction == "sell" and not (tp < ref):
            raise ValueError("TP must be below entry price for a sell position")


def _row(row: sqlite3.Row | None) -> dict | None:
    return None if row is None else {k: row[k] for k in row.keys()}


def _positions(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    return [_row(r) for r in ts.list_positions(conn, session_id)]


def _specs(conn: sqlite3.Connection, symbol: str) -> tuple[float, float] | None:
    r = conn.execute(
        "SELECT tick_size, tick_value FROM symbol_specs WHERE symbol = ?", (symbol,)
    ).fetchone()
    if r is None or r["tick_size"] in (None, 0) or r["tick_value"] in (None, 0):
        return None
    return float(r["tick_size"]), float(r["tick_value"])


def _next_bars(conn: sqlite3.Connection, symbol: str, timeframe: str,
               cursor_msc: int, range_end_msc: int, n: int) -> list:
    """The next `n` cached bars strictly after `cursor_msc`, up to `range_end_msc`.
    Widens the read window past market gaps: a fixed time-box would land entirely
    inside a weekend/holiday gap, read empty, and be mistaken for end-of-range. We
    double the span until at least `n` bars are found or the window already reaches
    range_end (genuinely no more bars). Bars come from cs.load_bars (native, else
    M1-aggregated) so evaluation matches what the chart shows."""
    span = timeframe_ms(timeframe) * (n + 2)
    while True:
        hi = min(range_end_msc, cursor_msc + span)
        bars = [b for b in cs.load_bars(conn, symbol, timeframe, cursor_msc + 1, hi)
                if b.time_msc > cursor_msc]
        if len(bars) >= n or hi >= range_end_msc:
            return bars[:n]
        span *= 2


def create_session(conn: sqlite3.Connection, *, symbol: str, timeframe: str,
                   range_start_msc: int, range_end_msc: int,
                   cursor_start_msc: int | None = None) -> dict:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}; expected one of {list(TIMEFRAMES)}")
    if range_start_msc > range_end_msc:
        raise ValueError("range_start_msc must be <= range_end_msc")
    cursor = range_start_msc if cursor_start_msc is None else cursor_start_msc
    if not (range_start_msc <= cursor <= range_end_msc):
        raise ValueError("cursor_start_msc must lie within [range_start_msc, range_end_msc]")

    sid = ts.create_session(
        conn, symbol=symbol, symbol_base=to_base(symbol), timeframe=timeframe,
        range_start_msc=range_start_msc, range_end_msc=range_end_msc,
        cursor_msc=cursor,
    )
    # Ensure the whole replay range is cached; the web NEVER touches the bridge —
    # it enqueues and `journal live` drains (returns 0 when already covered).
    req = candle_queue.request_candles(conn, symbol, timeframe,
                                       range_start_msc, range_end_msc)
    return {"session": _row(ts.get_session(conn, sid)), "pending": req != 0}


def session_view(conn: sqlite3.Connection, session_id: int) -> dict | None:
    s = ts.get_session(conn, session_id)
    if s is None:
        return None
    return {"session": _row(s), "positions": _positions(conn, session_id),
            "summary": ts.session_summary(conn, session_id)}


def list_sessions_view(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    return [_row(r) for r in ts.list_sessions(conn, status)]


def open_position(conn: sqlite3.Connection, session_id: int, *, direction: str,
                  volume: float, sl: float, tp: float) -> dict:
    s = ts.get_session(conn, session_id)
    if s is None:
        raise ValueError(f"no training session {session_id}")
    if direction not in ("buy", "sell"):
        raise ValueError("direction must be 'buy' or 'sell'")
    if s["status"] != "active":
        raise ValueError(f"session {session_id} is not active")
    if volume <= 0:
        raise ValueError("volume must be > 0")
    _check_direction(direction, None, sl, tp)
    pid = ts.insert_position(conn, session_id=session_id, direction=direction,
                             volume=volume, decision_msc=s["cursor_msc"],
                             sl=sl, tp=tp)
    return _row(ts.get_position(conn, pid))


def close_position(conn: sqlite3.Connection, session_id: int, position_id: int) -> dict:
    s = ts.get_session(conn, session_id)
    if s is None:
        raise ValueError(f"no training session {session_id}")
    pos = ts.get_position(conn, position_id)
    if pos is None or pos["session_id"] != session_id:
        raise ValueError(f"position {position_id} does not belong to session {session_id}")
    ts.request_close(conn, position_id, s["cursor_msc"])
    return _row(ts.get_position(conn, position_id))


def _to_state(r: sqlite3.Row) -> ev.PositionState:
    return ev.PositionState(
        id=r["id"], direction=r["direction"], volume=r["volume"],
        decision_msc=r["decision_msc"], sl=r["sl"], tp=r["tp"], status=r["status"],
        entry_msc=r["entry_msc"], entry_price=r["entry_price"],
        close_requested_msc=r["close_requested_msc"],
    )


def _resolve_close(conn: sqlite3.Connection, symbol: str, timeframe: str,
                   state: ev.PositionState) -> None:
    """Persist a just-closed position with money, R, and MAE/MFE. Reuses the same
    pure helpers the real pipeline uses; degrades to null money if no symbol_specs."""
    net = r = mae = mfe = mae_r = mfe_r = None
    specs = _specs(conn, symbol)
    if specs is not None and state.entry_price is not None and state.exit_price is not None:
        tick_size, tick_value = specs
        net = ev.net_profit_usc(state.direction, state.entry_price, state.exit_price,
                                state.volume, tick_size, tick_value)
    if state.entry_price is not None and state.exit_price is not None:
        r = ev.r_multiple(state.direction, state.entry_price, state.exit_price, state.sl)
    if state.entry_msc is not None and state.exit_msc is not None:
        bars = cs.load_bars(conn, symbol, timeframe, state.entry_msc, state.exit_msc)
        mae, mfe = compute_excursion(
            [(b.time_msc, b.low, b.high) for b in bars],
            state.entry_msc, state.exit_msc, state.entry_price, state.direction,
        )
        risk = abs(state.entry_price - state.sl) if state.sl else None
        if risk:  # truthy: not None and not 0.0 (Trap 6 shape)
            if mae is not None:
                mae_r = mae / risk
            if mfe is not None:
                mfe_r = mfe / risk
    ts.mark_close(conn, state.id, exit_msc=state.exit_msc, exit_price=state.exit_price,
                  exit_reason=state.exit_reason, net_profit=net, r_multiple=r,
                  mae=mae, mfe=mfe, mae_r=mae_r, mfe_r=mfe_r)
    session_id = conn.execute(
        "SELECT session_id FROM training_positions WHERE id = ?", (state.id,)
    ).fetchone()["session_id"]
    conn.execute(
        "INSERT OR IGNORE INTO training_session_stats (session_id, updated_at_msc) VALUES (?, ?)",
        (session_id, now_ms()),
    )
    column = {"sl": "sl_hits", "tp": "tp_hits"}.get(state.exit_reason, "manual_closes")
    conn.execute(
        f"UPDATE training_session_stats SET {column} = {column} + 1, "
        "total_closed = total_closed + 1, updated_at_msc = ? WHERE session_id = ?",
        (now_ms(), session_id),
    )
    conn.commit()


def step(conn: sqlite3.Connection, session_id: int, n: int = 1) -> dict:
    s = ts.get_session(conn, session_id)
    if s is None:
        raise ValueError(f"no training session {session_id}")
    if n < 1:
        raise ValueError("n must be >= 1")
    symbol, tf = s["symbol"], s["timeframe"]
    cursor, range_end = s["cursor_msc"], s["range_end_msc"]

    # The next n revealed bars are the first n with time_msc > cursor, up to range_end.
    upcoming = _next_bars(conn, symbol, tf, cursor, range_end, n)

    states = [_to_state(r) for r in ts.active_positions(conn, session_id)]
    by_id = {st.id: st for st in states}
    all_events: list[dict] = []
    for bar in upcoming:
        events = ev.step_bar(states, ev.Bar(bar.time_msc, bar.open, bar.high,
                                            bar.low, bar.close))
        for e in events:
            st = by_id[e.position_id]
            if e.kind == "fill":
                ts.mark_fill(conn, st.id, entry_msc=st.entry_msc, entry_price=st.entry_price)
            else:  # exit
                _resolve_close(conn, symbol, tf, st)
            all_events.append({"position_id": e.position_id, "kind": e.kind,
                               "price": e.price, "time_msc": e.time_msc, "reason": e.reason})
        cursor = bar.time_msc

    if not upcoming:
        cursor = range_end   # reached the end of the range; nothing left to reveal
    ts.update_cursor(conn, session_id, cursor)
    return {"cursor_msc": cursor, "events": all_events,
            "positions": _positions(conn, session_id),
            "summary": ts.session_summary(conn, session_id)}


def end_session(conn: sqlite3.Connection, session_id: int) -> dict:
    s = ts.get_session(conn, session_id)
    if s is None:
        raise ValueError(f"no training session {session_id}")
    for r in ts.active_positions(conn, session_id):
        # Unresolved at end of range: exit_reason 'eod', no money/R (unknown, rule 4).
        ts.mark_close(conn, r["id"], exit_msc=s["cursor_msc"], exit_price=None,
                      exit_reason="eod", net_profit=None, r_multiple=None,
                      mae=None, mfe=None, mae_r=None, mfe_r=None)
    ts.set_session_status(conn, session_id, "ended")
    return session_view(conn, session_id)


def career_summary(conn: sqlite3.Connection) -> dict:
    return ts.career_summary(conn)


def modify_sltp(
    conn: sqlite3.Connection,
    position_id: int,
    sl: float | None = None,
    tp: float | None = None,
) -> dict:
    """Modify SL/TP of an open training position. sl/tp: None = leave
    unchanged, 0 = remove (rule 4), any other value = set."""
    pos = conn.execute(
        "SELECT * FROM training_positions WHERE id = ?", (position_id,)
    ).fetchone()
    if not pos:
        raise ValueError(f"position {position_id} not found")
    if pos["status"] == "closed":
        raise ValueError(f"position {position_id} already closed")

    _check_direction(pos["direction"], pos["entry_price"], sl, tp)

    updates, params = [], []
    if sl is not None:
        updates.append("sl = ?")
        params.append(sl)
    if tp is not None:
        updates.append("tp = ?")
        params.append(tp)
    if updates:
        params.append(position_id)
        conn.execute(f"UPDATE training_positions SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()

    return _row(conn.execute(
        "SELECT * FROM training_positions WHERE id = ?", (position_id,)
    ).fetchone())


def get_session_stats(conn: sqlite3.Connection, session_id: int) -> dict:
    """SL/TP hit statistics for a training session. Lazily initializes the
    stats row on first read (a session created before this feature existed
    has none yet)."""
    stats = conn.execute(
        "SELECT * FROM training_session_stats WHERE session_id = ?", (session_id,)
    ).fetchone()
    if not stats:
        conn.execute(
            "INSERT INTO training_session_stats (session_id, updated_at_msc) VALUES (?, ?)",
            (session_id, now_ms()),
        )
        conn.commit()
        stats = {"total_closed": 0, "sl_hits": 0, "tp_hits": 0, "manual_closes": 0}

    total = stats["total_closed"]
    sl_rate = stats["sl_hits"] / total if total > 0 else None
    tp_rate = stats["tp_hits"] / total if total > 0 else None

    avg_r_sl = conn.execute(
        "SELECT AVG(r_multiple) FROM training_positions "
        "WHERE session_id = ? AND exit_reason = 'sl'", (session_id,),
    ).fetchone()[0]
    avg_r_tp = conn.execute(
        "SELECT AVG(r_multiple) FROM training_positions "
        "WHERE session_id = ? AND exit_reason = 'tp'", (session_id,),
    ).fetchone()[0]

    return {
        "session_id": session_id,
        "total_closed": total,
        "sl_hits": stats["sl_hits"],
        "tp_hits": stats["tp_hits"],
        "manual_closes": stats["manual_closes"],
        "sl_hit_rate": sl_rate,
        "tp_hit_rate": tp_rate,
        "avg_r_per_sl": avg_r_sl,
        "avg_r_per_tp": avg_r_tp,
    }
