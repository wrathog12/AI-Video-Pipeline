"""What the dashboard is allowed to change, and which options are real.

## The one rule this module exists to enforce

**A control must not offer an option that would crash, and must not offer one that
changes nothing.** Both failures are worse than omitting the control: the first
turns a config dropdown into a live-review crash, and the second is precisely the
bug the font work uncovered — `theme.font_family` was selectable for three phases
while no font was ever loaded, so the setting was decoration.

So availability is *probed*, not declared. `piper` and `whisperx` appear in
`config.yaml` comments as planned providers, and both raise `ImportError` on the
empty modules that stand in for them. They are listed here as unavailable, with the
reason shown in the UI, rather than silently omitted — a reviewer should be able to
see the seam exists and that this end of it is unbuilt.

## Why the whitelist is explicit and small

The dashboard writes a config file that a subprocess then loads, so anything it can
put in that file is executed by the pipeline. `providers.renderer` is not settable
from the UI at all: it selects a code path that shells out to `npx`, and there is
exactly one implementation, so a dropdown would be an attack surface with no
feature behind it. Only the fields below can be set, each is coerced to its declared
type, and numbers are clamped to a stated range.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any, Callable

from ..vendor_fonts import available_families

ROOT = Path(__file__).resolve().parents[2]

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


# ---------------------------------------------------------------------------
# Availability probes
# ---------------------------------------------------------------------------


def _module_importable(dotted: str, symbol: str) -> tuple[bool, str]:
    """Whether a provider's module can actually be imported.

    Import, not file existence: `tts/piper.py` exists and is 0 bytes, so an
    existence check would report it as available and the run would then fail with
    ImportError several seconds in — after the UI had already claimed success at
    accepting the setting.
    """
    try:
        module = importlib.import_module(dotted, package="python_pipeline")
    except Exception as exc:  # noqa: BLE001 - any import failure means unavailable
        return False, f"{type(exc).__name__}: {exc}"
    if not hasattr(module, symbol):
        return False, f"{dotted} defines no {symbol} (module is a stub)"
    return True, ""


def _icon_pack_status() -> tuple[bool, str]:
    from ..assets.icon_pack import IconPackProvider

    count = IconPackProvider().available()
    if count == 0:
        return False, "no SVGs vendored — run `python -m python_pipeline.assets.vendor_icons`"
    return True, f"{count} icons vendored"


def _llm_status(model: str) -> tuple[bool, str]:
    """An LLM option is available if its SDK imports and a key is present.

    Reports *presence*, never the value — `env.redact` exists because a key must not
    reach a log, and a dashboard served over HTTP is a log with a wider audience.
    """
    import os

    from ..env import load_env

    load_env()

    if model == "heuristic":
        return True, "offline, no key needed"
    if model.startswith("gemini"):
        try:
            importlib.import_module("google.genai")
        except Exception as exc:  # noqa: BLE001
            return False, f"google-genai not installed ({type(exc).__name__})"
        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            return False, "GEMINI_API_KEY not set — runs fall back to the heuristic"
        return True, "key present"
    if model.startswith("claude"):
        try:
            importlib.import_module("anthropic")
        except Exception:  # noqa: BLE001
            return False, "anthropic SDK not installed (optional extra)"
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return False, "ANTHROPIC_API_KEY not set"
        return True, "key present"
    return True, ""


def _tts_status(name: str) -> tuple[bool, str]:
    if name == "edge-tts":
        ok, why = _module_importable("edge_tts", "Communicate")
        return (ok, "no key needed, native word timings" if ok else why)
    if name == "cartesia":
        import os

        from ..env import load_env

        load_env()
        if not os.environ.get("CARTESIA_API_KEY"):
            return False, "CARTESIA_API_KEY not set"
        return True, "key present, native word timings"
    if name == "piper":
        return _module_importable(".tts.piper", "PiperTTS")
    return False, "unknown provider"


def provider_options() -> dict[str, list[dict[str, Any]]]:
    """The provider dropdowns, each option carrying its own availability.

    Recomputed per request. Vendoring icons or dropping a key into `.env` while the
    dashboard is open should change what the page offers without a restart.
    """
    llm = [
        ("gemini-2.5-flash", "Gemini 2.5 Flash", "Fast, structured outputs, seeded"),
        ("gemini-2.5-pro", "Gemini 2.5 Pro", "Slower, better on dense scripts"),
        ("claude-opus-5", "Claude Opus 5", "Requires the optional anthropic extra"),
        ("heuristic", "Heuristic (offline)", "Rule-based; no LLM, no key, no network"),
    ]
    tts = [
        ("edge-tts", "edge-tts", "Free, no key, native WordBoundary timings"),
        ("cartesia", "Cartesia Sonic", "Paid; word timestamps over SSE"),
        ("piper", "Piper (local)", "Planned — offline synthesis"),
    ]
    assets = [
        ("icon_pack", "Vendored icons", "Noto Emoji matched to narration keywords"),
        ("null", "None", "Templates are designed to look complete without assets"),
    ]
    aligner = [
        ("native", "TTS-native timings", "Exact by construction, zero extra cost"),
        ("whisperx", "WhisperX forced alignment", "Planned — pulls torch (~2.5 GB)"),
    ]

    def build(items, probe) -> list[dict[str, Any]]:
        out = []
        for value, label, blurb in items:
            ok, why = probe(value)
            out.append(
                {
                    "value": value,
                    "label": label,
                    "description": blurb,
                    "available": ok,
                    "reason": why,
                }
            )
        return out

    return {
        "llm": build(llm, _llm_status),
        "tts": build(tts, _tts_status),
        "assets": build(
            assets,
            lambda v: _icon_pack_status() if v == "icon_pack" else (True, "always available"),
        ),
        "aligner": build(
            aligner,
            lambda v: (True, "native to the TTS provider")
            if v == "native"
            else _module_importable(".align.whisperx", "WhisperXAligner"),
        ),
    }


def font_options() -> list[dict[str, Any]]:
    """Only families whose file is on disk.

    Unlike providers, an unavailable font is *omitted* rather than shown disabled:
    a provider seam is architecture worth displaying even when one end is unbuilt,
    whereas a greyed-out typeface is just noise.
    """
    families = available_families()
    return [{"value": f, "label": f, "available": True} for f in families] or [
        {
            "value": "Inter",
            "label": "Inter (not vendored)",
            "available": False,
            "reason": "run `python -m python_pipeline.vendor_fonts`",
        }
    ]


# ---------------------------------------------------------------------------
# The editable surface
# ---------------------------------------------------------------------------


def _num(lo: float, hi: float, cast: Callable[[Any], Any]):
    def coerce(value: Any) -> Any:
        try:
            out = cast(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"expected a number, got {value!r}") from exc
        if not lo <= out <= hi:
            raise ValueError(f"must be between {lo} and {hi}, got {out}")
        return out

    return coerce


def _colour(value: Any) -> str:
    text = str(value).strip()
    if not _HEX.match(text):
        raise ValueError(f"expected a #RRGGBB colour, got {text!r}")
    return text.upper()


def _choice(*allowed: str):
    def coerce(value: Any) -> str:
        text = str(value)
        if text not in allowed:
            raise ValueError(f"expected one of {', '.join(allowed)}, got {text!r}")
        return text

    return coerce


def _font(value: Any) -> str:
    """A font must be one that is actually loadable.

    This check is the whole point of the module docstring: without it the dropdown
    is back to setting a string the renderer ignores.
    """
    text = str(value)
    families = available_families()
    if families and text not in families:
        raise ValueError(
            f"font {text!r} is not vendored (available: {', '.join(families)})"
        )
    return text


# dotted config path -> coercion. Anything absent from this table is rejected,
# so the UI cannot reach `providers.renderer`, `profiles`, or arbitrary keys.
EDITABLE: dict[str, Callable[[Any], Any]] = {
    # Providers (R7)
    "providers.llm": str,
    "providers.tts": _choice("edge-tts", "cartesia", "piper"),
    "providers.assets": _choice("null", "icon_pack"),
    "providers.aligner": _choice("native", "whisperx"),
    # Typography + palette (R7: "colour palette and typography must be config")
    "theme.font_family": _font,
    "theme.type_scale_base_vmin": _num(1.5, 6.0, float),
    "theme.min_font_px": _num(8, 96, int),
    "theme.primary_color": _colour,
    "theme.secondary_color": _colour,
    "theme.background_color": _colour,
    "theme.text_color": _colour,
    "theme.muted_color": _colour,
    # Output format (R7: 16:9 and 9:16 both)
    "project.width": _num(256, 3840, int),
    "project.height": _num(256, 3840, int),
    "project.fps": _num(1, 120, int),
    "project.output_scale": _num(0.1, 1.0, float),
    "project.orientation": _choice("auto", "landscape", "portrait"),
}


def coerce_overrides(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate a flat {dotted.path: value} patch. Returns (nested patch, errors).

    Errors are collected rather than raised on the first one, so a form with two bad
    fields reports both instead of making the user resubmit twice.
    """
    nested: dict[str, Any] = {}
    errors: list[str] = []
    for path, value in raw.items():
        coerce = EDITABLE.get(path)
        if coerce is None:
            errors.append(f"{path}: not an editable setting")
            continue
        try:
            clean = coerce(value)
        except ValueError as exc:
            errors.append(f"{path}: {exc}")
            continue
        cursor = nested
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = clean
    return nested, errors


def flatten(config: dict[str, Any]) -> dict[str, Any]:
    """Current values for every editable path, for populating the form."""
    out: dict[str, Any] = {}
    for path in EDITABLE:
        cursor: Any = config
        for part in path.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                cursor = None
                break
            cursor = cursor[part]
        out[path] = cursor
    return out


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out
