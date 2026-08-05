"""`journal live` — the single process that OWNS the bridge (M9 Phase 4).

Exactly one thing may hold the bridge connection and issue orders, and this is
it. The web never talks to MT5; it INSERTs a `pending` row into `trade_commands`
and this loop claims, sends, and records it. That split is what keeps CLAUDE.md
rules 1 and 12 literally true everywhere else in the codebase.

One cycle does five jobs, in this order and for a reason:

  1. **Mirror.** Fetch `positions_get()` ONCE and use that single list for
     everything downstream — the SL/TP snapshots (via the reused `poll_once`),
     the `open_positions` mirror, AND close detection. Fetching twice would risk
     the three consumers seeing three slightly different worlds a few ms apart.

  2. **Serve watches + beat.** `serve_watches` (forming bar + promote closed
     bars) and the liveness beacon. Deliberately BEFORE steps 3 and 4: those
     can block on a multi-second bridge round trip (ingest on close, order
     send), and this is what `/chart`'s live edge and the liveness indicator
     depend on — it must not sit behind that wait. Cheap enough to lead with:
     one latest-bars fetch per active watch, ~1 given demand-driven watching.
     This beat alone is not enough to survive a close, though: the ingest
     pipeline in step 3 can run long enough on its own to age this beat past
     the web's staleness threshold before the NEXT cycle ever gets a chance to
     beat again — so step 3 beats a second time right after the pipeline runs.

  3. **Detect closes → ingest.** A `position_id` that was in `open_positions` at
     the START of this cycle but is absent from the fresh feed has CLOSED. MT5
     drops a closed position from `positions_get()` forever (Trap 6), so this is
     the one moment we know to pull its finished deal. On any close(s) we run the
     full pipeline — sync → rebuild → candles → rebuild — ONCE, coalesced, no
     matter how many closed together. A failed ingest is caught and logged, never
     propagated: losing this loop loses unrecoverable live SL history, which is
     the whole reason M4 exists, so a transient bridge hiccup must not kill it.
     The beacon is beaten again immediately after the pipeline (success or
     failure) — see step 2's note on why one beat is not enough across a long
     ingest.

  4. **Execute one command.** If trading is on, claim the OLDEST pending command
     and run it through the same gate the web used at enqueue time — the world
     moves between enqueue and claim, so we re-validate. One command per cycle
     keeps the sequence serial and auditable.

  5. **Fulfil one candle request.** Claim the OLDEST pending row in
     `candle_requests` (queued by the web, never sent there directly — see
     CLAUDE.md rules 1/12) and run it through `candle_fill.fulfill_request`.
     Same one-per-cycle discipline as commands, so a large backfill can never
     starve the position heartbeat. LAST because `fill_range` can walk a whole
     requested range over several round trips and nothing user-facing waits on
     it, while an order does. A failed fetch is marked `failed` and logged,
     never re-raised past this loop — unlike a command, a candle fetch is
     idempotent and safe to just re-request, so it does not need the command
     queue's stricter "never auto-retry" refusal.

The single most important refusal (shared with `execute.recover_interrupted`):
an order that MAY have reached the broker is NEVER re-sent by a machine. If
`order_send` raises, the row stays `sent` — evidence of possible broker contact —
and the next startup's `recover_interrupted` marks it failed with an instruction
to check MT5 by hand. We do not mark it failed here and we do not re-send.

`open_positions` is CURRENT state, not history: it is replaced wholesale every
cycle (DELETE the account's rows, re-INSERT the live feed). `observed_msc` is
this poller's TRUE-UTC wall clock (`now_ms`); `open_time_msc` is broker SERVER
time. They are different clocks — never compare or subtract them (Trap 7). Money
columns (profit/swap) are in `accounts.currency` = USC; stored as-is.

`live_cycle` is the timing-free unit surface (mirrors `poll_once`); `live_loop`
wraps it in a sleep loop with an injectable clock (mirrors `poll_loop`) so
`--once`/`--duration`/Ctrl+C are all testable without a real wall-clock wait.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field

from ..adapter.base import MT5Client, Position
from ..domain.commands import CommandError, build_request
from ..domain.symbols import to_base
from ..execute import (
    account_balance,
    claim_next,
    load_context,
    load_open_context,
    mark_sent,
    pending_count,
    record_result,
    recover_interrupted,
    reject,
)
from ..store import live_store
from ..store.candle_queue import claim_next_request, requeue_orphaned
from ..store.db import now_ms
from .candle_fill import fulfill_request
from .live_candles import serve_watches
from .poller import poll_once

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveReport:
    account_login: int
    observed_msc: int
    positions_seen: int = 0
    snapshots_written: int = 0
    closed_ids: list[int] = field(default_factory=list)
    ingest_ran: bool = False              # pipeline attempted AND succeeded
    command_id: int | None = None         # the command this cycle acted on, if any
    command_status: str | None = None     # its resulting status ('done'/'failed'/…)
    candle_request_id: int | None = None
    candle_bars_written: int | None = None


@dataclass(frozen=True)
class LiveLoopReport:
    cycles: int = 0
    recovered: int = 0                    # orphans closed out at startup
    stopped_by: str = "duration"          # 'once' | 'duration' | 'interrupt'


def _direction(type_: int | None) -> str | None:
    """MT5 position `type`: 0 = buy, 1 = sell. Anything else is unknown — return
    None (the `open_positions.direction` CHECK allows NULL) rather than guess."""
    if type_ == 0:
        return "buy"
    if type_ == 1:
        return "sell"
    return None


def _open_position_ids(conn: sqlite3.Connection, login: int) -> set[int]:
    """The position_ids currently mirrored for this account. Read BEFORE the
    wholesale replace so close detection compares last cycle to this one."""
    rows = conn.execute(
        "SELECT position_id FROM open_positions WHERE account_login = ?", (login,)
    ).fetchall()
    return {int(r["position_id"]) for r in rows}


def _replace_open_positions(
    conn: sqlite3.Connection, login: int, positions: list[Position], observed_msc: int
) -> None:
    """Mirror the live feed wholesale: drop this account's rows and re-insert the
    current ones, all in one transaction so the table is never seen half-empty."""
    conn.execute("DELETE FROM open_positions WHERE account_login = ?", (login,))
    for p in positions:
        if p.identifier is None:
            continue  # malformed, same skip as poll_once
        conn.execute(
            "INSERT INTO open_positions "
            "(account_login, position_id, symbol, symbol_base, direction, volume, "
            " open_price, price_current, sl, tp, profit, swap, magic, "
            " open_time_msc, observed_msc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                login,
                p.identifier,
                p.symbol,
                to_base(p.symbol) if p.symbol is not None else None,
                _direction(p.type),
                p.volume,
                p.price_open,
                p.price_current,
                p.sl,
                p.tp,
                p.profit,
                p.swap,
                p.magic,
                p.time_msc,        # broker SERVER time -> open_time_msc (Trap 7)
                observed_msc,      # true UTC, when WE saw it
            ),
        )
    conn.commit()


def _run_ingest_pipeline(client: MT5Client, conn: sqlite3.Connection) -> None:
    """sync → rebuild → candles → rebuild, the documented on-close order.

    Rebuild twice on purpose: the first pass reconstructs the freshly-closed
    trade from the new deals; `sync_candles` then fetches the OHLC that spans it;
    the second rebuild folds MAE/MFE (which need those candles) into the trade.

    Imported lazily, mirroring cli.py: keeps this module importable with no
    bridge present, and lets a test monkeypatch each stage at its source module.

    `sync_candles` caps how many candle windows it fetches per run, so a large
    backlog drains across several closes rather than stalling this loop. What is
    left is logged, never re-raised.
    """
    from ..ingest.deals import sync as run_sync
    from ..domain.reconstruct import rebuild as run_rebuild
    from ..ingest import candles

    run_sync(client, conn)
    run_rebuild(conn)
    report = candles.sync_candles(client, conn)
    if report.windows_pending:
        log.info(
            "live: %d candle window(s) still pending after this ingest — capped at "
            "%d per close so the forming bar keeps streaming; run `journal candles` "
            "to drain the backlog in one go",
            report.windows_pending,
            candles._MAX_FETCH_WINDOWS,
        )
    run_rebuild(conn)


def _open_price_for(client: MT5Client, symbol: str, price_ref: float | None) -> float | None:
    """The price an OPEN is re-validated against at send time.

    A fresh tick, because the market has moved since the human sized the order
    and the SL may now be on the wrong side — the one failure this re-check
    exists to catch. Falls back to the stored `price_ref` when the bridge cannot
    answer: a stale price still catches a gross error, and refusing every order
    whenever a tick call hiccups would be its own kind of trap.

    This is the ingest layer, so calling the client here is allowed (rules 1
    and 12 bind `web/` and `domain/`).
    """
    try:
        tick = client.symbol_info_tick(symbol)
    except Exception:
        log.warning("live: no fresh tick for %s — re-validating against price_ref", symbol)
        return price_ref
    if tick is None:
        return price_ref
    # Mid of bid/ask; either alone biases the side-check by the spread. Falls
    # back through last, then price_ref — rule 4 all the way down.
    if tick.bid is not None and tick.ask is not None:
        return (tick.bid + tick.ask) / 2.0
    if tick.last is not None:
        return tick.last
    return price_ref


def _execute_one_command(
    client: MT5Client, conn: sqlite3.Connection, login: int
) -> tuple[int | None, str | None]:
    """Claim and run the single oldest pending command, or do nothing.

    Returns (command_id, status). The lifecycle is deliberately fussy because
    every step is about real money:
      * `load_context`/`build_request` raising CommandError => `reject` (the world
        moved since enqueue: position closed, spec changed). Never sent.
      * an `open` has no position to load, so it re-validates against a FRESH
        tick — if the market crossed the stop while the command sat in the
        queue, the SL is now on the wrong side and the order is rejected, not
        sent.
      * `order_check` is a broker-side dry run first; then `mark_sent` COMMITS
        before `order_send`, so a crash mid-flight leaves the row as evidence.
      * if `order_send` (or `order_check`) raises, we catch, log loudly, and
        LEAVE the row where it is — a `sent` row is never re-sent here; the next
        startup's `recover_interrupted` deals with it. We must not guess success,
        must not mark it failed, and must not re-send.
    """
    row = claim_next(conn, login)
    if row is None:
        return None, None

    cmd_id = int(row["id"])

    try:
        if row["kind"] == "open":
            price = _open_price_for(client, row["symbol"], row["price_ref"])
            pos, spec = load_open_context(
                conn, login, row["symbol"], row["direction"], price
            )
            req = build_request(
                "open", pos, spec, sl=row["sl"], tp=row["tp"], volume=row["volume"],
                balance=account_balance(conn, login),
            )
        else:
            pos, spec = load_context(conn, login, row["position_id"])
            req = build_request(
                row["kind"], pos, spec, sl=row["sl"], tp=row["tp"], volume=row["volume"]
            )
    except CommandError as e:
        # Valid when queued, not now. Refuse WITHOUT sending.
        reject(conn, cmd_id, str(e))
        log.info("live: command %d rejected — %s", cmd_id, e)
        return cmd_id, "rejected"

    try:
        client.order_check(req)     # dry run; the bridge logs its verdict
        mark_sent(conn, cmd_id)     # committed BEFORE the real send (evidence)
        result = client.order_send(req)
    except Exception:
        # The bridge died somewhere in check/send. If we got past mark_sent the
        # row is 'sent' and MAY exist at the broker; if not it is 'claimed'.
        # Either way we do NOT re-send and do NOT invent an outcome — startup
        # recovery marks it failed with an instruction to check MT5 by hand.
        log.exception(
            "live: bridge raised while sending command %d — NOT retried; "
            "check MT5 before re-sending", cmd_id
        )
        status = conn.execute(
            "SELECT status FROM trade_commands WHERE id = ?", (cmd_id,)
        ).fetchone()["status"]
        return cmd_id, status

    status = record_result(conn, cmd_id, result)
    log.info("live: command %d -> %s", cmd_id, status)
    return cmd_id, status


def live_cycle(
    client: MT5Client,
    conn: sqlite3.Connection,
    login: int,
    *,
    trading: bool = True,
    on_closing=None,
    on_close=None,
) -> LiveReport:
    """One live cycle: mirror open positions, ingest any that closed, and (if
    `trading`) execute one queued command. Timing-free — this is the unit surface.

    `on_closing`, if given, is called with the closed position_ids the MOMENT a
    close is detected, BEFORE the ingest pipeline runs. That pipeline is a
    synchronous bridge round-trip (sync → candles) that can block the loop — and
    therefore the heartbeat — for several seconds; announcing it first is what
    stops that pause from reading as a freeze (reported the first time this ran
    live). `on_close` fires AFTER the ingest, with the same ids.
    """
    positions = client.positions_get()
    observed_msc = now_ms()

    # (1) SL/TP snapshots — reuse poll_once with the SAME fetched list so
    # positions_get() is called exactly once this cycle.
    poll_report = poll_once(client, conn, login, positions=positions)

    # (2) close detection reads the PRIOR mirror before we overwrite it.
    prior_ids = _open_position_ids(conn, login)
    live_ids = {int(p.identifier) for p in positions if p.identifier is not None}
    closed_ids = sorted(prior_ids - live_ids)

    _replace_open_positions(conn, login, positions, observed_msc)

    # (3) forming bar + promote closed bars, then the beacon. FIRST, ahead of
    # the two steps below that can block on a multi-second bridge round trip
    # (ingest pipeline on close, order send). Reported: adding an SL/TP or a
    # position closing froze /chart for however long that round trip took,
    # because this used to run after them and the whole cycle is one serial
    # call. Cheap enough to lead with — one latest-bars fetch per active watch,
    # ~1 given demand-driven watching. The BULK candle fetch stays at step (6),
    # behind order send: a backfill has no deadline, an order does.
    serve_watches(client, conn, observed_msc)

    # (4) liveness beacon — ALWAYS, even with no positions/watches, so the web can
    # tell "journal live is running" from "data is just old". Empty open_positions
    # cannot serve as a heartbeat (no rows when nothing is open).
    live_store.beat(conn, now_ms())

    # (5) detect closes → ingest.
    ingest_ran = False
    if closed_ids:
        log.info("live: %d position(s) closed: %s", len(closed_ids), closed_ids)
        if on_closing is not None:
            on_closing(closed_ids)   # BEFORE the blocking ingest — see docstring
        try:
            _run_ingest_pipeline(client, conn)   # ONCE, coalesced across closes
            ingest_ran = True
        except Exception:
            # A failed ingest must NOT kill the loop — losing the loop loses
            # unrecoverable live SL history. Catch, log, carry on.
            log.exception(
                "live: ingest pipeline failed after close(s) %s — loop continues",
                closed_ids,
            )
        finally:
            # The pipeline (sync + two rebuilds + capped candle fetch) can run
            # long enough to age the step-4 beat past the web's staleness
            # threshold before this cycle even finishes, let alone before the
            # next one beats — which reads as "journal live is down" while it
            # is in fact working. Beat again here, unconditionally: a failed
            # ingest is still a live process. This does not replace the step-4
            # beat, which must still fire BEFORE this blocking work starts.
            live_store.beat(conn, now_ms())
        if on_close is not None:
            on_close(closed_ids)

    # (6) one command per cycle.
    command_id: int | None = None
    command_status: str | None = None
    if trading:
        command_id, command_status = _execute_one_command(client, conn, login)

    # (7) one candle request per cycle — same one-per-cycle discipline as
    # commands, so a big backfill can never starve the position heartbeat. LAST
    # on purpose: `fill_range` can walk a whole requested range over several
    # bridge round trips, and nothing user-facing waits on it (the request stays
    # queued and the forming bar is already served at step 3), whereas an SL/TP
    # or close command must not queue behind bulk history. This is the ONLY
    # place a browser-triggered candle fetch reaches the bridge.
    candle_request_id: int | None = None
    candle_bars_written: int | None = None
    req = claim_next_request(conn)
    if req is not None:
        candle_request_id = int(req["id"])
        try:
            candle_bars_written = fulfill_request(client, conn, req, observed_msc)
        except Exception:
            log.exception(
                "live: candle request %d failed — marked failed, will not auto-retry "
                "this exact row (a new request re-queues)", candle_request_id
            )

    return LiveReport(
        account_login=login,
        observed_msc=observed_msc,
        positions_seen=poll_report.positions_seen,
        snapshots_written=poll_report.snapshots_written,
        closed_ids=closed_ids,
        ingest_ran=ingest_ran,
        command_id=command_id,
        command_status=command_status,
        candle_request_id=candle_request_id,
        candle_bars_written=candle_bars_written,
    )


def live_loop(
    client: MT5Client,
    conn: sqlite3.Connection,
    login: int,
    *,
    interval_idle: float = 5.0,
    interval_busy: float = 1.0,
    trading: bool = True,
    once: bool = False,
    duration: float | None = None,
    sleep=time.sleep,
    monotonic=time.monotonic,
    on_cycle=None,
    on_closing=None,
) -> LiveLoopReport:
    """Repeatedly run `live_cycle`. `recover_interrupted` runs ONCE before the
    first cycle — a `claimed`/`sent` row on startup means a crash mid-command and
    is marked failed, never re-sent.

    After each cycle the next sleep is `interval_busy` when a command is pending
    (be responsive) else `interval_idle`. `sleep`/`monotonic` are injectable so
    `--once`/`--duration`/Ctrl+C are all testable with a fake clock. Always runs
    at least one cycle; `once` beats `duration`; the deadline is checked after a
    cycle and before the next sleep, so a `duration` run never sleeps after its
    final cycle. Ctrl+C stops cleanly with `stopped_by='interrupt'`.
    """
    recovered = recover_interrupted(conn, login)
    if recovered:
        log.info("live: recovered %d interrupted command(s) at startup", recovered)
    requeued = requeue_orphaned(conn)
    if requeued:
        log.info("live: requeued %d orphaned candle request(s) at startup", requeued)

    cycles = 0
    deadline = monotonic() + duration if duration is not None else None

    try:
        while True:
            r = live_cycle(client, conn, login, trading=trading, on_closing=on_closing)
            cycles += 1
            if on_cycle is not None:
                on_cycle(r)
            if once:
                return LiveLoopReport(cycles=cycles, recovered=recovered, stopped_by="once")
            if deadline is not None and monotonic() >= deadline:
                return LiveLoopReport(
                    cycles=cycles, recovered=recovered, stopped_by="duration"
                )
            interval = interval_busy if pending_count(conn, login) > 0 else interval_idle
            sleep(interval)
    except KeyboardInterrupt:
        return LiveLoopReport(cycles=cycles, recovered=recovered, stopped_by="interrupt")
