"""Indicator script language: a strict subset of Python syntax.

`ast.parse` does the parsing; `parse` then walks the tree and rejects every
node not on the whitelist, so nothing here is ever handed to `eval`/`exec`.
The checked tree is kept as-is and `engine.evaluate` interprets it.

Statements: `name = expr`, `name = input(...)`, `plot(...)`, `hline(...)`,
`signal(side, cond, stop=, target=, target_r=)`, `exit(side, cond)`. Expressions: numbers, booleans, names, arithmetic, comparisons,
`and`/`or`/`not`, builtin calls (positional args only), and `x[n]` with n a
literal integer >= 0 — n bars AGO. A negative or computed index is refused:
that is the language half of the no-lookahead guard.

`tf("H1", expr)` evaluates `expr` on higher-timeframe bars (engine maps only
CLOSED ones back). Inside it: base series, constants, builtins, `x[n]` — no
chart-TF variable (another time axis) and no nested `tf()`.

Window-like builtin arguments (`int`/`float` params) must be constant: a
literal, an input, or arithmetic over those. That keeps every window known
before evaluation, which is what lets `lookback` size the warm-up."""
from __future__ import annotations

import ast
import math
from dataclasses import dataclass, field
from typing import Any, Literal

from ...adapter.base import TIMEFRAMES
from ..resample import timeframe_ms
from .builtins import REGISTRY

MAX_SOURCE = 20_000
MAX_STATEMENTS = 200
MAX_DEPTH = 50
MAX_WINDOW = 5_000
# Total warm-up a script may ask for. Nested smoothing multiplies fast, and
# every compute loads this many bars before the window.
MAX_LOOKBACK = 20_000

SERIES = ("open", "high", "low", "close", "volume", "spread", "hl2", "hlc3", "ohlc4")
OUTPUTS = ("input", "plot", "hline", "signal", "exit", "tf")
# Theme token names from frontend/src/lib/theme.ts — never hex (one palette).
COLORS = ("violet", "cyan", "mark-amber", "mark-sky", "pos", "neg", "warn",
          "mark-chalk", "muted", "ink")
STYLES = ("line", "histogram", "dots")
SIDES = ("long", "short")

_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
_CMPOPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


class ScriptError(ValueError):
    """A script problem the author can fix. `line`/`col` are 1-based."""

    def __init__(self, msg: str, line: int = 0, col: int = 0) -> None:
        super().__init__(f"line {line}:{col}: {msg}" if line else msg)
        self.msg, self.line, self.col = msg, line, col


def error_at(node: ast.AST | None, msg: str) -> ScriptError:
    line, col = getattr(node, "lineno", 0), getattr(node, "col_offset", -1)
    return ScriptError(msg, line, col + 1) if line else ScriptError(msg)


@dataclass(frozen=True)
class Input:
    name: str
    kind: Literal["int", "float", "bool"]
    default: float | bool
    min: float | None = None
    max: float | None = None
    step: float | None = None
    title: str = ""


@dataclass(frozen=True)
class Plot:
    title: str
    expr: ast.expr
    color: str
    pane: str
    style: str
    width: int


@dataclass(frozen=True)
class HLine:
    value: ast.expr
    title: str
    pane: str
    color: str


@dataclass(frozen=True)
class Signal:
    """`stop`/`target`: price-level series read at the signal bar; `target_r`:
    a constant R multiple of the actual risk (exclusive with `target`)."""
    side: str
    expr: ast.expr
    stop: ast.expr | None = None
    target: ast.expr | None = None
    target_r: ast.expr | None = None


@dataclass(frozen=True)
class Exit:
    side: str
    expr: ast.expr


@dataclass(frozen=True)
class Step:
    """One statement, in source order. `kind` = assign | plot | signal | stop |
    target | exit; `target` is the variable name or the index into
    plots/signals/exits (stop/target index their signal)."""
    kind: Literal["assign", "plot", "signal", "stop", "target", "exit"]
    target: str | int
    expr: ast.expr


@dataclass
class Program:
    inputs: list[Input] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    plots: list[Plot] = field(default_factory=list)
    hlines: list[HLine] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    exits: list[Exit] = field(default_factory=list)
    # Names bound to a constant expression (inputs included), in order.
    consts: dict[str, ast.expr] = field(default_factory=dict)
    # Timeframes named by tf() calls.
    htfs: set[str] = field(default_factory=set)


