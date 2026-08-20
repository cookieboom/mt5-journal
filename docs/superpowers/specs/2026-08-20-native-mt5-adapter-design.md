# Native MT5 adapter (Windows) — design

Date: 2026-08-20
Status: approved, pending implementation plan

## Problem

The project runs macOS-only today. The only way it talks to MT5 is
`adapter/live.py`'s `LiveMT5Client`, which speaks to a `siliconmetatrader5`
rpyc bridge running inside a Docker container (a Windows MT5 terminal lives
inside that container — see CLAUDE.md § This account). This is a workaround
for macOS not being able to load the MT5 terminal DLL at all.

If this project is instead hosted on a real Windows machine, the workaround
is unnecessary: the official `MetaTrader5` PyPI package talks to a local MT5
terminal directly, no Docker, no rpyc, no port 8001. `siliconmetatrader5`
deliberately mirrors that package's API (same method names, same
`._asdict()`-able namedtuple returns), so the two adapters can share almost
all of their trap-prone conversion logic.

Goal: let the same codebase run against either backend, chosen automatically
by environment, with zero duplicated conversion logic between them.

## Requirements (confirmed with user)

1. **Coexist, auto-detected.** Not a replacement, not a manual switch. On
   Windows with the official package importable, use it; otherwise (macOS,
   Linux, or Windows without a running terminal) fall back to the Docker
   bridge exactly as today.
2. **Full parity, read + write.** The native adapter implements the entire
   `MT5Client` Protocol including `order_check`/`order_send` (M9 write side).
   Live trading and paper trading both work unchanged from a Windows host.
3. **`MetaTrader5` declared with an environment marker**
   (`sys_platform == 'win32'`) in `pyproject.toml`, not a separate extra —
   `uv sync` on macOS must keep working with zero extra steps.

## Non-goals

- No change to `FakeMT5Client` or any test fixture — tests never touch a
  live backend of either kind.
- No change to `MT5Client` Protocol itself (`adapter/base.py`) — both
  backends already fit it.
- Not verified functionally in this repo: there is no Windows machine
  available to this session. `native.py`'s correctness against a real
  terminal is a manual follow-up (`journal doctor` on Windows), not something
  CI or this plan can prove.

## Components

### 1. `adapter/_mt5_common.py` (new)

Extracted, unchanged in behavior, from `adapter/live.py`:

- `_build(cls, raw: dict) -> T` — maps a bridge/native `._asdict()` dict onto
  one of our frozen dataclasses, stashing the full dict in `.raw`.
- `_to_bridge_request(req: TradeRequest) -> dict` — our `TradeRequest` to the
  MT5 wire dict. Carries the None-vs-0.0 distinction for `sl`/`tp` (rule 4)
  and the enum-to-int conversion (rule 12) that both backends need
  identically.
- `_from_bridge_result(res) -> TradeResult` — the MT5 result object (bridge
  netref or native namedtuple) to our `TradeResult`, reading every attribute
  eagerly so a netref's connection lifetime can't bite a lazy read.

These functions take no `self` and touch no MT5 module import — pure
translation, testable exactly as `tests/test_trade_ops.py` already tests
them today (import path changes, test behavior does not).

### 2. `adapter/native.py` (new)

`NativeMT5Client` — the only file besides `live.py` allowed to import an MT5
package (rule 1 permits this: it names the whole `adapter/` directory, not a
single file). Imports the official `MetaTrader5` package.

Mirrors `LiveMT5Client` method for method: `account_info`, `symbol_info`,
`symbol_info_tick`, `symbols_get`, `copy_rates_range`, `history_deals_get`,
`history_orders_get`, `positions_get`, `order_check`, `order_send`. Each
method's body is the same MT5 call as `live.py`, followed by a call into
`_mt5_common`.

Differences from `LiveMT5Client`:

- `__init__(self)` — no `host`/`port`/`keepalive`; calls `mt5.initialize()`
  directly (local terminal, already running and logged in).
- `_assert_enums_match()` — same check as `live.py` (our `IntEnum`s are
  authoritative; verify against whatever constants the native package
  exposes), NOT skipped. Same vendor does not guarantee same values shipped
  in a given release — rule 12 says confirm, never assume.
- `_tf` timeframe map built from the native package's own `TIMEFRAME_*`
  constants.

### 3. `adapter/select.py` (new)

```
def get_client() -> MT5Client:
    if sys.platform == "win32":
        try:
            from .native import NativeMT5Client
            client = NativeMT5Client()
            log.info("adapter: native MetaTrader5 (Windows)")
            return client
        except Exception as exc:
            log.warning("native adapter unavailable (%s); falling back to bridge", exc)
    from .live import LiveMT5Client
    client = LiveMT5Client()
    log.info("adapter: siliconmetatrader5 bridge (Docker)")
    return client
```

Both imports stay lazy (matches the existing lazy-import pattern in
`cli.py`, so the CLI stays importable without either MT5 package present).
A native `ImportError` (package not installed) or `RuntimeError`
(`initialize()` failed — terminal not running/logged in) both fall through
to the bridge; only if *both* fail does the caller see the bridge's
exception, unchanged from today's behavior.

`cli.py`'s 6 call sites (`doctor`, and 5 others per the earlier grep) replace
`from .adapter.live import LiveMT5Client; client = LiveMT5Client()` with
`from .adapter.select import get_client; client = get_client()`.

### 4. `pyproject.toml`

Add one line to `dependencies`:

```
"MetaTrader5; sys_platform == 'win32'",
```

`siliconmetatrader5` stays exactly as-is — still required on every platform,
still the fallback path.

### 5. Docs

`CLAUDE.md` § This account gets one added line under "Adapter" noting the
native path exists and is auto-selected on Windows; the rest of that
section (margin mode, currency, symbols) describes the account, not the
transport, and does not change.

## Testing

- No existing test changes behavior — `_mt5_common.py` extraction is a pure
  move, `tests/test_trade_ops.py` keeps testing the same functions via their
  new import path.
- New: a small contract test asserting `NativeMT5Client` and `LiveMT5Client`
  both satisfy `isinstance(client, MT5Client)` — cheap (the Protocol is
  `runtime_checkable`), catches a missing method on either backend without
  needing a live connection to either one.
- `journal doctor` on an actual Windows box with a real terminal is the only
  real verification of `native.py`'s correctness and is out of scope for
  this repo's automated tests — flagged explicitly rather than claimed.

## Error handling

- `get_client()` logs which backend it picked at INFO, every time — silent
  backend selection would make a future "why is this slow / why did this
  order behave differently" bug much harder to diagnose.
- Native `initialize()` failure (terminal not running, not logged in, wrong
  path) raises the same shape of `RuntimeError` `LiveMT5Client` already
  raises on bridge-init failure, so callers don't need new handling — the
  fallback in `get_client()` is what actually reacts to it.

## Rollback / risk

Additive only: no existing file's behavior changes except `live.py` losing
its three module-level helper functions (moved, not altered) and `cli.py`'s
6 call sites. Both are mechanical, low-risk, verified by the full existing
test suite plus the new contract test.
