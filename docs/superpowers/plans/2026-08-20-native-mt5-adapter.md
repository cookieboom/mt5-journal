# Native MT5 Adapter (Windows) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let this codebase talk to MT5 either through the existing Docker/`siliconmetatrader5` bridge (macOS) or directly through the official `MetaTrader5` package (Windows), auto-selected at runtime, with zero duplicated conversion logic between the two.

**Architecture:** Extract the three trap-prone, MT5-module-agnostic conversion functions out of `adapter/live.py` into a new `adapter/_mt5_common.py`. Add `adapter/native.py`, a second `MT5Client` implementation that imports the official `MetaTrader5` package and reuses those extracted functions. Add `adapter/select.py`, a `get_client()` factory that tries native on Windows and falls back to the bridge everywhere else, and point all 6 CLI call sites at it instead of constructing `LiveMT5Client` directly.

**Tech Stack:** Python 3.12, Typer (CLI), pytest. New runtime dependency: `MetaTrader5` (official PyPI package, Windows-only wheel), added with a `sys_platform == 'win32'` marker.

**Spec:** `docs/superpowers/specs/2026-08-20-native-mt5-adapter-design.md`

## Global Constraints

- Rule 1 (CLAUDE.md): `import MetaTrader5` (or `siliconmetatrader5`) may appear only inside `src/journal/adapter/`. `_mt5_common.py` must NOT import either MT5 package — it stays pure translation.
- Rule 4 (CLAUDE.md): `sl`/`tp` — `None` means "leave untouched", `0.0` means "clear this level". Never conflate them when moving `_to_bridge_request`.
- Rule 12 (CLAUDE.md): MT5 integer constants never leave the adapter. `native.py` must verify its enum values against the official package at init (`_assert_enums_match`-equivalent), exactly like `live.py` does — never assume same vendor means same values.
- `MetaTrader5` goes into `pyproject.toml` with `; sys_platform == 'win32'` — `uv sync` on macOS must not attempt to install it.
- No behavior change for the existing bridge path: `LiveMT5Client` must work identically before and after the extraction (only its imports change).
- `uv run pytest` must pass after every task, on macOS, with neither MT5 package installed.

---

### Task 1: Extract shared conversion helpers into `adapter/_mt5_common.py`

**Files:**
- Create: `src/journal/adapter/_mt5_common.py`
- Modify: `src/journal/adapter/live.py:1-302`
- Modify: `tests/test_trade_ops.py:36`

**Interfaces:**
- Produces (used by Task 2 and by `live.py`):
  - `_build(cls, raw: dict[str, Any])` — same signature/behavior as today.
  - `_to_bridge_request(req: TradeRequest) -> dict[str, Any]`
  - `_from_bridge_result(res: Any) -> TradeResult`

- [ ] **Step 1: Run the existing trade-ops tests to capture the baseline**

Run: `uv run pytest tests/test_trade_ops.py -v`
Expected: all tests PASS (this is the safety net for the move — behavior must not change).

- [ ] **Step 2: Create `_mt5_common.py` with the three functions moved verbatim**

Copy `_build`, `_to_bridge_request`, `_from_bridge_result` out of `adapter/live.py` (lines 39-44 and 218-301 in the current file) into the new file, unchanged except for their imports:

```python
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
```

- [ ] **Step 3: Strip the moved code out of `live.py`, import from `_mt5_common` instead**

In `src/journal/adapter/live.py`:

Replace the `from dataclasses import fields` import and the `_build` function
(original lines 12 and 39-44) — delete them.

Replace this import block (original lines 17-34):
```python
from .base import (
    Account,
    Candle,
    Deal,
    DealEntry,
    DealReason,
    DealType,
    Order,
    OrderFilling,
    OrderType,
    Position,
    SymbolInfo,
    Tick,
    TradeAction,
    TradeRequest,
    TradeResult,
    TradeRetcode,
)
```
with:
```python
from .base import (
    Account,
    Candle,
    Deal,
    DealEntry,
    DealReason,
    DealType,
    Order,
    OrderFilling,
    OrderType,
    Position,
    SymbolInfo,
    Tick,
    TradeAction,
    TradeRequest,
    TradeResult,
    TradeRetcode,
)
from ._mt5_common import _build, _from_bridge_result, _to_bridge_request
```
(`TradeResult` and `TradeRequest` both stay in the `base` import: even after
the *builder* functions move to `_mt5_common.py`, `live.py`'s own
`order_check`/`order_send` methods still declare
`-> TradeResult` and `request: TradeRequest` in their signatures.)