# --- parsing ----------------------------------------------------------------

def parse(source: str) -> Program:
    if len(source) > MAX_SOURCE:
        raise ScriptError(f"script too long ({len(source)} > {MAX_SOURCE} chars)")
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as e:
        raise ScriptError(f"syntax error: {e.msg}", e.lineno or 0, e.offset or 0) from None
    if len(tree.body) > MAX_STATEMENTS:
        raise ScriptError(f"too many statements ({len(tree.body)} > {MAX_STATEMENTS})")
    if _depth(tree) > MAX_DEPTH:
        raise ScriptError(f"expression nested too deep (> {MAX_DEPTH})")

    p = Program()
    defined: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            _assign(p, stmt, defined)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) \
                and isinstance(stmt.value.func, ast.Name) \
                and stmt.value.func.id in ("plot", "hline", "signal", "exit"):
            _output(p, stmt.value, defined)
        else:
            raise error_at(stmt, f"{type(stmt).__name__} statement is not allowed")
    if not p.plots and not p.signals:
        raise ScriptError("a script must plot() or signal() something")
    if p.exits and not p.signals:
        raise ScriptError("exit() without signal(): nothing to exit")
    return p


def _depth(node: ast.AST) -> int:
    kids = list(ast.iter_child_nodes(node))
    return 1 + (max(map(_depth, kids)) if kids else 0)


def _assign(p: Program, stmt: ast.Assign, defined: set[str]) -> None:
    if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
        raise error_at(stmt, "this assignment form is not allowed; use `name = expression`")
    name = stmt.targets[0].id
    if name in SERIES or name in REGISTRY or name in OUTPUTS:
        raise error_at(stmt.targets[0], f"{name!r} is reserved")
    v = stmt.value
    if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "input":
        p.inputs.append(_input(name, v))
        p.consts[name] = v
    else:
        _expr(v, defined, p)
        if _is_const(v, p):
            p.consts[name] = v
        else:
            p.consts.pop(name, None)
        p.steps.append(Step("assign", name, v))
    defined.add(name)


def _kwargs(call: ast.Call, allowed: tuple[str, ...]) -> dict[str, ast.expr]:
    out: dict[str, ast.expr] = {}
    for kw in call.keywords:
        if kw.arg is None or kw.arg not in allowed:
            raise error_at(kw.value, f"unknown keyword {kw.arg!r}; allowed: {', '.join(allowed)}")
        out[kw.arg] = kw.value
    return out


def _literal(node: ast.expr, what: str) -> Any:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) \
            and isinstance(node.operand, ast.Constant) \
            and isinstance(node.operand.value, (int, float)) \
            and not isinstance(node.operand.value, bool):
        return -node.operand.value
    if not isinstance(node, ast.Constant):
        raise error_at(node, f"{what} must be a literal")
    return node.value


def _str(node: ast.expr | None, what: str, default: str,
         choices: tuple[str, ...] | None = None) -> str:
    if node is None:
        return default
    v = _literal(node, what)
    if not isinstance(v, str):
        raise error_at(node, f"{what} must be a string")
    if choices is not None and v not in choices:
        raise error_at(node, f"{what} {v!r} is not one of {', '.join(choices)}")
    return v


def _num(node: ast.expr | None, what: str) -> float | None:
    if node is None:
        return None
    v = _literal(node, what)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise error_at(node, f"{what} must be a number")
    return float(v)


def _input(name: str, call: ast.Call) -> Input:
    if len(call.args) != 1:
        raise error_at(call, "input() takes exactly one default value")
    d = call.args[0]
    if not isinstance(d, (ast.Constant, ast.UnaryOp)):
        raise error_at(d, "input() default must be a literal number or boolean")
    default = _literal(d, "input() default")
    kw = _kwargs(call, ("min", "max", "step", "title"))
    title = _str(kw.get("title"), "input title", name)
    if isinstance(default, bool):
        return Input(name, "bool", default, title=title)
    if not isinstance(default, (int, float)):
        raise error_at(d, "input() default must be a number or boolean")
    kind: Literal["int", "float"] = "int" if isinstance(default, int) else "float"
    return Input(name, kind, default, _num(kw.get("min"), "min"),
                 _num(kw.get("max"), "max"), _num(kw.get("step"), "step"), title)


