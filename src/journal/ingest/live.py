"""`journal live` — the single process that OWNS the bridge (M9 Phase 4).

Exactly one thing may hold the bridge connection and issue orders, and this is
it. The web never talks to MT5; it INSERTs a `pending` row into `trade_commands`
and this loop claims, sends, and records it. That split is what keeps CLAUDE.md
rules 1 and 12 literally true everywhere else in the codebase.

The work is SPLIT ACROSS TWO THREADS with independent intervals. They share one
bridge connection (serialized by `ingest.locked_client.LockedMT5Client`), take a
SQLite connection each, and are connected by nothing else but one in-memory
`closed_queue`. Neither waits on the other; there is no shared ordering left to
reason about. `cli.live` starts both.

**The position side — `position_cycle` / `position_loop`. Must stay fast.**

  1. **Mirror.** Fetch `positions_get()` ONCE and use that single list for
     everything downstream — the SL/TP snapshots (via the reused `poll_once`),
     the `open_positions` mirror, AND close detection. Fetching twice would risk
     the three consumers seeing three slightly different worlds a few ms apart.

  2. **Beat.** The liveness beacon, written every cycle whether or not anything
     is open — an empty `open_positions` cannot serve as a heartbeat. Exactly
     ONE beat per cycle: before the split there was a second one after the
     inline ingest, because that pipeline could age the first beat past the
     web's staleness threshold. The ingest is on the other thread now and cannot
     block this beat, so the second beat is gone.

  3. **Paper step,** then **detect closes → QUEUE.** A `position_id` that was in
     `open_positions` at the START of this cycle but is absent from the fresh
     feed has CLOSED. MT5 drops a closed position from `positions_get()` forever
     (Trap 6), so this is the one moment we know to pull its finished deal — but
     this cycle does not pull it. It pushes the ids onto `closed_queue` and moves
     on; the symbol side ingests them on its next tick. That handoff is the whole
     point of the split: a multi-second sync/candles round trip used to block the
     beacon and the command below it.

  4. **Expire, then execute one command.** `expire_stale` first: a command that
     has been queued longer than `STALE_PENDING_S` with nothing executing it is
     refused unsent, because everything it was validated against (`price_ref`,
     the stop distance, the feed-freshness verdict) was measured at enqueue time
     and has a shelf life. That runs whether or not trading is on — with trading
     off it is the only thing that ever clears the row. Then, if trading is on,
     claim the OLDEST pending command and run it through the same gate the web
     used at enqueue time — the world moves between enqueue and claim, so we
     re-validate. One command per cycle keeps the sequence serial and auditable.

**The symbol side — `symbol_cycle` / `symbol_loop`. Allowed to be slow.**

  1. **Serve watches.** `serve_watches` (forming bar + promote closed bars) —
     what `/chart`'s live edge depends on. One latest-bars fetch per active
     watch, ~1 given demand-driven watching.

  2. **Drain `closed_queue` → ingest.** Everything queued since the last tick,
     coalesced into ONE pipeline run (sync → rebuild → candles → rebuild) no
     matter how many positions closed. A failed ingest is caught and logged,
     never propagated: losing this loop loses nothing unrecoverable, but the
     position loop is unaffected either way. This is also why `symbol_loop`
     checks its stop event only AFTER a cycle — stopping without one last drain
     leaves that close un-ingested until the next close (or `journal sync`)
     triggers a run.

  3. **Fulfil one candle request.** Claim the OLDEST pending row in
     `candle_requests` (queued by the web, never sent there directly — see
     CLAUDE.md rules 1/12) and run it through `candle_fill.fulfill_request`.
     One per cycle, so a large backfill cannot starve the forming bar. A failed
     fetch is marked `failed` and logged, never re-raised past this loop —
     unlike a command, a candle fetch is idempotent and safe to just re-request,
     so it does not need the command queue's stricter "never auto-retry"
     refusal.

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

`position_cycle` and `symbol_cycle` are the timing-free unit surfaces (they
mirror `poll_once`); `position_loop` and `symbol_loop` wrap them in sleep loops
with an injectable clock (mirroring `poll_loop`) so `--once`/`--duration`/Ctrl+C
are all testable without a real wall-clock wait. `journal live` runs the two
loops on two threads with independent intervals — a close detected by
`position_cycle` crosses to `symbol_cycle` on an in-memory queue, so an ingest
round-trip never blocks the position mirror or command execution.
"""

