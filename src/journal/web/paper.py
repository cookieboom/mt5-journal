"""Paper trading orchestration — the impure glue between the pure evaluator
(`domain/paper_eval`), the pure store (`store/paper_store`), and the latest tick
the daemon left in `live_quotes`. Never the bridge (M9 boundary): the web reads
prices from the DB or refuses.

Money is USC and every payload says so. R is unit-free. An unknown is `None` all
the way to the browser — a 0 equity would read as a wiped account.

This module is open for Tasks 11 (order placement) and 12 (close/modify/reverse)
to append to — keep new functions below, reusing `_row`, `_specs`, `_quote`,
`_state` rather than duplicating them.
"""
from __future__ import annotations

import sqlite3

from ..analytics.report import sequence_stats
from ..domain import paper_eval as pe
from ..domain.sim_stats import summary as sim_summary
from ..store import live_store, paper_store

CURRENCY = "USC"


class PaperError(Exception):
    """A refusal the human should read. Routes turn it into a 400."""


def _row(row: sqlite3.Row | None) -> dict | None:
    return None if row is None else {k: row[k] for k in row.keys()}


def _specs(conn: sqlite3.Connection, symbol: str) -> pe.Specs | None:
    r = conn.execute(
        "SELECT tick_size, tick_value, contract_size, currency_profit "
        "FROM symbol_specs WHERE symbol = ?", (symbol,)
    ).fetchone()
    if r is None or r["tick_size"] in (None, 0) or r["tick_value"] in (None, 0):
        return None
    return pe.Specs(tick_size=float(r["tick_size"]), tick_value=float(r["tick_value"]),
                    contract_size=float(r["contract_size"] or 1.0),
                    currency_profit=r["currency_profit"] or "")


def _quote(conn: sqlite3.Connection, symbol: str) -> pe.Quote | None:
    r = live_store.read_quote(conn, symbol)
    if r is None:
        return None
    return pe.Quote(symbol=symbol, bid=float(r["bid"]), ask=float(r["ask"]),
                    time_msc=int(r["tick_msc"]))


def _state(row: sqlite3.Row) -> pe.PaperPos:
    return pe.PaperPos(
        id=row["id"], symbol=row["symbol"], direction=row["direction"],
        order_kind=row["order_kind"], request_price=row["request_price"],
        volume=row["volume"], sl=row["sl"] or 0.0, tp=row["tp"] or 0.0,
        status=row["status"], entry_price=row["entry_price"],
        entry_msc=row["entry_msc"], expires_msc=row["expires_msc"],
    )


def create_account(conn: sqlite3.Connection, *, name: str, initial_balance: float,
                   leverage: int, stopout_pct: float) -> dict:
    if not name or not name.strip():
        raise PaperError("Nama akun wajib diisi.")
    if initial_balance <= 0:
        raise PaperError("Balance awal harus lebih besar dari 0 (USC).")
    if leverage <= 0:
        raise PaperError("Leverage harus lebih besar dari 0.")
    if stopout_pct < 0:
        raise PaperError("Stop-out level tidak boleh negatif.")
    try:
        account_id = paper_store.create_account(
            conn, name=name.strip(), initial_balance=float(initial_balance),
            leverage=int(leverage), stopout_pct=float(stopout_pct),
        )
    except ValueError as e:
        raise PaperError(str(e)) from e
    return _row(paper_store.get_account(conn, account_id))


def list_accounts_view(conn: sqlite3.Connection,
                       status: str | None = None) -> list[dict]:
    return [_row(r) for r in paper_store.list_accounts(conn, status)]


def archive_account(conn: sqlite3.Connection, account_id: int) -> dict:
    if paper_store.get_account(conn, account_id) is None:
        raise PaperError(f"Tidak ada akun paper {account_id}.")
    paper_store.archive_account(conn, account_id)
    return _row(paper_store.get_account(conn, account_id))


def account_view(conn: sqlite3.Connection, account_id: int) -> dict | None:
    """Everything one panel needs: the account, a marked header, the live rows,
    the closed history, the ungated summary, and the realized equity curve."""
    account = paper_store.get_account(conn, account_id)
    if account is None:
        return None

    rows = paper_store.list_positions(conn, account_id)
    open_rows = [r for r in rows if r["status"] == "open"]
    pending_rows = [r for r in rows if r["status"] == "pending"]
    closed_rows = [r for r in rows if r["status"] == "closed"]

    symbols = {r["symbol"] for r in open_rows}
    quotes = {s: q for s in symbols if (q := _quote(conn, s)) is not None}
    specs = {s: sp for s in symbols if (sp := _specs(conn, s)) is not None}

    state = pe.account_state(
        [_state(r) for r in open_rows], quotes, specs,
        balance=float(account["balance"]), leverage=int(account["leverage"]),
    )

    # Reuse the report's own drawdown: it is pure, it already starts its peak at
    # the account's start, and it reads `close_time_msc` — so alias `exit_msc`.
    seq_rows = conn.execute(
        "SELECT exit_msc AS close_time_msc, net_profit FROM paper_positions "
        "WHERE account_id = ? AND status = 'closed' AND net_profit IS NOT NULL",
        (account_id,),
    ).fetchall()
    _, max_dd, _, _ = sequence_stats(seq_rows)

    balance = float(account["initial_balance"])
    curve = []
    for r in sorted((r for r in closed_rows if r["exit_msc"] is not None),
                    key=lambda r: r["exit_msc"]):
        if r["net_profit"] is None:
            continue
        balance += float(r["net_profit"])
        curve.append({"exit_msc": r["exit_msc"], "balance": balance,
                      "position_id": r["id"], "symbol_base": r["symbol_base"]})

    return {
        "account": _row(account),
        "header": {
            "currency": CURRENCY,
            "balance": float(account["balance"]),
            "equity": state.equity,
            "margin": state.margin,
            "free_margin": state.free_margin,
            "margin_level": state.margin_level,
            "floating": state.floating,
            "leverage": int(account["leverage"]),
            "stopout_pct": float(account["stopout_pct"]),
        },
        "open": [
            {**_row(r),
             "floating": (pe.floating_usc(_state(r), quotes[r["symbol"]],
                                          specs[r["symbol"]])
                          if r["symbol"] in quotes and r["symbol"] in specs
                          else None)}
            for r in open_rows
        ],
        "pending": [_row(r) for r in pending_rows],
        "closed": [_row(r) for r in closed_rows],
        "summary": sim_summary(closed_rows),
        "max_drawdown": max_dd,
        "equity_curve": curve,
    }
