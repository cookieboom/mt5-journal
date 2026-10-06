"""Paper trading: a virtual account with the balance, leverage and stop-out
the human sets. Fills come from the tick `journal live` stored in
`live_quotes`; the web never touches the bridge. Nothing here reaches
deals_raw or trades (rule 2).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from ...store import prefs_store
from .. import api
from .. import paper
from ..deps import get_conn

router = APIRouter()


# --------------------------------------------------------- paper trading
# A virtual account: balance, leverage and stop-out the human sets. Fills
# come from the tick `journal live` stored in `live_quotes`; the web never
# touches the bridge. Nothing here reaches deals_raw or trades (rule 2).
@router.get("/api/paper/accounts")
def api_paper_accounts(status: str | None = None,
                       conn: sqlite3.Connection = Depends(get_conn)):
    return JSONResponse(api.to_jsonable(paper.list_accounts_view(conn, status)))


@router.post("/api/paper/accounts")
def api_paper_create(
    name: str = Body(...),
    initial_balance: float = Body(...),
    leverage: int = Body(...),
    stopout_pct: float = Body(...),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        out = paper.create_account(conn, name=name,
                                   initial_balance=initial_balance,
                                   leverage=leverage, stopout_pct=stopout_pct)
    except paper.PaperError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))


@router.post("/api/paper/accounts/{account_id}/archive")
def api_paper_archive(account_id: int,
                      conn: sqlite3.Connection = Depends(get_conn)):
    try:
        out = paper.archive_account(conn, account_id)
    except paper.PaperError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))


@router.get("/api/paper/accounts/{account_id}")
def api_paper_account(account_id: int,
                      conn: sqlite3.Connection = Depends(get_conn)):
    view = paper.account_view(conn, account_id)
    if view is None:
        return JSONResponse({"error": f"no paper account {account_id}"},
                            status_code=404)
    return JSONResponse(api.to_jsonable(view))


@router.post("/api/paper/accounts/{account_id}/orders")
def api_paper_order(
    account_id: int,
    symbol: str = Body(...),
    direction: str = Body(...),
    kind: str = Body("market"),
    volume: float | None = Body(None),
    risk_pct: float | None = Body(None),
    price: float | None = Body(None),
    sl: float = Body(0.0),
    tp: float = Body(0.0),
    expires_msc: int | None = Body(None),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        out = paper.place_order(conn, account_id, symbol=symbol,
                                direction=direction, kind=kind, volume=volume,
                                risk_pct=risk_pct, price=price, sl=sl, tp=tp,
                                expires_msc=expires_msc)
    except paper.PaperError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))


@router.patch("/api/paper/positions/{position_id}")
def api_paper_modify(
    position_id: int,
    sl: float | None = Body(None),
    tp: float | None = Body(None),
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        out = paper.modify_sltp(conn, position_id, sl=sl, tp=tp)
    except paper.PaperError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))


@router.post("/api/paper/positions/{position_id}/close")
def api_paper_close(position_id: int,
                    volume: float | None = Body(None, embed=True),
                    conn: sqlite3.Connection = Depends(get_conn)):
    try:
        out = paper.close_position(conn, position_id, volume=volume)
    except paper.PaperError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))


@router.post("/api/paper/positions/{position_id}/reverse")
def api_paper_reverse(position_id: int,
                      conn: sqlite3.Connection = Depends(get_conn)):
    try:
        out = paper.reverse_position(conn, position_id)
    except paper.PaperError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))


@router.delete("/api/paper/positions/{position_id}")
def api_paper_cancel(position_id: int,
                     conn: sqlite3.Connection = Depends(get_conn)):
    try:
        out = paper.cancel_pending(conn, position_id)
    except paper.PaperError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))


@router.post("/api/paper/accounts/{account_id}/close_all")
def api_paper_close_all(account_id: int,
                        conn: sqlite3.Connection = Depends(get_conn)):
    try:
        out = paper.close_all(conn, account_id)
    except paper.PaperError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse(api.to_jsonable(out))


@router.get("/api/prefs/paper")
def api_paper_prefs_get(conn: sqlite3.Connection = Depends(get_conn)):
    return JSONResponse({"prefs": prefs_store.get_paper_prefs(conn)})


@router.put("/api/prefs/paper")
def api_paper_prefs_set(prefs: dict = Body(...),
                        conn: sqlite3.Connection = Depends(get_conn)):
    return JSONResponse({"updated_ms": prefs_store.set_paper_prefs(conn, prefs)})
