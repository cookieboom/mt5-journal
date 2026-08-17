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
from ..domain import commands as cmd
from ..domain import paper_eval as pe
from ..domain import risk
from ..domain.sim_stats import summary as sim_summary
from ..domain.symbols import to_base
from ..execute import FEED_STALE_MS
from ..store import live_store, paper_store
from ..store.db import now_ms

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


def _require_active(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row:
    account = paper_store.get_account(conn, account_id)
    if account is None:
        raise PaperError(f"Tidak ada akun paper {account_id}.")
    if account["status"] != "active":
        raise PaperError("Akun ini sudah diarsipkan — buka akun lain untuk trading.")
    return account


def _fresh_quote(conn: sqlite3.Connection, symbol: str,
                 now_msc: int) -> pe.Quote:
    """The latest tick, or a refusal. A stale reference price does not fail
    loudly — it silently resizes the position, which is why the guard is here and
    not only in the browser. Same threshold as a real open (`FEED_STALE_MS`)."""
    row = live_store.read_quote(conn, symbol)
    if row is None:
        raise PaperError(
            f"Belum ada harga untuk {symbol} — `journal live` belum pernah "
            f"menyimpan tick simbol ini. Order ditolak, bukan ditebak."
        )
    age = now_msc - int(row["updated_msc"])
    if age >= FEED_STALE_MS:
        raise PaperError(
            f"Harga {symbol} basi {age / 1000:.0f}s — `journal live` tidak "
            f"menyuapi feed. Order ditolak."
        )
    return pe.Quote(symbol=symbol, bid=float(row["bid"]), ask=float(row["ask"]),
                    time_msc=int(row["tick_msc"]))


def _spec_row(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM symbol_specs WHERE symbol = ?", (symbol,)
    ).fetchone()
    if row is None:
        raise PaperError(
            f"Spesifikasi {symbol} belum diketahui — jalankan `journal sync`."
        )
    return row


def place_order(conn: sqlite3.Connection, account_id: int, *, symbol: str,
                direction: str, kind: str = "market", volume: float | None = None,
                risk_pct: float | None = None, price: float | None = None,
                sl: float = 0.0, tp: float = 0.0,
                expires_msc: int | None = None,
                now_msc: int | None = None) -> dict:
    """Open a market position immediately, or park a pending limit/stop order.

    Sizing takes `volume` OR `risk_pct`, never both and never neither: a route
    that picks for you is a route that sizes someone's position by guessing.
    """
    now = now_ms() if now_msc is None else now_msc
    account = _require_active(conn, account_id)
    if direction not in ("buy", "sell"):
        raise PaperError("Arah harus 'buy' atau 'sell'.")
    if kind not in ("market", "limit", "stop"):
        raise PaperError("Jenis order harus 'market', 'limit', atau 'stop'.")
    if (volume is None) == (risk_pct is None):
        raise PaperError("Isi salah satu: volume ATAU risk_pct, tidak dua-duanya.")

    quote = _fresh_quote(conn, symbol, now)
    spec_row = _spec_row(conn, symbol)
    specs = _specs(conn, symbol)
    if specs is None:
        raise PaperError(f"Spesifikasi harga {symbol} belum lengkap.")

    if kind == "market":
        reference = pe.entry_side(direction, quote)
    else:
        if price is None:
            raise PaperError("Order limit/stop wajib menyebut harga pemicu.")
        reference = float(price)

    if risk_pct is not None:
        if risk_pct <= 0:
            raise PaperError("risk_pct harus lebih besar dari 0.")
        if sl is None or abs(sl) < 1e-9:
            raise PaperError(
                "Sizing dari risiko butuh SL — tanpa jarak stop tidak ada "
                "risiko untuk dibagi."
            )
        state = pe.account_state(
            [_state(r) for r in paper_store.list_positions(
                conn, account_id, statuses=("open",))],
            {symbol: quote}, {symbol: specs},
            balance=float(account["balance"]), leverage=int(account["leverage"]),
        )
        equity = state.equity
        if equity is None:
            raise PaperError(
                "Equity akun belum bisa dihitung (harga posisi lain belum ada) "
                "— sizing dari risiko ditolak."
            )
        budget = equity * risk_pct / 100.0
        raw = risk.volume_for_risk(reference, sl, specs.tick_size,
                                   specs.tick_value, budget)
        volume = risk.floor_to_step(raw, spec_row["volume_step"])
        if volume is None or volume <= 0:
            raise PaperError(
                "Risiko yang diminta lebih kecil dari satu step volume broker."
            )

    try:
        cmd.check_volume("open", None, spec_row, volume)
        cmd.check_level("sl", sl, direction, reference, spec_row)
        cmd.check_level("tp", tp, direction, reference, spec_row)
    except cmd.CommandError as e:
        raise PaperError(str(e)) from e

    if kind == "market":
        need = pe.margin_usc(volume, reference, specs, int(account["leverage"]))
        state = pe.account_state(
            [_state(r) for r in paper_store.list_positions(
                conn, account_id, statuses=("open",))],
            {symbol: quote}, {symbol: specs},
            balance=float(account["balance"]), leverage=int(account["leverage"]),
        )
        if need is None or state.free_margin is None:
            raise PaperError(
                "Margin tidak bisa dihitung untuk simbol ini — order ditolak, "
                "bukan diasumsikan aman."
            )
        if need > state.free_margin:
            raise PaperError(
                f"Butuh margin {need:.2f} {CURRENCY}, free margin hanya "
                f"{state.free_margin:.2f} {CURRENCY}."
            )

    status = "open" if kind == "market" else "pending"
    pid = paper_store.insert_position(
        conn, account_id=account_id, symbol=symbol, symbol_base=to_base(symbol),
        direction=direction, order_kind=kind,
        request_price=(None if kind == "market" else reference),
        volume=float(volume), sl=float(sl or 0.0), tp=float(tp or 0.0),
        status=status,
        entry_price=(reference if kind == "market" else None),
        entry_msc=(quote.time_msc if kind == "market" else None),
        expires_msc=expires_msc,
    )
    return _row(paper_store.get_position(conn, pid))