from __future__ import annotations

import logging
import queue as queue_mod
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from ..adapter.base import MT5Client, Position
from ..domain import paper_eval as pe
from ..domain import replay_eval as rev
from ..domain.commands import CommandError, build_request
from ..domain.symbols import to_base
from ..execute import (
    STALE_PENDING_S,
    account_balance,
    claim_next,
    expire_stale,
    load_context,
    load_open_context,
    mark_sent,
    pending_count,
    record_result,
    recover_interrupted,
    reject,
)
from ..store import backup, health, live_store, paper_store
from ..store.candle_queue import claim_next_request, requeue_orphaned
from ..store.db import now_ms
from .candle_fill import fulfill_request
from .live_candles import serve_watches
from .poller import poll_once

log = logging.getLogger(__name__)

# Consecutive "database is locked" cycles before a loop gives up and lets the
# error out to `cli.live` (which prints "run only one `journal live`" and exits).
# This process now holds TWO writer connections on one DB (conn_positions,
# conn_symbols), so ONE locked cycle no longer implies a second daemon — a long
# symbol-side write (a big `candle_fill.fill_range` backfill) can push the other
# thread past the 5 s busy_timeout on its own. That clears in milliseconds; a
# second `journal live` does not clear at all and hits this on every cycle, so
# it still exits within LOCKED_STREAK_EXIT × interval (a few seconds).
LOCKED_STREAK_EXIT = 3


@dataclass(frozen=True)
class PositionLoopReport:
    cycles: int = 0
    recovered: int = 0                    # orphans closed out at startup
    failed_cycles: int = 0                # cycles that raised (bridge gone, etc)
    stopped_by: str = "duration"          # 'once' | 'duration' | 'interrupt'


@dataclass(frozen=True)
class PositionCycleReport:
    account_login: int
    observed_msc: int
    positions_seen: int = 0
    snapshots_written: int = 0
    closed_ids: list[int] = field(default_factory=list)
    command_id: int | None = None         # the command this cycle acted on, if any
    command_status: str | None = None     # its resulting status ('done'/'failed'/…)
    paper_resolved: int = 0


@dataclass(frozen=True)
class SymbolCycleReport:
    observed_msc: int
    forming_bars_written: int = 0
    ingest_ran: bool = False
    closed_ids: list[int] = field(default_factory=list)
    candle_request_id: int | None = None
    candle_bars_written: int | None = None


@dataclass(frozen=True)
class SymbolLoopReport:
    cycles: int = 0
    requeued: int = 0                     # orphaned candle requests requeued at startup
    failed_cycles: int = 0                # cycles that raised (bridge gone, etc)
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

    t0 = time.monotonic()
    r = run_sync(client, conn)
    # Logged because the last time this pipeline froze for ~4 minutes, the only way
    # to find out which stage owned the time was reconstructing it from row
    # timestamps afterwards. `history_from_msc` is 0 on a full pull — seeing that
    # here means the watermark window fell back, which is itself the answer.
    log.info(
        "live: deals sync %.1fs — %d new / %d deals seen, history from %d",
        time.monotonic() - t0, r.deals_new, r.deals_seen, r.history_from_msc,
    )
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