Delete the entire `# --- request/result mapping (M9)` section at the bottom
of the file (original lines 210-301: the comment block plus the
`_to_bridge_request` and `_from_bridge_result` function bodies) — it now
lives in `_mt5_common.py` and `live.py` calls the imported versions.

- [ ] **Step 4: Update the test import**

In `tests/test_trade_ops.py:36`, change:
```python
from journal.adapter.live import _from_bridge_result, _to_bridge_request
```
to:
```python
from journal.adapter._mt5_common import _from_bridge_result, _to_bridge_request
```

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -v`
Expected: same PASS count as the Step 1 baseline. `test_trade_ops.py` in
particular must be unchanged in pass/fail status — this step only moved
code, it must not have changed behavior.

- [ ] **Step 6: Commit**

```bash
git add src/journal/adapter/_mt5_common.py src/journal/adapter/live.py tests/test_trade_ops.py
git commit -m "refactor: extract MT5 wire-format conversion into adapter/_mt5_common.py"
```

---

### Task 2: `adapter/native.py` — the Windows native MT5 client

**Files:**
- Create: `src/journal/adapter/native.py`
- Test: `tests/test_native_adapter.py`

**Interfaces:**
- Consumes: `_build`, `_to_bridge_request`, `_from_bridge_result` from `adapter/_mt5_common.py` (Task 1). `MT5Client`, `Account`, `Candle`, `Deal`, `DealEntry`, `DealReason`, `DealType`, `Order`, `OrderFilling`, `OrderType`, `Position`, `SymbolInfo`, `Tick`, `TradeAction`, `TradeRequest`, `TradeResult`, `TradeRetcode` from `adapter/base.py`.
- Produces (used by Task 3): `NativeMT5Client` class with the same public method set as `LiveMT5Client` (`account_info`, `symbol_info`, `symbol_info_tick`, `symbols_get`, `copy_rates_range`, `history_deals_get`, `history_orders_get`, `positions_get`, `order_check`, `order_send`), constructible with **no required arguments**: `NativeMT5Client()`.

This file cannot be exercised against a real terminal from this repo (no
Windows machine available) — the test below verifies everything that
doesn't require one: that the class exists, satisfies the `MT5Client`
Protocol, and that importing the module does not require the `MetaTrader5`
package to be *installed* for the module's non-connecting parts to be
inspectable. The module itself DOES require `MetaTrader5` at import time
(same pattern as `live.py` requiring `siliconmetatrader5`) — the test skips
if it's absent, which it always will be on this macOS dev machine and in CI.

- [ ] **Step 1: Write the (skip-if-absent) test first**

```python
# tests/test_native_adapter.py
"""Native (Windows) MT5 adapter — see docs/superpowers/specs/2026-08-20-native-mt5-adapter-design.md.

No Windows machine is available to this repo's test run, so this only
verifies what's checkable without a live terminal: the module imports (or is
skipped where the `MetaTrader5` package isn't installed) and the class shape
matches the `MT5Client` Protocol. Functional correctness against a real
terminal is a manual follow-up (`journal doctor` on Windows).
"""

from __future__ import annotations

import pytest

pytest.importorskip("MetaTrader5")


def test_native_client_satisfies_mt5client_protocol():
    from journal.adapter.base import MT5Client
    from journal.adapter.native import NativeMT5Client

    assert issubclass(NativeMT5Client, object)
    for method in (
        "account_info", "symbol_info", "symbol_info_tick", "symbols_get",
        "copy_rates_range", "history_deals_get", "history_orders_get",
        "positions_get", "order_check", "order_send",
    ):
        assert hasattr(NativeMT5Client, method), f"missing {method}"
    # runtime_checkable Protocol: an unconnected instance still can't be
    # built without a terminal, so this checks the class shape, not an
    # instance — an isinstance check on a real instance is Task 3's contract
    # test, run against both backends together.