def _output(p: Program, call: ast.Call, defined: set[str]) -> None:
    fn = call.func.id  # type: ignore[attr-defined]  # checked by the caller
    if fn == "plot":
        if len(call.args) != 1:
            raise error_at(call, "plot() takes one expression")
        kw = _kwargs(call, ("title", "color", "pane", "style", "width"))
        _expr(call.args[0], defined, p)
        i = len(p.plots)
        width = _num(kw.get("width"), "width")
        p.plots.append(Plot(
            title=_str(kw.get("title"), "title", f"plot {i + 1}"),
            expr=call.args[0],
            color=_str(kw.get("color"), "color", COLORS[i % len(COLORS)], COLORS),
            pane=_str(kw.get("pane"), "pane", "price"),
            style=_str(kw.get("style"), "style", "line", STYLES),
            width=max(1, min(4, int(width))) if width is not None else 2,
        ))
        p.steps.append(Step("plot", i, call.args[0]))
    elif fn == "hline":
        if len(call.args) != 1:
            raise error_at(call, "hline() takes one value")
        kw = _kwargs(call, ("title", "pane", "color"))
        _expr(call.args[0], defined, p)
        if not _is_const(call.args[0], p):
            raise error_at(call.args[0], "hline() value must be constant")
        p.hlines.append(HLine(call.args[0], _str(kw.get("title"), "title", ""),
                              _str(kw.get("pane"), "pane", "price"),
                              _str(kw.get("color"), "color", "muted", COLORS)))
    elif fn == "exit":
        if len(call.args) != 2 or call.keywords:
            raise error_at(call, 'exit() takes a side ("long"/"short") and a condition')
        side = _str(call.args[0], "exit side", "", SIDES)
        _expr(call.args[1], defined, p)
        p.exits.append(Exit(side, call.args[1]))
        p.steps.append(Step("exit", len(p.exits) - 1, call.args[1]))
    else:
        if len(call.args) != 2:
            raise error_at(call, 'signal() takes a side ("long"/"short") and a condition')
        kw = _kwargs(call, ("stop", "target", "target_r"))
        side = _str(call.args[0], "signal side", "", SIDES)
        if "target" in kw and "target_r" in kw:
            raise error_at(kw["target_r"], "target and target_r are mutually exclusive")
        for node in (call.args[1], *kw.values()):
            _expr(node, defined, p)
        if "target_r" in kw and not _is_const(kw["target_r"], p):
            raise error_at(kw["target_r"], "target_r must be constant "
                                           "(a number, an input, or arithmetic over those)")
        i = len(p.signals)
        p.signals.append(Signal(side, call.args[1], kw.get("stop"), kw.get("target"),
                                kw.get("target_r")))
        p.steps.append(Step("signal", i, call.args[1]))
        for k in ("stop", "target"):
            if k in kw:
                p.steps.append(Step(k, i, kw[k]))  # type: ignore[arg-type]  # literal k