def _paper_specs(conn: sqlite3.Connection, symbol: str) -> pe.Specs | None:
    row = conn.execute(
        "SELECT tick_size, tick_value, contract_size, currency_profit "
        "FROM symbol_specs WHERE symbol = ?", (symbol,)
    ).fetchone()
    if row is None or row["tick_size"] in (None, 0) or row["tick_value"] in (None, 0):
        return None
    return pe.Specs(
        tick_size=float(row["tick_size"]), tick_value=float(row["tick_value"]),
        contract_size=float(row["contract_size"] or 1.0),
        currency_profit=row["currency_profit"] or "",
    )


def _as_state(row: sqlite3.Row) -> pe.PaperPos:
    return pe.PaperPos(
        id=row["id"], symbol=row["symbol"], direction=row["direction"],
        order_kind=row["order_kind"], request_price=row["request_price"],
        volume=row["volume"], sl=row["sl"] or 0.0, tp=row["tp"] or 0.0,
        status=row["status"], entry_price=row["entry_price"],
        entry_msc=row["entry_msc"], expires_msc=row["expires_msc"],
    )


def _persist_exit(conn: sqlite3.Connection, row: sqlite3.Row, ev: pe.Event,
                  specs: pe.Specs | None) -> None:
    """Write one closed slice: money from the specs, R from `sl_initial`, and the
    account's realized balance moved by exactly that money. MAE/MFE stay NULL
    here — they need cached candle rows, and the close path must not block the
    daemon on a candle read; `web/paper.py` fills them when the panel asks.

    `specs` may be None — a symbol can fill and exit on price alone (rule 4's
    "no guess" only bites the MONEY math). `net_profit` then stays NULL/unknown
    rather than coerced to 0; R still resolves independently, since it needs
    only entry/exit/sl, not the symbol's tick value."""
    entry = row["entry_price"]
    net = None if entry is None or specs is None else rev.net_profit_usc(
        row["direction"], entry, ev.price, row["volume"],
        specs.tick_size, specs.tick_value,
    )
    r = None
    if entry is not None and row["sl_initial"] is not None:
        r = rev.r_multiple(row["direction"], entry, ev.price, row["sl_initial"])
    paper_store.mark_close(
        conn, row["id"], exit_msc=ev.time_msc, exit_price=ev.price,
        exit_reason=ev.reason, net_profit=net, r_multiple=r,
        mae=None, mfe=None, mae_r=None, mfe_r=None,
    )
    if net is not None:
        paper_store.add_balance(conn, row["account_id"], net)


