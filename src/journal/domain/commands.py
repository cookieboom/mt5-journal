"""What stands between a click and a real order (M9).

Pure: takes a position row and a symbol spec row, returns a `TradeRequest` or
raises `CommandError`. No DB, no bridge, no clock — so every rule below is a
unit test, not a thing we hope the UI remembers to do.

Two measured facts about THIS account shape most of what follows, and neither is
guessable from the MQL5 docs:

  * **Hedging** (`margin_mode = 2`). One order = one position. A close MUST carry
    `position_id` or the broker opens a second, opposite position instead. And
    "add volume" cannot grow an existing position at all — see `build_request`.
  * **Market execution** (`trade_exemode = 2` on all three symbols). The broker
    fills at its own price and ignores the `price` field, so we do not send one.

The asymmetry running through this module is deliberate: **rules that would
prevent a human from REDUCING exposure are not applied.** The 1-lot cap, the
unknown-spec refusal, and the `trade_mode` gate all bind when opening or adding,
and all step aside for a full close. A safety rule that traps someone in a
position is not a safety rule.

CLAUDE.md rule 9 still holds: nothing here suggests a level, a size, or a moment.
The human types the numbers; this module only says yes or no, and why.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..adapter.base import (
    OrderType,
    TradeAction,
    TradeRequest,
    TradeRetcode,
    filling_for,
    is_success,
)
from .risk import risk_amount

# The human's hard cap, 2026-07-23: one lot per command. A constant here rather
# than a `max` attribute on a form field, so it is enforced somewhere testable.
# On XAUUSDc 1 lot = 1 oz (CLAUDE.md), so this is a real ceiling, not a
# formality. It governs volume a human TYPES — see `check_volume`.
MAX_LOT = 1.0

# The second hard ceiling, and the one that scales with the account: no single
# order may put more than this share of `accounts.balance` at stake. MAX_LOT
# alone does not bound risk — one lot with a distant stop is a large loss. A
# constant here rather than a pref, for the same reason MAX_LOT is: a limit the
# UI can raise is not a limit.
MAX_RISK_PCT = 5.0

# Rule 5: money and volume are REAL, compared with tolerance, never `==` or a
# bare `>`. Lot steps are 0.01, so this is comfortably finer than any real
# distinction while absorbing IEEE754 noise.
_TOL = 1e-9

KINDS = ("modify_sltp", "close", "close_partial", "add_volume", "open")

# The kinds that can INCREASE exposure. Everything stricter applies only to
# these; `close` is deliberately absent.
_OPENING = ("add_volume", "open")

# SYMBOL_TRADE_MODE_*, from the bridge (__init__.py:166-170). Kept as plain ints
# with a comment rather than an adapter enum: unlike DealType or TradeAction these
# never cross the Protocol in either direction — they arrive as a stored integer
# in `symbol_specs.trade_mode` and are only ever compared here.
_TRADE_MODE_DISABLED = 0
_TRADE_MODE_LONGONLY = 1
_TRADE_MODE_SHORTONLY = 2
_TRADE_MODE_CLOSEONLY = 3
_TRADE_MODE_FULL = 4


class CommandError(Exception):
    """A command that must not be sent. The message is shown to the human, so it
    says what is wrong and what the limit actually is — never just 'invalid'."""


def _get(row: Mapping[str, Any] | Any, key: str) -> Any:
    """Read a column from a `sqlite3.Row` or a plain dict.

    `sqlite3.Row` has no `.get()`, so a missing key raises IndexError there and
    KeyError on a dict — both mean the same thing to us: unknown (rule 4).
    """
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _is_multiple(value: float, step: float) -> bool:
    """Is `value` a whole number of `step`s?

    NOT `value % step == 0`: in IEEE754, `0.03 % 0.01` is 0.009999999999999998,
    so a modulo check refuses a perfectly ordinary 0.03-lot order. Round to the
    nearest step and compare the difference against a tolerance instead
    (rule 5).
    """
    if step <= 0:
        return False
    n = round(value / step)
    return abs(value - n * step) < max(_TOL, step * 1e-6)


def _reduces_exposure(kind: str) -> bool:
    """A full close only ever gives risk back. The rules that exist to stop
    exposure growing must not apply to it."""
    return kind == "close"


# ---------------------------------------------------------------- validation


def _check_trade_mode(kind: str, spec: Mapping[str, Any] | Any, direction: str) -> None:
    mode = _get(spec, "trade_mode")

    if mode == _TRADE_MODE_DISABLED:
        raise CommandError(
            "Simbol ini sedang tidak bisa ditradingkan (trade_mode=disabled)."
        )
    if _reduces_exposure(kind):
        return  # closing is allowed in every mode except fully disabled

    if mode is None:
        # Rule 4, applied consistently: an unknown mode is not permission to
        # increase exposure — but the early return above means it never blocks
        # an exit.
        raise CommandError(
            "trade_mode simbol belum diketahui — jalankan `journal sync` dulu. "
            "Menambah posisi ditolak selama batasannya tidak diketahui."
        )
    if mode == _TRADE_MODE_CLOSEONLY:
        raise CommandError("Simbol ini close-only; menambah posisi ditolak.")
    if mode == _TRADE_MODE_LONGONLY and direction == "sell":
        raise CommandError("Simbol ini long-only.")
    if mode == _TRADE_MODE_SHORTONLY and direction == "buy":
        raise CommandError("Simbol ini short-only.")


def check_volume(
    kind: str, position: Mapping[str, Any] | Any, spec: Mapping[str, Any] | Any,
    volume: float | None,
) -> None:
    """Every rule about a volume the HUMAN typed.

    Paper trading is now a second caller. Not called for `close`, whose volume is
    the position's own — validating it could only ever refuse a legitimate exit.
    """
    if volume is None:
        raise CommandError("Volume wajib diisi untuk perintah ini.")
    if volume <= _TOL:
        raise CommandError("Volume harus lebih besar dari 0.")

    # The human's cap first: it is ours, it is the tightest, and it produces the
    # most useful message.
    if volume > MAX_LOT + _TOL:
        raise CommandError(
            f"Volume {volume} melebihi batas keras {MAX_LOT} lot per perintah."
        )

    vmin = _get(spec, "volume_min")
    vmax = _get(spec, "volume_max")
    vstep = _get(spec, "volume_step")
    if vmin is None or vmax is None or vstep is None:
        # Rule 4. Validating against a limit we do not have is not validating.
        raise CommandError(
            "Spesifikasi volume simbol belum diketahui (volume_min/max/step "
            "masih NULL) — jalankan `journal sync` dulu. Perintah ditolak, "
            "bukan diasumsikan aman."
        )

    if volume < vmin - _TOL:
        raise CommandError(f"Volume {volume} di bawah minimum broker {vmin}.")
    if volume > vmax + _TOL:
        raise CommandError(f"Volume {volume} di atas maksimum broker {vmax}.")
    if not _is_multiple(volume, vstep):
        raise CommandError(f"Volume {volume} bukan kelipatan step {vstep}.")

    if kind == "close_partial":
        held = _get(position, "volume")
        if held is None:
            raise CommandError("Volume posisi tidak diketahui.")
        if volume >= held - _TOL:
            raise CommandError(
                f"Partial close {volume} tidak lebih kecil dari volume posisi "
                f"{held} — pakai `close` untuk menutup seluruhnya."
            )


def check_level(
    name: str, level: float | None, direction: str,
    price: float | None, spec: Mapping[str, Any] | Any,
) -> None:
    """One of SL/TP against the current price.

    Paper trading is now a second caller. `price` is the entry price for a market
    order and the requested price for a pending one. `None` = leave it alone,
    `0.0` = clear it (rule 4). A cleared level has no side, so the side check must
    skip it — otherwise clearing a buy's stop would be refused for sitting 'below'
    the price.
    """
    if level is None:
        return
    if abs(level) < _TOL:
        return  # explicit clear

    if price is None:
        raise CommandError(
            "Harga terkini posisi tidak diketahui — tidak bisa memeriksa sisi "
            f"{name.upper()}. Tunggu snapshot `journal live` berikutnya."
        )

    # Which side is correct depends on the direction AND on which level it is.
    # A buy's stop sits below and its target above; a sell is the mirror.
    below = (direction == "buy") == (name == "sl")
    if below and level >= price - _TOL:
        raise CommandError(
            f"{name.upper()} {level} harus di BAWAH harga sekarang {price} "
            f"untuk posisi {direction}."
        )
    if not below and level <= price + _TOL:
        raise CommandError(
            f"{name.upper()} {level} harus di ATAS harga sekarang {price} "
            f"untuk posisi {direction}."
        )

    # stops_level is expressed in POINTS, so the minimum distance in price units
    # is stops_level * point. This broker reports 0 (measured — genuinely no
    # restriction), but brokers widen it around news, and a refetched spec would
    # then start enforcing it here with no code change.
    stops_level = _get(spec, "stops_level")
    point = _get(spec, "point")
    if stops_level and point:
        min_distance = stops_level * point
        if abs(level - price) < min_distance - _TOL:
            raise CommandError(
                f"{name.upper()} {level} terlalu dekat ke harga {price}; broker "
                f"minta jarak minimal {min_distance:g} ({stops_level} point)."
            )


def _check_risk(
    position: Mapping[str, Any] | Any, spec: Mapping[str, Any] | Any,
    sl: float | None, volume: float | None, balance: float | None,
) -> None:
    """The risk ceiling, for an `open` only.

    An open is the only command that both creates exposure and knows its own
    stop, so it is the only one whose risk can be bounded before it is sent.
    Every unknown here refuses rather than defaults: an unmeasurable risk is not
    a small one.
    """
    if sl is None or abs(sl) < _TOL:
        raise CommandError(
            "SL wajib diisi untuk membuka posisi — tanpa SL, risikonya tidak "
            "bisa dihitung dan ukuran lot tidak bisa diturunkan."
        )
    if balance is None:
        raise CommandError(
            "Balance akun belum diketahui — jalankan `journal sync` dulu. "
            "Membuka posisi ditolak selama batas risikonya tidak bisa dihitung."
        )

    risk = risk_amount(
        _get(position, "price_current"), sl,
        _get(spec, "tick_size"), _get(spec, "tick_value"), volume,
    )
    if risk is None:
        raise CommandError(
            "Risiko tidak bisa dihitung (tick_size/tick_value simbol atau harga "
            "terkini belum diketahui) — jalankan `journal sync` dulu. Posisi "
            "tidak dibuka tanpa risiko yang terukur."
        )

    ceiling = balance * MAX_RISK_PCT / 100.0
    if risk > ceiling + _TOL:
        pct = (risk / balance * 100.0) if balance else float("inf")
        raise CommandError(
            f"Risiko {risk:.2f} ({pct:.2f}% dari balance) melebihi batas keras "
            f"{MAX_RISK_PCT}% ({ceiling:.2f}). Perkecil lot atau dekatkan SL."
        )


def validate(
    kind: str,
    position: Mapping[str, Any] | Any,
    spec: Mapping[str, Any] | Any,
    *,
    sl: float | None = None,
    tp: float | None = None,
    volume: float | None = None,
    balance: float | None = None,
) -> None:
    """Raise `CommandError` if this command must not be sent. Returns None when
    it may be.

    `balance` is read only for `open` — the one kind whose risk can be bounded
    before it exists. Every other kind ignores it.
    """
    if kind not in KINDS:
        raise CommandError(f"Jenis perintah tidak dikenal: {kind!r}.")

    direction = _get(position, "direction")
    if direction not in ("buy", "sell"):
        raise CommandError(f"Arah posisi tidak diketahui: {direction!r}.")

    _check_trade_mode(kind, spec, direction)

    if kind == "modify_sltp":
        if sl is None and tp is None:
            # An empty instruction. The broker would answer NO_CHANGES; saying so
            # here is cheaper and tells the human something they can act on.
            raise CommandError("Tidak ada yang diubah — isi SL atau TP.")
        price = _get(position, "price_current")
        check_level("sl", sl, direction, price, spec)
        check_level("tp", tp, direction, price, spec)
        return

    if kind == "open":
        # Order matters: volume rules first (the cheapest and most specific
        # message), then the levels, then the risk — which needs both a valid
        # volume and a valid SL to mean anything.
        check_volume(kind, position, spec, volume)
        price = _get(position, "price_current")
        check_level("sl", sl, direction, price, spec)
        check_level("tp", tp, direction, price, spec)
        _check_risk(position, spec, sl, volume, balance)
        return

    if kind in ("close_partial", "add_volume"):
        check_volume(kind, position, spec, volume)


# ------------------------------------------------------------- request building


def _opposite(direction: str) -> OrderType:
    """The order type that CLOSES a position of this direction."""
    return OrderType.SELL if direction == "buy" else OrderType.BUY


def _same(direction: str) -> OrderType:
    return OrderType.BUY if direction == "buy" else OrderType.SELL


def build_request(
    kind: str,
    position: Mapping[str, Any] | Any,
    spec: Mapping[str, Any] | Any,
    *,
    sl: float | None = None,
    tp: float | None = None,
    volume: float | None = None,
    balance: float | None = None,
) -> TradeRequest:
    """Turn a validated command into the request the adapter will send.

    Validates first, unconditionally: this must not become a way around
    `validate`. A caller that forgot still cannot produce an over-cap request.
    """
    validate(kind, position, spec, sl=sl, tp=tp, volume=volume, balance=balance)

    direction = _get(position, "direction")
    # Rule 11: MT5 is queried with the verbatim symbol ('XAUUSDc'). 'XAUUSD'
    # does not exist on this server.
    symbol = _get(position, "symbol")
    position_id = _get(position, "position_id")

    if kind == "modify_sltp":
        # MT5's TRADE_ACTION_SLTP has no partial-update semantics: a field the
        # bridge omits from the request defaults to 0.0 on the broker side,
        # which MT5 reads as "clear this level" (rule 4 / trap 6). So the side
        # the human did NOT touch must be carried forward from the position's
        # CURRENT level, not left None — otherwise setting only the SL wipes a
        # live TP. `None` still survives through untouched when the current
        # level is itself unknown (NULL); there is nothing to fill in with.
        sl_out = sl if sl is not None else _get(position, "sl")
        tp_out = tp if tp is not None else _get(position, "tp")
        return TradeRequest(
            action=TradeAction.SLTP,
            position_id=position_id,
            symbol=symbol,
            sl=sl_out,
            tp=tp_out,
            # No volume and no filling: a modify is not a fill.
        )

    filling = filling_for(_get(spec, "filling_mode"))

    if kind in ("close", "close_partial"):
        return TradeRequest(
            action=TradeAction.DEAL,
            # THE field. On a hedging account, omitting it does not close
            # anything — it opens a second, opposite position.
            position_id=position_id,
            symbol=symbol,
            order_type=_opposite(direction),
            volume=volume if kind == "close_partial" else _get(position, "volume"),
            # No price: execution is MARKET (trade_exemode=2, measured on all
            # three symbols), so the broker fills at its own price and ignores
            # this field. Sending a price_current that is already stale by the
            # time it lands buys nothing and invites INVALID_PRICE / PRICE_OFF.
            filling=filling,
        )

    if kind == "open":
        # The first command in this project that CREATES a position. Same shape
        # as add_volume — a plain market DEAL with no position_id — but it
        # carries the levels, because attaching SL/TP to the opening request is
        # the only way they exist from the position's first tick. A separate
        # modify afterwards leaves a window where the position is live and
        # unprotected, and the whole point of this feature is the stop.
        return TradeRequest(
            action=TradeAction.DEAL,
            position_id=None,
            symbol=symbol,
            order_type=_same(direction),
            volume=volume,
            sl=sl,
            tp=tp,
            filling=filling,
        )

    # add_volume.
    #
    # THE thing about this operation the human must understand: on a HEDGING
    # account a market order in the same direction CANNOT grow the existing
    # position. MT5 opens a SECOND position with its own ticket, and the journal
    # will show two trades rather than one larger one. `position_id` is
    # deliberately omitted — on a DEAL request that field means "close this",
    # which is the opposite of the intent. The originating position is still
    # recorded on the trade_commands row for the audit trail.
    return TradeRequest(
        action=TradeAction.DEAL,
        position_id=None,
        symbol=symbol,
        order_type=_same(direction),
        volume=volume,
        filling=filling,
    )


# ------------------------------------------------------------------- outcome


def classify(retcode: int | TradeRetcode | None) -> str:
    """`'done'` or `'failed'`, for the command's stored status.

    A DONE_PARTIAL is `done` — it changed the account, and the caller records the
    ACTUAL filled volume separately. A `None` retcode (the bridge answered
    nothing) is `failed`, but it is NOT proof the order failed; the human-facing
    error text is what carries that distinction, and such a command is never
    auto-retried.
    """
    return "done" if is_success(retcode) else "failed"