def _expr(node: ast.expr, defined: set[str], p: Program, inner: bool = False) -> None:
    """Raise unless `node` is a whitelisted expression over defined names.
    `inner`: inside tf() — only base series and constants may be named."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            raise error_at(node, "a string is not a value here")
        if not isinstance(node.value, (int, float, bool)):
            raise error_at(node, f"{type(node.value).__name__} constant is not allowed")
    elif isinstance(node, ast.Name):
        if node.id not in SERIES and node.id not in defined:
            raise error_at(node, f"undefined name {node.id!r}")
        if inner and node.id not in SERIES and node.id not in p.consts:
            raise error_at(node, f"{node.id!r} is on the chart timeframe; "
                                 "assign it inside tf() instead")
    elif isinstance(node, ast.BinOp) and isinstance(node.op, _BINOPS):
        _expr(node.left, defined, p, inner)
        _expr(node.right, defined, p, inner)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
        _expr(node.operand, defined, p, inner)
    elif isinstance(node, ast.BoolOp):
        for v in node.values:
            _expr(v, defined, p, inner)
    elif isinstance(node, ast.Compare) and all(isinstance(o, _CMPOPS) for o in node.ops):
        _expr(node.left, defined, p, inner)
        for c in node.comparators:
            _expr(c, defined, p, inner)
    elif isinstance(node, ast.Subscript):
        n = node.slice
        if not (isinstance(n, ast.Constant) and type(n.value) is int and n.value >= 0):
            raise error_at(n, "history index must be a literal integer >= 0 (bars ago)")
        if n.value > MAX_WINDOW:
            raise error_at(n, f"history index above the window limit ({MAX_WINDOW})")
        _expr(node.value, defined, p, inner)
    elif isinstance(node, ast.Call):
        _call(node, defined, p, inner)
    else:
        raise error_at(node, f"{type(node).__name__} is not allowed")


def _call(node: ast.Call, defined: set[str], p: Program, inner: bool = False) -> None:
    if not isinstance(node.func, ast.Name):
        raise error_at(node, "attribute and method calls are not allowed")
    if node.func.id == "tf":
        _tf(node, defined, p, inner)
        return
    if node.func.id not in REGISTRY:
        raise error_at(node, f"unknown function {node.func.id!r}")
    if node.keywords:
        raise error_at(node.keywords[0].value, "keyword arguments are not allowed on builtins")
    b = REGISTRY[node.func.id]
    required = sum(1 for prm in b.params if prm.default is None)
    if not required <= len(node.args) <= len(b.params):
        raise error_at(node, f"{node.func.id}() takes {required}..{len(b.params)} "
                         f"arguments, got {len(node.args)}")
    for prm, arg in zip(b.params, node.args, strict=False):  # trailing params default
        _expr(arg, defined, p, inner)
        if prm.kind != "series" and not _is_const(arg, p):
            raise error_at(arg, f"{node.func.id}() argument {prm.name!r} must be constant "
                            "(a number, an input, or arithmetic over those)")


def _tf(node: ast.Call, defined: set[str], p: Program, inner: bool) -> None:
    if inner:
        raise error_at(node, "tf() cannot be nested inside tf()")
    if len(node.args) != 2 or node.keywords:
        raise error_at(node, 'tf() takes a timeframe and an expression: tf("H1", expr)')
    tf = node.args[0]
    if not (isinstance(tf, ast.Constant) and isinstance(tf.value, str)):
        raise error_at(tf, 'tf() timeframe must be a string literal, e.g. "H1"')
    if tf.value not in TIMEFRAMES:
        raise error_at(tf, f"unknown timeframe {tf.value!r}; one of {', '.join(TIMEFRAMES)}")
    _expr(node.args[1], defined, p, inner=True)
    p.htfs.add(tf.value)


def tf_of(node: ast.Call) -> str:
    """The timeframe of a validated tf() call."""
    tf = node.args[0]
    assert isinstance(tf, ast.Constant) and isinstance(tf.value, str)  # parse guarantees it
    return tf.value


def check_higher(node: ast.Call, tf_ms: int) -> int:
    """The tf() call's bar length; raise unless it is above the chart's."""
    h = timeframe_ms(tf_of(node))
    if h <= tf_ms:
        raise error_at(node, f"tf({tf_of(node)!r}, …) on this chart: "
                             "tf() only reaches higher timeframes")
    return h


def _is_const(node: ast.expr, p: Program) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float, bool))
    if isinstance(node, ast.Name):
        return node.id in p.consts
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _is_const(node.operand, p)
    if isinstance(node, ast.BinOp) and isinstance(node.op, _BINOPS):
        return _is_const(node.left, p) and _is_const(node.right, p)
    return False


# --- constants and lookback ------------------------------------------------------

def _clamp_input(i: Input, raw: Any) -> float | bool:
    if i.kind == "bool":
        return raw if isinstance(raw, bool) else bool(i.default)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        v = float(i.default)
    else:
        v = float(raw)
    if i.min is not None:
        v = max(i.min, v)
    if i.max is not None:
        v = min(i.max, v)
    return float(round(v)) if i.kind == "int" else v


def const_value(node: ast.expr, consts: dict[str, float | bool]) -> float | bool:
    if isinstance(node, ast.Constant):
        v = node.value
        assert isinstance(v, (int, float, bool))  # _is_const admits only these
        return v
    if isinstance(node, ast.Name):
        return consts[node.id]
    if isinstance(node, ast.UnaryOp):
        v = float(const_value(node.operand, consts))
        return -v if isinstance(node.op, ast.USub) else v
    assert isinstance(node, ast.BinOp)  # _is_const admits nothing else
    a, b = float(const_value(node.left, consts)), float(const_value(node.right, consts))
    op = node.op
    try:
        if isinstance(op, ast.Add):
            return a + b
        if isinstance(op, ast.Sub):
            return a - b
        if isinstance(op, ast.Mult):
            return a * b
        if isinstance(op, ast.Div):
            return a / b
        if isinstance(op, ast.Mod):
            return a % b
        return float(a ** b)
    except (ZeroDivisionError, OverflowError) as e:
        raise error_at(node, f"constant arithmetic failed: {e}") from None


def resolve_consts(p: Program, inputs: dict[str, Any]) -> dict[str, float | bool]:
    """Every constant name's value, user `inputs` applied (clamped to the input's
    min/max, rounded for an int input; a wrong-typed value falls back to the
    default)."""
    by_name = {i.name: i for i in p.inputs}
    out: dict[str, float | bool] = {}
    for name, node in p.consts.items():
        if name in by_name:
            i = by_name[name]
            out[name] = _clamp_input(i, inputs.get(name, i.default))
        else:
            out[name] = const_value(node, out)
    return out


def call_consts(node: ast.Call, consts: dict[str, float | bool]) -> dict[str, float]:
    """The constant (window-like) arguments of a builtin call, defaults filled,
    windows range-checked."""
    b = REGISTRY[node.func.id]  # type: ignore[attr-defined]  # validated call
    out: dict[str, float] = {}
    for i, prm in enumerate(b.params):
        if prm.kind == "series":
            continue
        arg = node.args[i] if i < len(node.args) else None
        v = float(const_value(arg, consts)) if arg is not None else float(prm.default or 0)
        if prm.kind == "int":
            if v != int(v) or not 1 <= v <= MAX_WINDOW:
                raise error_at(arg or node, f"{prm.name} window must be an integer in "
                                        f"1..{MAX_WINDOW}, got {v:g}")
        out[prm.name] = v
    return out


def lookback(p: Program, consts: dict[str, float | bool], tf_ms: int) -> int:
    """Warm-up bars needed before the first displayed bar. Costs SUM along
    nesting (an EMA of an EMA needs both warm-ups) and take the MAX across
    siblings and across outputs. A tf() call costs one HTF bar in chart bars;
    its inner cost is counted in `htf_lookback`."""
    return _lookbacks(p, consts, tf_ms)[0]


def htf_lookback(p: Program, consts: dict[str, float | bool], tf_ms: int) -> dict[str, int]:
    """Warm-up per tf() timeframe, in THAT timeframe's bars."""
    return _lookbacks(p, consts, tf_ms)[1]