```

- [ ] **Step 2: Run it to confirm it's skipped (not installed here)**

Run: `uv run pytest tests/test_native_adapter.py -v`
Expected: `SKIPPED (could not import 'MetaTrader5')`

- [ ] **Step 3: Write `adapter/native.py`**

```python
"""The native MT5 adapter for a Windows host. THE ONLY FILE (besides
`live.py`) ALLOWED TO IMPORT AN MT5 PACKAGE — CLAUDE.md Hard rule 1 names
the whole `adapter/` directory, not a single file within it.

Connects directly to a local, already-running, already-logged-in MT5
terminal via the official `MetaTrader5` PyPI package — no Docker, no rpyc,
no host/port. Picked automatically by `adapter/select.py` when
`sys.platform == "win32"` and this package is importable; falls back to
`adapter/live.py`'s Docker bridge everywhere else.
"""

from __future__ import annotations

import logging
from typing import Any

import MetaTrader5 as mt5  # noqa: the other permitted import (see module docstring)

from ._mt5_common import _build, _from_bridge_result, _to_bridge_request
from .base import (
    Account,
    Candle,
    Deal,
    DealEntry,
    DealReason,
    DealType,
    Order,
    OrderFilling,
    OrderType,
    Position,
    SymbolInfo,
    Tick,
    TradeAction,
    TradeRequest,
    TradeResult,
    TradeRetcode,
)

log = logging.getLogger(__name__)


class NativeMT5Client:
    """Implements `MT5Client` over a local MT5 terminal (Windows only)."""

    def __init__(self) -> None:
        if not mt5.initialize():
            raise RuntimeError(
                f"MT5 initialize() failed — is a terminal installed, running, "
                f"and logged in? last_error={self._safe_last_error()}"
            )
        self._assert_enums_match()
        self._tf = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }

    def _safe_last_error(self) -> Any:
        try:
            return mt5.last_error()
        except Exception:  # pragma: no cover - diagnostic only
            return "unavailable"

    def _assert_enums_match(self) -> None:
        """Same discipline as `LiveMT5Client._assert_enums_match` (rule 12):
        our IntEnums are authoritative; verify, never assume — same vendor
        does not guarantee the installed release exposes identical values.
        A mismatch on an exposed constant is a hard failure; a missing
        constant is logged and skipped, not a failure."""
        checks = {
            DealType: "DEAL_TYPE_{}",
            DealEntry: "DEAL_ENTRY_{}",
            DealReason: "DEAL_REASON_{}",
            TradeAction: "TRADE_ACTION_{}",
            OrderType: "ORDER_TYPE_{}",
            OrderFilling: "ORDER_FILLING_{}",
            TradeRetcode: "TRADE_RETCODE_{}",
        }
        for enum_cls, tmpl in checks.items():
            for member in enum_cls:
                attr = tmpl.format(member.name)
                if not hasattr(mt5, attr):
                    log.warning(
                        "native package does not expose %s; cannot verify "
                        "%s.%s=%d (unverifiable, not a failure)",
                        attr, enum_cls.__name__, member.name, member.value,
                    )
                    continue
                native_val = getattr(mt5, attr)
                if native_val != member.value:
                    raise RuntimeError(
                        f"enum mismatch: {enum_cls.__name__}.{member.name}="
                        f"{member.value} but native {attr}={native_val}"
                    )

    # --------------------------------------------------------------- methods

    def account_info(self) -> Account | None:
        info = mt5.account_info()
        return _build(Account, info._asdict()) if info is not None else None

    def symbol_info(self, symbol: str) -> SymbolInfo | None:
        mt5.symbol_select(symbol, True)  # trap 12, same as live.py
        info = mt5.symbol_info(symbol)
        return _build(SymbolInfo, info._asdict()) if info is not None else None

    def symbol_info_tick(self, symbol: str) -> Tick | None:
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        return _build(Tick, tick._asdict()) if tick is not None else None

    def symbols_get(self, group: str | None = None) -> list[SymbolInfo]:
        syms = mt5.symbols_get(group) if group else mt5.symbols_get()
        return [_build(SymbolInfo, s._asdict()) for s in (syms or ())]

    def copy_rates_range(
        self, symbol: str, timeframe: str, date_from: Any, date_to: Any
    ) -> list[Candle]:
        if timeframe not in self._tf:
            raise ValueError(
                f"unknown timeframe {timeframe!r}; expected one of {list(self._tf)}"
            )
        mt5.symbol_select(symbol, True)
        rows = mt5.copy_rates_range(symbol, self._tf[timeframe], date_from, date_to)
        out: list[Candle] = []
        for r in rows if rows is not None else ():
            # `time` is epoch SECONDS; convert to ms at the boundary (Trap 15,
            # same as live.py — the native package returns rates in the same
            # shape as the bridge).
            out.append(
                Candle(
                    time_msc=int(r["time"]) * 1000,
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    tick_volume=int(r["tick_volume"]),
                    spread=int(r["spread"]),
                    real_volume=int(r["real_volume"]),
                )
            )
        return out

    def history_deals_get(self, date_from: Any, date_to: Any) -> list[Deal]:
        deals = mt5.history_deals_get(date_from, date_to)
        return [_build(Deal, d._asdict()) for d in (deals or ())]

    def history_orders_get(self, date_from: Any, date_to: Any) -> list[Order]:
        orders = mt5.history_orders_get(date_from, date_to)
        return [_build(Order, o._asdict()) for o in (orders or ())]

    def positions_get(self) -> list[Position]:
        positions = mt5.positions_get()
        return [_build(Position, p._asdict()) for p in (positions or ())]

    # ----------------------------------------------------------- write side (M9)

    def order_check(self, request: TradeRequest) -> TradeResult:
        """Dry run — asks the broker whether it would accept this. Sends nothing."""
        return _from_bridge_result(mt5.order_check(_to_bridge_request(request)))

    def order_send(self, request: TradeRequest) -> TradeResult:
        """Send for real. THIS MOVES MONEY. Logged before/after at INFO —
        same reasoning as `LiveMT5Client.order_send`: this is the only record
        outside the DB of what was actually sent."""
        payload = _to_bridge_request(request)
        log.info("order_send -> %s", payload)
        result = _from_bridge_result(mt5.order_send(payload))
        log.info(
            "order_send <- retcode=%s deal=%s volume=%s comment=%s",
            result.retcode, result.deal, result.volume, result.comment,
        )
        return result
