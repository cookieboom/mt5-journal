"""MT5 wire-format conversion, shared by every MT5Client backend.

These functions touch no MT5 module import — they only know our own
dataclasses/enums (`base.py`) and plain dicts/objects. `adapter/live.py`
(the Docker bridge) and `adapter/native.py` (the Windows package) both call
these so the trap-prone parts — the sl/tp None-vs-0.0 distinction (rule 4),
the enum-to-int conversion (rule 12), and eager-reading a possibly-netref
result object — exist exactly once.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from .base import TradeRequest, TradeResult


def _build(cls, raw: dict[str, Any]):
    """Map a bridge/native `._asdict()` into our dataclass: keep declared
    fields, stash the whole dict in `raw`. Unknown MT5 fields survive in
    `raw` (forward-compat)."""
    known = {f.name for f in fields(cls)} - {"raw"}
    kwargs = {k: raw[k] for k in known if k in raw}
    return cls(**kwargs, raw=dict(raw))


def _to_bridge_request(req: TradeRequest) -> dict[str, Any]:
    """`TradeRequest` -> the MT5 wire dict. THE ONLY PLACE our enums become
    MT5 integers (rule 12).

    Omits every field the caller left as None rather than sending a 0, which
    matters most for `sl`/`tp`: MT5 reads a 0.0 as "clear this level", so
    passing None through as 0 would wipe a live stop-loss on a modify that
    only meant to set a take-profit (rule 4).
    """
    # `order_send` on the bridge path is server-side `eval(repr(request))`
    # (siliconmetatrader5/__init__.py:772), so every value here must be a
    # plain builtin with a faithful repr. An IntEnum's repr is
    # `<TradeAction.SLTP: 6>` — which would be a SyntaxError on that path.
    # int() is not cosmetic; without it nothing sends at all on the bridge.
    # The native path passes a plain dict too, so the same conversion is
    # correct (and required) there as well.
    out: dict[str, Any] = {}
    if req.action is not None:
        out["action"] = int(req.action)
    if req.position_id is not None:
        out["position"] = int(req.position_id)   # MT5's field name is `position`
    if req.symbol is not None:
        out["symbol"] = str(req.symbol)
    if req.order_type is not None:
        out["type"] = int(req.order_type)
    if req.volume is not None:
        out["volume"] = float(req.volume)
    if req.price is not None:
        out["price"] = float(req.price)
    if req.sl is not None:
        out["sl"] = float(req.sl)
    if req.tp is not None:
        out["tp"] = float(req.tp)
    if req.deviation is not None:
        out["deviation"] = int(req.deviation)
    if req.filling is not None:
        out["type_filling"] = int(req.filling)
    if req.magic is not None:
        out["magic"] = int(req.magic)
    if req.comment is not None:
        out["comment"] = str(req.comment)
    return out


def _from_bridge_result(res: Any) -> TradeResult:
    """The MT5 result object -> ours.

    On the bridge path this is an rpyc NETREF (no `obtain=True`): every
    attribute read is a round trip and the object dies with the connection.
    On the native path it is a local namedtuple with no such lifetime issue,
    but reading it the same eager way is still correct and keeps this one
    function backend-agnostic. Everything is read and copied into plain
    builtins immediately — a lazy read later would be a use-after-close on
    the bridge path.
    """
    if res is None:
        # Nothing came back at all. We cannot say the order failed — it may
        # well have reached the broker — so this is UNKNOWN (rule 4), and
        # `is_success` treats a None retcode as not-success.
        return TradeResult(comment="no result from MT5")

    def _get(name: str) -> Any:
        try:
            return getattr(res, name)
        except Exception:  # pragma: no cover - a field this build lacks
            return None

    raw: dict[str, Any] = {}
    try:
        raw = dict(res._asdict())
    except Exception:  # pragma: no cover - check results may lack _asdict
        pass

    retcode = _get("retcode")
    volume = _get("volume")
    price = _get("price")
    return TradeResult(
        retcode=int(retcode) if retcode is not None else None,
        deal=int(_get("deal")) if _get("deal") is not None else None,
        order=int(_get("order")) if _get("order") is not None else None,
        # ACTUAL filled volume/price. On a DONE_PARTIAL these are NOT the
        # requested figures — never substitute the request's values here.
        volume=float(volume) if volume is not None else None,
        price=float(price) if price is not None else None,
        comment=str(_get("comment")) if _get("comment") is not None else None,
        request_id=int(_get("request_id")) if _get("request_id") is not None else None,
        raw=raw,
    )