def _lookbacks(p: Program, consts: dict[str, float | bool],
               tf_ms: int) -> tuple[int, dict[str, int]]:
    var_lb: dict[str, int] = {}
    htf: dict[str, int] = {}

    def lb(node: ast.expr, bar_ms: int) -> int:
        if isinstance(node, ast.Name):
            return var_lb.get(node.id, 0)
        if isinstance(node, ast.Subscript):
            n = node.slice
            assert isinstance(n, ast.Constant) and type(n.value) is int  # parse guarantees it
            return n.value + lb(node.value, bar_ms)
        if isinstance(node, ast.Call) and node.func.id == "tf":  # type: ignore[attr-defined]
            h = check_higher(node, bar_ms)
            need = lb(node.args[1], h)
            if need > MAX_LOOKBACK:
                raise error_at(node, f"needs {need} {tf_of(node)} bars of warm-up; "
                                     f"the limit is {MAX_LOOKBACK}")
            htf[tf_of(node)] = max(htf.get(tf_of(node), 0), need)
            return math.ceil(h / bar_ms)
        if isinstance(node, ast.Call):
            b = REGISTRY[node.func.id]  # type: ignore[attr-defined]  # validated
            c = call_consts(node, consts)
            inner = [lb(a, bar_ms) for prm, a in zip(b.params, node.args, strict=False)
                     if prm.kind == "series"]
            return b.cost(c, bar_ms) + max(inner, default=0)
        return max((lb(k, bar_ms) for k in ast.iter_child_nodes(node)
                    if isinstance(k, ast.expr)), default=0)

    out = 0
    for step in p.steps:
        n = lb(step.expr, tf_ms)
        if n > MAX_LOOKBACK:
            raise error_at(step.expr, f"needs {n} bars of warm-up; the limit is {MAX_LOOKBACK}")
        if step.kind == "assign":
            assert isinstance(step.target, str)
            var_lb[step.target] = n
        else:
            out = max(out, n)
    return out, htf