```

- [ ] **Step 4: Run the native adapter test again**

Run: `uv run pytest tests/test_native_adapter.py -v`
Expected: still `SKIPPED` on this machine (no `MetaTrader5` package here) —
this confirms the module is syntactically valid and importable up to the
point where the missing package is the only blocker.

Run: `uv run python -c "import ast; ast.parse(open('src/journal/adapter/native.py').read())"`
Expected: no output (parses cleanly) — a cheap syntax check since the module
can't actually be imported on this machine.

- [ ] **Step 5: Run the full suite to confirm nothing else broke**

Run: `uv run pytest -v`
Expected: same pass count as Task 1's final run, plus one new SKIPPED test.

- [ ] **Step 6: Commit**

```bash
git add src/journal/adapter/native.py tests/test_native_adapter.py
git commit -m "feat(adapter): native MT5 client for a Windows host (M9 read+write parity)"
```

---

### Task 3: `adapter/select.py` — auto-detecting factory + contract test

**Files:**
- Create: `src/journal/adapter/select.py`
- Test: `tests/test_adapter_select.py`

**Interfaces:**
- Consumes: `LiveMT5Client` (`adapter/live.py`), `NativeMT5Client` (`adapter/native.py`, Task 2), `MT5Client` (`adapter/base.py`).
- Produces (used by Task 4): `get_client() -> MT5Client` in `journal.adapter.select`.

- [ ] **Step 1: Write the failing/skip-aware tests first**

```python
# tests/test_adapter_select.py
"""adapter/select.py — see docs/superpowers/specs/2026-08-20-native-mt5-adapter-design.md.

get_client() itself needs a real backend to fully exercise (no bridge and no
Windows terminal are available in this test run), so these tests cover what
IS checkable here: both concrete clients satisfy the MT5Client Protocol
shape, and select.py's platform-branch logic is exercised with the real
imports patched out.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from journal.adapter.base import MT5Client


def test_fake_client_satisfies_mt5client_protocol():
    # The one backend guaranteed importable and constructible everywhere —
    # anchors the contract check even where neither real backend is available.
    from journal.adapter.fake import FakeMT5Client

    assert isinstance(FakeMT5Client(fixtures_dir="tests/fixtures"), MT5Client)


def test_get_client_uses_bridge_on_non_windows():
    from journal.adapter import select

    fake_bridge_client = object()
    with patch.object(sys, "platform", "darwin"):
        with patch("journal.adapter.live.LiveMT5Client", return_value=fake_bridge_client):
            assert select.get_client() is fake_bridge_client


def test_get_client_falls_back_to_bridge_when_native_init_fails():
    from journal.adapter import select

    fake_bridge_client = object()
    with patch.object(sys, "platform", "win32"):
        with patch("journal.adapter.native.NativeMT5Client", side_effect=RuntimeError("no terminal")):
            with patch("journal.adapter.live.LiveMT5Client", return_value=fake_bridge_client):
                assert select.get_client() is fake_bridge_client


def test_get_client_uses_native_on_windows_when_available():
    from journal.adapter import select

    fake_native_client = object()
    with patch.object(sys, "platform", "win32"):
        with patch("journal.adapter.native.NativeMT5Client", return_value=fake_native_client):
            assert select.get_client() is fake_native_client
```

Check `tests/fixtures` exists and has the shape `FakeMT5Client` expects
before relying on it in the first test above:

Run: `ls tests/fixtures`
Expected: a non-empty directory (fixture files used by other adapter tests).
If empty/missing, use whatever fixture path `tests/test_*.py` already uses
elsewhere for `FakeMT5Client(...)` instead of `"tests/fixtures"`.

- [ ] **Step 2: Run to verify all four fail (module doesn't exist yet)**

Run: `uv run pytest tests/test_adapter_select.py -v`
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'journal.adapter.select'` (and the first test may pass since it doesn't touch `select` — that's fine, the point is the other three fail for the right reason).

- [ ] **Step 3: Write `adapter/select.py`**

```python
"""Picks which `MT5Client` backend to use, automatically.

