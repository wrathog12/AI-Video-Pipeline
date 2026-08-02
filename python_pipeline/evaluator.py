"""Symbolic evaluation of on-screen values (R4).

This is the module that makes R4 true rather than aspirational. The LLM proposes
an *expression* ("2**8"); Python computes the *result* ("256"). No number that
appears on screen is ever copied from model output.

Two failure modes this closes:

*   **Arithmetic.** A model asked for 256**3 will usually say 16,777,216 and
    occasionally say 16,777,116. Python is never occasionally wrong.
*   **Digit mangling.** Thousands separators, trailing zeros and rounding are all
    produced by one formatter here, so 16777216 cannot reach the screen as
    "16,777,216" in one scene and "16777216.0" in the next.

`eval()` is not used. A whitelisted AST walk is the only way to be sure a model
that emits `__import__("os").system(...)` — accidentally or otherwise — cannot
execute it, since annotator output is untrusted input by construction.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass
from typing import Any, Callable

from .schema import Value

# --------------------------------------------------------------------------
# Limits. An expression is attacker-shaped input: it arrives from a model, and
# `9**9**9**9` is a one-line denial of service that node whitelisting alone
# does not catch.
# --------------------------------------------------------------------------

MAX_EXPR_CHARS = 240
MAX_NODES = 120
MAX_RESULT_BITS = 8192      # ~2466 decimal digits; far beyond anything displayable
MAX_FACTORIAL = 20


class EvaluationError(ValueError):
    """Raised when an expression is unsafe, unparseable, or absurdly large."""


_BINOPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARYOPS: dict[type, Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}


def _factorial(n: Any) -> int:
    if isinstance(n, bool) or not isinstance(n, int):
        raise EvaluationError("factorial() needs an integer")
    if n < 0 or n > MAX_FACTORIAL:
        raise EvaluationError(f"factorial() limited to 0..{MAX_FACTORIAL}, got {n}")
    return math.factorial(n)


def _sum(*args: Any) -> Any:
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        return sum(args[0])
    return sum(args)


_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": _sum,
    "int": int,
    "float": float,
    "sqrt": math.sqrt,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "gcd": math.gcd,
    "lcm": math.lcm,
    "hypot": math.hypot,
    "factorial": _factorial,
    "degrees": math.degrees,
    "radians": math.radians,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
}


def _check_pow(base: Any, exponent: Any) -> None:
    """Refuse exponentiations whose *result* would be unreasonable.

    Checked before the multiply happens: `2**10**9` must never be computed, only
    rejected. Bit-length arithmetic gives the size of the answer without
    producing it.
    """
    if isinstance(exponent, float) and not exponent.is_integer():
        return  # fractional powers stay in float range; math handles overflow
    try:
        exp = int(exponent)
    except (TypeError, ValueError, OverflowError):
        raise EvaluationError(f"Bad exponent: {exponent!r}") from None
    if exp < 0:
        return
    if isinstance(base, float):
        if abs(base) > 1 and exp > 1024:
            raise EvaluationError(f"Exponent too large: {exp}")
        return
    bits = max(int(base).bit_length(), 1)
    if bits * exp > MAX_RESULT_BITS:
        raise EvaluationError(
            f"Result of {base}**{exp} would be ~{bits * exp} bits; "
            f"limit is {MAX_RESULT_BITS}"
        )


def evaluate(expr: str) -> Any:
    """Evaluate one arithmetic expression under a whitelist.

    Returns int, float, or a tuple of those. Anything the whitelist does not
    cover raises EvaluationError naming the offending construct — the message
    reaches the run log, so a bad annotation is diagnosable without a debugger.
    """
    if not isinstance(expr, str) or not expr.strip():
        raise EvaluationError("Empty expression")
    if len(expr) > MAX_EXPR_CHARS:
        raise EvaluationError(f"Expression too long ({len(expr)} > {MAX_EXPR_CHARS} chars)")

    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise EvaluationError(f"Cannot parse {expr!r}: {exc.msg}") from None

    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > MAX_NODES:
        raise EvaluationError(f"Expression too complex ({node_count} nodes)")

    return _eval_node(tree.body)


def _eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise EvaluationError(f"Only numbers allowed, got {node.value!r}")
        return node.value

    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise EvaluationError(f"Operator not allowed: {type(node.op).__name__}")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow):
            _check_pow(left, right)
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise EvaluationError("Division by zero")
        try:
            result = op(left, right)
        except (OverflowError, ValueError, TypeError) as exc:
            raise EvaluationError(f"Arithmetic error: {exc}") from None
        _check_size(result)
        return result

    if isinstance(node, ast.UnaryOp):
        op = _UNARYOPS.get(type(node.op))
        if op is None:
            raise EvaluationError(f"Unary operator not allowed: {type(node.op).__name__}")
        return op(_eval_node(node.operand))

    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise EvaluationError(
            f"Unknown name {node.id!r} (available: {', '.join(sorted(_CONSTANTS))})"
        )

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise EvaluationError("Only direct calls to whitelisted functions are allowed")
        fn = _FUNCTIONS.get(node.func.id)
        if fn is None:
            raise EvaluationError(f"Function not allowed: {node.func.id}()")
        if node.keywords:
            raise EvaluationError(f"{node.func.id}() takes no keyword arguments here")
        args = [_eval_node(a) for a in node.args]
        try:
            result = fn(*args)
        except EvaluationError:
            raise
        except (TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
            raise EvaluationError(f"{node.func.id}(): {exc}") from None
        _check_size(result)
        return result

    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(_eval_node(e) for e in node.elts)

    raise EvaluationError(f"Syntax not allowed: {type(node).__name__}")


def _check_size(value: Any) -> None:
    if isinstance(value, int) and value.bit_length() > MAX_RESULT_BITS:
        raise EvaluationError(f"Result too large ({value.bit_length()} bits)")


# --------------------------------------------------------------------------
# Formatting. One place, so identical numbers never render two ways (R4).
# --------------------------------------------------------------------------


def format_value(value: Any, fmt: str) -> str:
    if isinstance(value, tuple):
        parts = [format_value(v, "raw") for v in value]
        if fmt == "range":
            if len(parts) != 2:
                raise EvaluationError(f"'range' format needs 2 values, got {len(parts)}")
            return f"{parts[0]}-{parts[1]}"
        return "(" + ", ".join(parts) + ")"

    if fmt == "int":
        return f"{round(value):d}"

    if fmt == "thousands":
        return f"{round(value):,d}"

    if fmt == "float":
        return _trim_float(value)

    if fmt == "percent":
        # The expression must already yield a percentage ("12/48*100"), not a
        # fraction. Silently multiplying by 100 when the value happens to be
        # <= 1 would make 0.5% render as 50%.
        return f"{_trim_float(value)}%"

    if fmt in ("raw", "tuple", "range"):
        return _trim_float(value) if isinstance(value, float) else str(value)

    raise EvaluationError(f"Unknown format: {fmt!r}")


def _trim_float(value: Any) -> str:
    """Render a float readably and deterministically.

    Fixed rules rather than repr(): repr(0.1 + 0.2) is "0.30000000000000004",
    and that is a mangled digit on screen.
    """
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):
        raise EvaluationError(f"Non-finite result: {value!r}")
    if v == int(v) and abs(v) < 1e15:
        return f"{int(v):d}"
    text = f"{v:.2f}" if abs(v) >= 1 else f"{v:.4g}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


# --------------------------------------------------------------------------
# Applying it to the IR
# --------------------------------------------------------------------------


@dataclass
class Resolution:
    """One resolved value, for the run log and for --explain-values."""

    path: str            # e.g. "scene_02.expression"
    label: str
    expr: str | None
    format: str
    resolved: str
    computed: bool       # True = Python computed it; False = literal text


# Keys that make a dict recognisable as a Value. `resolved` alone is not enough:
# an unrelated prop could legitimately carry that name.
_VALUE_KEYS = set(Value.model_fields)
_VALUE_MARKERS = {"expr", "format", "resolved"}


def _looks_like_value(obj: Any) -> bool:
    return (
        isinstance(obj, dict)
        and bool(_VALUE_MARKERS & obj.keys())
        and set(obj.keys()) <= _VALUE_KEYS
    )


# A literal that is entirely digits/separators is an authored number — the thing
# R4 forbids. "0-255" and "8-bit" are ranges/labels and pass.
_AUTHORED_NUMBER = re.compile(r"^-?\d+(?:[,\d]*)?(?:\.\d+)?%?$")


def resolve_props(props: Any, *, path: str = "") -> tuple[Any, list[Resolution]]:
    """Walk a props tree and fill in every Value's `resolved`.

    Returns a new tree plus a record of what happened, so the caller can log
    "resolved 6 values, 5 computed" instead of trusting silently.

    A Value with no `expr` keeps its `resolved` text verbatim — that is the path
    for non-numeric text quoted from the script ("0-255", "RGB"). Such a value is
    flagged `computed=False`, and rejected outright if it looks like a bare
    number, because an authored number is exactly what R4 forbids.
    """
    if _looks_like_value(props):
        value = Value.model_validate(props)
        record, resolved = _resolve_one(value, path)
        return resolved.model_dump(), [record]

    if isinstance(props, dict):
        out: dict[str, Any] = {}
        records: list[Resolution] = []
        for key, val in props.items():
            child, child_records = resolve_props(val, path=f"{path}.{key}" if path else key)
            out[key] = child
            records.extend(child_records)
        return out, records

    if isinstance(props, list):
        out_list: list[Any] = []
        list_records: list[Resolution] = []
        for i, val in enumerate(props):
            child, child_records = resolve_props(val, path=f"{path}[{i}]")
            out_list.append(child)
            list_records.extend(child_records)
        return out_list, list_records

    return props, []


def _resolve_one(value: Value, path: str) -> tuple[Resolution, Value]:
    if value.expr:
        computed = evaluate(value.expr)
        text = format_value(computed, value.format)
        return (
            Resolution(path, value.label, value.expr, value.format, text, True),
            value.model_copy(update={"resolved": text}),
        )

    literal = (value.resolved or "").strip()
    if not literal:
        raise EvaluationError(
            f"{path or 'value'} ({value.label!r}) has neither `expr` nor `resolved`. "
            "Every on-screen value needs an expression to compute or literal text to show."
        )
    if _AUTHORED_NUMBER.match(literal):
        raise EvaluationError(
            f"{path or 'value'} ({value.label!r}) carries the authored number "
            f"{literal!r} with no expression. Numbers on screen must be computed "
            "(R4): supply `expr` instead, e.g. expr=\"2**8\"."
        )
    return (
        Resolution(path, value.label, None, value.format, literal, False),
        value.model_copy(update={"resolved": literal}),
    )


def resolve_scene_spec(spec: Any) -> list[Resolution]:
    """Resolve every scene's props in place. Returns all resolutions."""
    records: list[Resolution] = []
    for scene in spec.scenes:
        props, scene_records = resolve_props(scene.props, path=scene.scene_id)
        scene.props = props
        records.extend(scene_records)
    return records


def render_resolutions(records: list[Resolution]) -> str:
    """Human-readable table for --explain-values."""
    if not records:
        return "no values to resolve"
    width = max(len(r.path) for r in records)
    lines = [f"{'PATH'.ljust(width)}  SOURCE      RESOLVED"]
    for r in records:
        source = (r.expr or "(literal)")[:24]
        lines.append(f"{r.path.ljust(width)}  {source.ljust(24)}  {r.resolved}")
    computed = sum(1 for r in records if r.computed)
    lines.append(f"\n{len(records)} values, {computed} computed from expressions")
    return "\n".join(lines)