def paper_step(client: MT5Client, conn: sqlite3.Connection, *,
               now_msc: int) -> int:
    """Advance every live paper position by one tick, and return how many
    DISTINCT positions were resolved this cycle — closed or expired. A fill
    alone does not count: it starts a position's life, it does not conclude it.
    Counted by position id, not by event: a pending order that fills and then
    gaps through its stop on the SAME tick produces two events (fill, exit) for
    one position, and must report 1, not 2 — this value surfaces to a human
    through `PositionCycleReport.paper_resolved` and must not lie about how many
    positions it touched.

    Zero exposure means zero bridge calls: the symbol list comes from the DB
    first. But "exposure" isn't only an open/pending position — with at least
    one active paper account, the symbol the chart is currently watching
    (`live_watches`) counts too, or `place_order` could never accept a FIRST
    order on any symbol: `live_quotes` would never have a row for it, and
    `_fresh_quote` refuses on a missing row rather than guessing a price. No
    active paper account at all means paper trading isn't in use, so a watched
    chart symbol alone still costs zero bridge calls. A bridge failure is
    logged and the step returns — losing the loop loses unrecoverable live SL
    history, and no simulated account is worth that.

    Runs regardless of `trading`: paper is not real trading.

    A position can only be resolved once, on both axes:
      * WITHIN this cycle, `states` (mutable `PaperPos`s) are shared between
        `step_tick` and `resolve_stopout` — once a position's in-memory status
        flips to "closed"/"expired", both functions' own status guards exclude
        it from further processing on the very same pass, so the event list
        contains at most one exit per position this cycle.
      * ACROSS cycles/restarts, each cycle re-reads `list_positions(...,
        statuses=("pending","open"))` fresh from the DB. `mark_fill`/
        `mark_close` flip that status column, so a row closed by a prior cycle
        (or a prior process, before a restart) never appears in `rows`/`states`
        again — it is structurally excluded from being handed to `mark_close`
        a second time, which is the hazard `mark_close` itself does not guard.
    """
    symbols = set(paper_store.open_or_pending_symbols(conn))
    if paper_store.list_accounts(conn, status="active"):
        symbols |= {s for s, _tf in live_store.active_watches(conn, now_msc)}
    if not symbols:
        return 0

    quotes: dict[str, pe.Quote] = {}
    specs: dict[str, pe.Specs] = {}
    for symbol in symbols:
        try:
            tick = client.symbol_info_tick(symbol)
        except Exception:
            log.exception("paper: tick fetch failed for %s — step skipped", symbol)
            return 0
        if tick is None or tick.bid is None or tick.ask is None:
            continue
        # A fill/SL/TP trigger needs only the quote — no specs required. Specs
        # are needed ONLY for the money math on a close (rule 4: unknown specs
        # must not block a fill, they must only leave net_profit/margin NULL).
        tick_msc = tick.time_msc or now_msc
        live_store.upsert_quote(conn, symbol, bid=float(tick.bid),
                                ask=float(tick.ask), tick_msc=tick_msc,
                                now_msc=now_msc)
        quotes[symbol] = pe.Quote(symbol=symbol, bid=float(tick.bid),
                                  ask=float(tick.ask), time_msc=tick_msc)
        spec = _paper_specs(conn, symbol)
        if spec is not None:
            specs[symbol] = spec

    resolved_ids: set[int] = set()
    for account in paper_store.list_accounts(conn, status="active"):
        rows = {r["id"]: r for r in paper_store.list_positions(
            conn, account["id"], statuses=("pending", "open"))}
        states = [_as_state(r) for r in rows.values()]
        if not states:
            continue

        events: list[pe.Event] = []
        for quote in quotes.values():
            events.extend(pe.step_tick(states, quote, now_msc))
        events.extend(pe.resolve_stopout(
            states, quotes, specs, balance=float(account["balance"]),
            stopout_pct=float(account["stopout_pct"]),
            leverage=int(account["leverage"]), now_msc=now_msc,
        ))

        for ev in events:
            row = rows[ev.position_id]
            if ev.kind == "fill":
                paper_store.mark_fill(
                    conn, ev.position_id, entry_msc=ev.time_msc,
                    entry_price=ev.price,
                    sl_initial=(row["sl"] if row["sl"] and row["sl"] > 0 else None),
                )
            elif ev.kind == "expire":
                paper_store.update_status(conn, ev.position_id, "expired")
                resolved_ids.add(ev.position_id)
            else:
                # Re-read: a fill earlier in this same loop wrote the entry price
                # this exit's money depends on. specs.get(...): None is a valid,
                # meaningful value here (unknown spec), not a bug.
                fresh = paper_store.get_position(conn, ev.position_id)
                _persist_exit(conn, fresh, ev, specs.get(fresh["symbol"]))
                resolved_ids.add(ev.position_id)

    return len(resolved_ids)