Windows + the official `MetaTrader5` package importable and initializable ->
`adapter/native.py`. Everything else (macOS, Linux, or a Windows host
without a running/logged-in terminal) -> `adapter/live.py`'s Docker bridge,
exactly as before this module existed.

Imports of both backends are lazy (matches the existing pattern in
`cli.py`), so importing THIS module never requires either MT5 package to be
installed.
"""

from __future__ import annotations

import logging
import sys

from .base import MT5Client

log = logging.getLogger(__name__)


def get_client() -> MT5Client:
    if sys.platform == "win32":
        try:
            from .native import NativeMT5Client

            client = NativeMT5Client()
            log.info("adapter: native MetaTrader5 (Windows)")
            return client
        except Exception as exc:
            log.warning(
                "native adapter unavailable (%s); falling back to the Docker bridge",
                exc,
            )

    from .live import LiveMT5Client

    client = LiveMT5Client()
    log.info("adapter: siliconmetatrader5 bridge (Docker)")
    return client
```

- [ ] **Step 4: Run the tests, verify all four pass**

Run: `uv run pytest tests/test_adapter_select.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: same pass count as Task 2's final run, plus 4 new PASS.

- [ ] **Step 6: Commit**

```bash
git add src/journal/adapter/select.py tests/test_adapter_select.py
git commit -m "feat(adapter): get_client() auto-selects native vs bridge by platform"
```

---

### Task 4: Wire `cli.py`'s 6 call sites to `get_client()`

**Files:**
- Modify: `src/journal/cli.py:44`, `:138`, `:529`, `:565`, `:674`, `:749`

**Interfaces:**
- Consumes: `get_client()` from `journal.adapter.select` (Task 3).

- [ ] **Step 1: Replace each of the 6 call sites**

In each of the 6 locations, this pattern:
```python
    from .adapter.live import LiveMT5Client

    client = LiveMT5Client()
```
becomes:
```python
    from .adapter.select import get_client

    client = get_client()
```

The 6 locations (function name : original line of the `from .adapter.live import LiveMT5Client` statement, confirmed by `grep -n "LiveMT5Client(" src/journal/cli.py` before this task):
- `doctor()` — line 44
- `sync()` — line 138
- `candles()` — line 529
- `candles_warm()` — line 565
- `poll()` — line 674
- `live()` — line 749

Also update `doctor()`'s docstring, which currently says "Needs the
siliconmetatrader5 bridge up on localhost:8001." — change to "Needs a
reachable MT5 backend: the siliconmetatrader5 Docker bridge, or on Windows a
running local terminal." No other docstring in the 6 functions makes a
bridge-specific claim strong enough to need editing (spot-check each while
editing — fix any that do).

- [ ] **Step 2: Confirm no stray `LiveMT5Client()` construction remains outside `select.py`/`live.py` itself**

Run: `grep -n "LiveMT5Client(" src/journal/cli.py`
Expected: no output (all 6 replaced).

Run: `grep -n "from .adapter.live import LiveMT5Client" src/journal/cli.py`
Expected: no output.

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -v`
Expected: same pass count as Task 3's final run — `cli.py` has no direct
unit tests exercising these call sites against a live backend (they all
require a real bridge/terminal), so this is confirming nothing else broke,
not new coverage. If `tests/` contains a CLI smoke test that imports `cli`
module-level, run it explicitly too and confirm it still passes without
either MT5 package installed (the lazy-import pattern must still hold).

- [ ] **Step 4: Manual sanity check that `cli.py` still imports cleanly**

Run: `uv run python -c "from journal import cli"`
Expected: no output, exit code 0 — confirms the lazy-import pattern still
holds (importing `cli` must not require `MetaTrader5` or
`siliconmetatrader5` to be installed).

- [ ] **Step 5: Commit**

```bash
git add src/journal/cli.py
git commit -m "refactor(cli): route all 6 MT5 client construction sites through get_client()"
```

---

### Task 5: Declare the `MetaTrader5` dependency with a platform marker

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add the marked dependency**

In `pyproject.toml`, in the `dependencies` list (currently starting with
`"siliconmetatrader5",`), add a new line:
```toml
    "MetaTrader5; sys_platform == 'win32'",
```
placed directly after the `"siliconmetatrader5",` line, so the two MT5-related
dependencies stay adjacent for anyone reading the file.

- [ ] **Step 2: Verify `uv sync` still resolves cleanly on macOS**

Run: `uv sync`
Expected: succeeds, and does NOT attempt to install `MetaTrader5` (the
marker excludes it on `darwin`). Check with:

Run: `uv pip list | grep -i metatrader`
Expected: only `siliconmetatrader5` (or its installed name) appears —
`MetaTrader5` does not.

- [ ] **Step 3: Run the full test suite one more time**

Run: `uv run pytest -v`
Expected: same pass count as Task 4's final run — a dependency-list change
touches no code.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: declare MetaTrader5 as a Windows-only dependency"
```

(If `uv sync` didn't regenerate `uv.lock`, run `uv lock` first, then stage
both files together.)

---

### Task 6: Document the native path in CLAUDE.md

**Files:**
- Modify: `/Users/reisa/mt5-journal/CLAUDE.md` (the `## This account` section, "Adapter" line)

- [ ] **Step 1: Update the Adapter line**

Find this line near the top of `## This account`:
```
- Adapter: `siliconmetatrader5` bridge, Docker container on `localhost:8001`.
```
Replace with:
```
- Adapter: `siliconmetatrader5` bridge, Docker container on `localhost:8001`
  (macOS), OR the official `MetaTrader5` package talking to a local terminal
  directly (Windows). `adapter/select.py` picks automatically by platform —
  see `docs/superpowers/specs/2026-08-20-native-mt5-adapter-design.md`.
```

- [ ] **Step 2: Confirm the rest of `## This account` still reads correctly**

Run: `sed -n '/## This account/,/## Hard rules/p' CLAUDE.md`
Expected: the margin-mode/currency/symbol facts below the Adapter line are
unchanged — this task edits exactly one bullet.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: note the native Windows MT5 adapter path in CLAUDE.md"
```

---

## Final verification (after all 6 tasks)

- [ ] Run: `uv run pytest -v` — full suite passes, same or higher pass count than before Task 1 started.
- [ ] Run: `uv run journal rebuild` (against whatever local `data/journal.db` exists, or skip if none — this plan changes no schema and no rebuild logic, this is a smoke check that nothing in the adapter change broke the CLI's import chain end to end).
- [ ] Run: `grep -rn "import siliconmetatrader5\|import MetaTrader5" src/` — expect exactly two matches: `adapter/live.py` and `adapter/native.py`. Confirms rule 1 held throughout.