def position_cycle(
    client: MT5Client,
    conn: sqlite3.Connection,
    login: int,
    closed_queue: queue_mod.Queue[list[int]],
    *,
    trading: bool = True,
    on_closing=None,
) -> PositionCycleReport:
    """The fast half of the split live loop: mirror open positions, detect
    closes, and (if `trading`) execute one queued command. Timing-free — this is
    the unit surface (mirrors `poll_once`; `symbol_cycle` is the other half).

    A closed position is no longer ingested inline: its id is pushed onto
    `closed_queue` for the (separately scheduled) symbol side to drain, so a
    multi-second sync/candle round trip never blocks this cycle's beacon beat
    or command execution. `on_closing`, if given, is called with the closed
    position_ids the MOMENT a close is detected, before they are queued.
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

    # (3) liveness beacon — ALWAYS, even with no positions, so the web can
    # tell "journal live is running" from "data is just old". Empty
    # open_positions cannot serve as a heartbeat (no rows when nothing is open).
    live_store.beat(conn, now_ms())

    # (3b) paper trading. AHEAD of the order send below, which can block for
    # seconds on a bridge round trip: a paper SL has a deadline the same way an
    # order does. Zero cost when no paper position is live, and it runs with
    # `trading` off — paper is not real trading.
    try:
        paper_resolved = paper_step(client, conn, now_msc=observed_msc)
    except Exception:
        log.exception("paper: step failed — loop continues")
        paper_resolved = 0

    # (4) detect closes → hand off to the symbol side. MT5 drops a closed
    # position from `positions_get()` forever (Trap 6), so this is the one
    # moment we know to queue its finished deal for ingest.
    if closed_ids:
        log.info("live: %d position(s) closed: %s", len(closed_ids), closed_ids)
        if on_closing is not None:
            on_closing(closed_ids)
        closed_queue.put(closed_ids)

    # (5) refuse anything that has been queued too long, THEN execute one
    # command. Ahead of the claim on purpose: a stale row must not be the one
    # this cycle sends. Runs with `trading` off too — that is precisely the
    # mode in which nothing else ever clears the row.
    expired = expire_stale(conn, login)
    if expired:
        log.warning(
            "live: %d queued command(s) expired unsent — older than %.0fs with "
            "nothing executing them; re-queue from /live if still wanted",
            expired, STALE_PENDING_S,
        )

    command_id: int | None = None
    command_status: str | None = None
    if trading:
        command_id, command_status = _execute_one_command(client, conn, login)

    return PositionCycleReport(
        account_login=login,
        observed_msc=observed_msc,
        positions_seen=poll_report.positions_seen,
        snapshots_written=poll_report.snapshots_written,
        closed_ids=closed_ids,
        command_id=command_id,
        command_status=command_status,
        paper_resolved=paper_resolved,
    )


def symbol_cycle(
    client: MT5Client,
    conn: sqlite3.Connection,
    closed_queue: queue_mod.Queue[list[int]],
) -> SymbolCycleReport:
    """One symbol-data cycle: serve forming bars, drain any closed-position ids
    handed off by position_cycle and run the ingest pipeline once for all of
    them coalesced, then fulfil one queued candle backfill request.

    Runs on its own interval, independent of position_cycle's — see
    docs/superpowers/specs/2026-09-04-decouple-live-loop-design.md. A close
    detected by position_cycle is ingested here on THIS loop's next tick, not
    inline in the cycle that detected it: that's the whole point of the split.
    """
    observed_msc = now_ms()

    forming_bars_written = serve_watches(client, conn, observed_msc)

    closed_ids: list[int] = []
    while True:
        try:
            closed_ids.extend(closed_queue.get_nowait())
        except queue_mod.Empty:
            break

    ingest_ran = False
    if closed_ids:
        log.info("live: symbol_cycle ingesting closed position(s): %s", closed_ids)
        try:
            _run_ingest_pipeline(client, conn)
            ingest_ran = True
        except Exception:
            log.exception(
                "live: ingest pipeline failed for closed position(s) %s — "
                "symbol_cycle continues, position_cycle is unaffected", closed_ids,
            )

    candle_request_id: int | None = None
    candle_bars_written: int | None = None
    req = claim_next_request(conn)
    if req is not None:
        candle_request_id = int(req["id"])
        try:
            candle_bars_written = fulfill_request(client, conn, req, observed_msc)
        except Exception:
            log.exception(
                "live: candle request %d failed — marked failed, will not "
                "auto-retry this exact row", candle_request_id,
            )

    return SymbolCycleReport(
        observed_msc=observed_msc,
        forming_bars_written=forming_bars_written,
        ingest_ran=ingest_ran,
        closed_ids=closed_ids,
        candle_request_id=candle_request_id,
        candle_bars_written=candle_bars_written,
    )


def _maybe_backup(conn: sqlite3.Connection, login: int, every_s: float, keep: int) -> None:
    """Snapshot the DB if one has not been taken in `every_s` — the reason this
    lives in the loop at all is that `journal backup` only runs when a human
    remembers, and the measured rate at which humans remember is three times a
    year. This is the only long-lived process in the project.

    Two refusals. It never runs while a trade command is pending: the copy runs
    in this thread and a 60 MB pager copy must not sit in front of an SL/TP or a
    close. And it never propagates — a full disk is a bad backup, but a `journal
    live` that exits because of one is worse.

    A row that stays `pending` still defers the snapshot — but only until
    `expire_stale` refuses it (`STALE_PENDING_S`, one cycle earlier than this
    runs), so the deferral is now bounded by minutes instead of by however long
    a human takes to notice. That bound is why this check needs no escape hatch
    of its own.
    """
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    if not path:                                   # :memory: — nothing to copy
        return
    if pending_count(conn, login) > 0 or not backup.due(path, every_s):
        return
    try:
        s = backup.snapshot(path, keep=keep)
    except Exception:
        log.exception("live: auto-backup failed — the loop continues, the DB is unbacked")
        return
    if s.integrity != "ok":
        log.error("live: auto-backup %s failed integrity_check (%s) — CHECK THE SOURCE DB",
                  s.out.name, s.integrity)
    else:
        log.info("live: auto-backup %s (%d trades)%s", s.out.name, s.n_trades,
                 f", pruned {len(s.pruned)}" if s.pruned else "")


def position_loop(
    client: MT5Client,
    conn: sqlite3.Connection,
    login: int,
    closed_queue: queue_mod.Queue[list[int]],
    *,
    interval_idle: float = 5.0,
    interval_busy: float = 1.0,
    trading: bool = True,
    once: bool = False,
    duration: float | None = None,
    backup_every_s: float | None = 86_400.0,
    backup_keep: int = 7,
    sleep=time.sleep,
    monotonic=time.monotonic,
    on_cycle=None,
    on_closing=None,
    stop_event: threading.Event | None = None,
) -> PositionLoopReport:
    """Repeatedly run `position_cycle` — the fast half of the split live loop
    (`symbol_loop` is the slow half). `recover_interrupted` runs
    ONCE before the first cycle — a `claimed`/`sent` row on startup means a crash
    mid-command and is marked failed, never re-sent.

    After each cycle the next sleep is `interval_busy` when a command is pending
    (be responsive) else `interval_idle`. `sleep`/`monotonic` are injectable so
    `--once`/`--duration`/Ctrl+C are all testable with a fake clock. Runs at
    least one cycle unless `stop_event` is already set; `once` beats `duration`;
    the deadline is checked after a cycle and before the next sleep, so a
    `duration` run never sleeps after its final cycle. Ctrl+C stops cleanly with
    `stopped_by='interrupt'` — `stop_event` does the same from outside this
    thread: `cli.live` runs this loop on a thread that never receives the
    SIGINT-driven `KeyboardInterrupt` directly, so its signal handling sets
    `stop_event` to reach it. That event is checked at the TOP of the loop as
    well as before the sleep: the interrupt normally arrives WHILE this thread
    sleeps, and one further cycle would send a queued order after the human
    asked to stop.

    A cycle that RAISES does not end the loop: it is rolled back, counted in
    `failed_cycles`, and retried on the next tick. Nothing beats the heartbeat on
    that path on purpose — the process is up but cannot see the broker, and a
    beat would tell `/live` and `journal status` that everything is fine while
    the position mirror sits frozen. "live down" is the more honest of the two
    available lies; the log line says which one it is.

    Every cycle also asks `_maybe_backup` whether the DB is due a snapshot
    (`backup_every_s=None` turns that off). It is the only thing here that is
    not about the bridge, and it is here because this is the only process that
    runs all day — see that function.
    """
    live_store.mark_started(conn, now_ms(), health.code_fingerprint())
    recovered = recover_interrupted(conn, login)
    if recovered:
        log.info("live: recovered %d interrupted command(s) at startup", recovered)

    cycles = 0
    failed = 0          # total, reported
    streak = 0          # consecutive, resets on the first good cycle
    locked_streak = 0   # consecutive "locked" ones — see LOCKED_STREAK_EXIT
    deadline = monotonic() + duration if duration is not None else None

    def _report(stopped_by: str) -> PositionLoopReport:
        return PositionLoopReport(
            cycles=cycles, recovered=recovered, failed_cycles=failed,
            stopped_by=stopped_by,
        )

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                # BEFORE the cycle, not only after it: the event is normally set
                # by Ctrl+C on the main thread WHILE this one sleeps, and the
                # cycle this would otherwise run first can send a queued order.
                # A stop must not put money on the market.
                return _report("interrupt")
            try:
                r = position_cycle(
                    client, conn, login, closed_queue, trading=trading, on_closing=on_closing,
                )
            except Exception as e:
                # The bridge went away mid-cycle (container restart, refused
                # connection) — the single most likely way this process dies, and
                # dying is the expensive outcome: live SL/TP history is the one
                # thing here that CANNOT be re-synced later (Trap 16), and the
                # daily snapshot only exists inside this loop. Log, sleep, retry.
                conn.rollback()   # never carry a half-written cycle's WAL writer
                                  # slot into the sleep — `journal serve` blocks
                                  # on it, and this project has paid for that twice
                if isinstance(e, sqlite3.OperationalError) and "locked" in str(e).lower():
                    # An ISOLATED locked cycle is now expected: this process holds
                    # two writer connections, and a long symbol-side write can push
                    # this one past the busy_timeout for a moment. What a second
                    # `journal live` looks like is PERSISTENCE — it locks us out on
                    # every cycle, forever. So only a streak escapes, and then
                    # `cli.live` says so and exits; below the threshold this is
                    # just another transient failure, logged and retried.
                    locked_streak += 1
                    if locked_streak >= LOCKED_STREAK_EXIT:
                        raise
                else:
                    locked_streak = 0
                failed += 1
                streak += 1
                if streak == 1:
                    log.exception("live: cycle failed — retrying, the loop stays up")
                elif streak % 60 == 0:
                    log.warning("live: cycle still failing, %d in a row", streak)
            else:
                if streak:
                    log.info("live: recovered after %d failed cycle(s)", streak)
                    streak = 0
                locked_streak = 0
                if on_cycle is not None:
                    on_cycle(r)
            cycles += 1
            if backup_every_s is not None:
                _maybe_backup(conn, login, backup_every_s, backup_keep)
            if once:
                return _report("once")
            if deadline is not None and monotonic() >= deadline:
                return _report("duration")
            if stop_event is not None and stop_event.is_set():
                return _report("interrupt")
            interval = interval_busy if pending_count(conn, login) > 0 else interval_idle
            sleep(interval)
    except KeyboardInterrupt:
        return _report("interrupt")


def symbol_loop(
    client: MT5Client,
    conn: sqlite3.Connection,
    closed_queue: queue_mod.Queue[list[int]],
    *,
    interval: float = 5.0,
    once: bool = False,
    duration: float | None = None,
    sleep=time.sleep,
    monotonic=time.monotonic,
    on_cycle=None,
    stop_event: threading.Event | None = None,
) -> SymbolLoopReport:
    """Repeatedly run `symbol_cycle` — the slow half of the split live loop
    (mirrors `position_loop`, which repeats `position_cycle`). `requeue_orphaned`
    runs ONCE at startup: a candle request left `claimed` by a crashed process is
    put back on the queue instead of sitting stuck forever.

    Simpler than `position_loop`: one flat `interval` (no busy/idle split — there
    is no `pending_count` here, that's position-domain), no backup call, no
    `recover_interrupted` (order recovery is position-domain too). `sleep`/
    `monotonic` are injectable so `--once`/`--duration`/Ctrl+C are all testable
    with a fake clock. Always runs at least one cycle; `once` beats `duration`;
    the deadline is checked after a cycle and before the next sleep, so a
    `duration` run never sleeps after its final cycle. Ctrl+C stops cleanly with
    `stopped_by='interrupt'` — `stop_event` does the same from outside this
    thread: `cli.live` runs this loop on a thread that never receives the
    SIGINT-driven `KeyboardInterrupt` directly, so its signal handling sets
    `stop_event` to reach it. Checked AFTER the cycle only — unlike
    `position_loop`, this loop must always drain `closed_queue` once more before
    it stops, or a close queued while it slept goes un-ingested until the next
    close (or a manual `journal sync`) triggers a run.

    A cycle that RAISES does not end the loop: it is counted in `failed_cycles`
    and retried on the next tick, same reasoning as `position_loop`.
    """
    requeued = requeue_orphaned(conn)
    if requeued:
        log.info("live: requeued %d orphaned candle request(s) at startup", requeued)

    cycles = 0
    failed = 0          # total, reported
    streak = 0          # consecutive, resets on the first good cycle
    locked_streak = 0   # consecutive "locked" ones — see LOCKED_STREAK_EXIT
    deadline = monotonic() + duration if duration is not None else None

    def _report(stopped_by: str) -> SymbolLoopReport:
        return SymbolLoopReport(
            cycles=cycles, requeued=requeued, failed_cycles=failed,
            stopped_by=stopped_by,
        )

    try:
        while True:
            # NOT checked here, unlike position_loop: `symbol_cycle` is the only
            # thing that drains `closed_queue`, so a stop set during the sleep
            # must still buy one more cycle or any close `position_cycle` queued
            # in that window dies with the process. Not lost forever — the drain
            # only TRIGGERS the pipeline, which re-syncs a window rather than the
            # queued ids, so the next unrelated close coalesces it in, and
            # `journal sync` by hand does it now — but until one of those happens
            # the journal silently lags behind the account, and one more cycle
            # costs nothing. The asymmetry is the point: position_loop's extra
            # cycle can SEND AN ORDER, this one only drains a queue and reads
            # candles.
            try:
                r = symbol_cycle(client, conn, closed_queue)
            except Exception as e:
                # Mirrors position_loop: rollback, log, retry, never die on a
                # bridge blip. Never carry a half-written cycle's WAL writer
                # slot into the sleep.
                conn.rollback()
                if isinstance(e, sqlite3.OperationalError) and "locked" in str(e).lower():
                    # Same rule as position_loop: one locked cycle is ordinary
                    # contention between this process's two writer connections;
                    # only a streak of them means a second `journal live`, and
                    # only that escapes to `cli.live`.
                    locked_streak += 1
                    if locked_streak >= LOCKED_STREAK_EXIT:
                        raise
                else:
                    locked_streak = 0
                failed += 1
                streak += 1
                if streak == 1:
                    log.exception("live: symbol_cycle failed — retrying, the loop stays up")
                elif streak % 60 == 0:
                    log.warning("live: symbol_cycle still failing, %d in a row", streak)
            else:
                if streak:
                    log.info("live: symbol_cycle recovered after %d failed cycle(s)", streak)
                    streak = 0
                locked_streak = 0
                if on_cycle is not None:
                    on_cycle(r)
            cycles += 1
            if once:
                return _report("once")
            if deadline is not None and monotonic() >= deadline:
                return _report("duration")
            if stop_event is not None and stop_event.is_set():
                return _report("interrupt")
            sleep(interval)
    except KeyboardInterrupt:
        return _report("interrupt")
