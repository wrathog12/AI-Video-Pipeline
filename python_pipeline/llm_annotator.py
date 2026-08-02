"""LLM annotator — Gemini via google-genai.

The LLM's entire job is here, and it is narrow by design. It receives segments
whose text is already fixed and returns, for each one, a template name and a set
of labels plus arithmetic *expressions*. It does not:

*   choose scene boundaries (segmenter.py does, deterministically);
*   compute any number (evaluator.py does, symbolically);
*   emit code, JSX, CSS or timings;
*   supply narration — `build_spec` takes narration from the segment, so even a
    model that returns reworded text cannot change what is spoken.

Determinism (R3). Two mechanisms, in order of strength:

1.  The annotation cache. Same script + same prompt + same model → the JSON is
    read from disk and no request is made. This is the guarantee, and it is what
    makes the "run it twice" check pass.
2.  `temperature=0` + a fixed `seed`. Gemini accepts both, unlike some providers.
    This narrows drift on a *cold* cache but is not a guarantee: providers do not
    promise bitwise reproducibility across serving stacks.

Point 1 is the claim made in the log; point 2 is a mitigation, and the docstring
says so rather than overstating it.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from .schema import Annotation, AnnotationSet, SCHEMA_VERSION
from .segmenter import Segment

DEFAULT_MODEL = "gemini-2.5-flash"

# Env var names checked in order. GEMINI_API_KEY is what AI Studio hands out;
# GOOGLE_API_KEY is what the SDK reads by default, so both are honoured.
API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")

# Bumping this invalidates every cached annotation, which is correct: a changed
# prompt is a changed function.
# Bump on any SYSTEM_PROMPT or RESPONSE_SCHEMA change: the spec cache key is
# derived from this, so a stale key would serve annotations produced by the old
# prompt and the change would appear to have no effect.
PROMPT_VERSION = 5

SYSTEM_PROMPT = """\
You are the annotation stage of a deterministic video compiler. You label \
pre-segmented narration. You do not write narration, code, or timings.

For each segment you receive, choose a template and describe what should appear \
on screen.

## Templates

- `TitleCard` — the opening. `title` plus a short `subtitle`.
- `BigNumber` — one number is the point of the segment. `headline` plus a short \
`caption`.
- `ExpressionCard` — an arithmetic relationship. `headline` is the relationship; \
`items` are up to three supporting values.
- `KeyValuePanel` — two to four related values, no single headline. `items`.
- `ComparisonGrid` — two to five values whose *relative sizes* are the point \
("this one is far bigger than that one"). `items`, plus an optional `caption`. \
Prefer this over `KeyValuePanel` when the narration contrasts magnitudes; the \
values are drawn as proportional bars, so comparable numbers work best.
- `ProcessSteps` — a sequence of stages. `steps`, two to five of them.
- `Fallback` — prose with nothing quantitative in it. `title` only, or nothing.

## Tuples draw as colour

A `tuple` value whose three components are 0-255 is painted on screen as an \
actual colour swatch (four components in 0-100 are treated as CMYK). So when the \
narration describes a specific colour by its components, emit it as a tuple \
expression — `expr: "(255, 0, 0)"`, `format: "tuple"` — and the viewer sees the \
colour, not just the digits. Group such values into one `KeyValuePanel`.

## The rule about numbers

Never write a computed number. Write the expression that produces it.

The narration says "two to the eighth power, resulting in 256 possible \
combinations". You return `expr: "2**8"`. You do NOT return `text: "256"`. \
Python evaluates the expression; if you supply a number as text the run fails.

Expressions are Python arithmetic: `+ - * / // % **`, parentheses, and \
`abs round min max sum sqrt log log2 log10 exp floor ceil gcd lcm hypot \
factorial degrees radians sin cos tan`, plus the constants `pi e tau`. A tuple \
like `(255, 0, 0)` is a valid expression. Nothing else — no strings, no \
comparisons.

**Every expression must be fully numeric.** No variable names, ever. Algebra is \
not evaluable, so a formula written with symbols is discarded and that value \
does not appear on screen at all.

- WRONG: `expr: "P * (1.07)**N"` — `P` and `N` are names, not numbers.
- WRONG: `expr: "72 / R"` — same problem.
- RIGHT: `expr: "1000 * (1.07)**30"` — substitute the actual figures the \
narration gives.

If the narration states a general formula without concrete figures, put the \
formula in the *label* as prose ("Balance grows by 7% each year") and give the \
value a concrete expression using the numbers the narration does supply. If \
there are no concrete numbers, choose `Fallback` for that segment.

Use `text` only for genuinely non-numeric labels: "RGB", "8-bit", "sRGB". A \
`text` value that is entirely digits is rejected.

## Formats

`int` (256) · `thousands` (16,777,216) · `float` (3.14) · `tuple` ((255, 0, 0)) \
· `range` (0-255, needs a two-element expression) · `percent` (the expression \
must already yield the percentage, so write `12/48*100`, not `12/48`) · `raw`.

## cue_word

For each value, set `cue_word` to the single word in that segment's narration \
where the value should appear on screen. It must be a word that literally \
occurs in the narration — copy it exactly, including digits and punctuation as \
written ("16,777,216", not "16777216"). Omit it if no word fits.

## Labels

Labels are short — two to six words — and describe the value's meaning, not its \
digits. Prefer wording drawn from the narration. Titles are three to six words.

Aim for variety across the video: a run of seven ExpressionCards is worse than a \
mix, even if each one is individually defensible.
"""


class LLMError(RuntimeError):
    """Annotation failed. The caller falls back to the heuristic annotator."""


def api_key() -> str | None:
    for var in API_KEY_VARS:
        value = os.environ.get(var)
        if value and value.strip():
            return value.strip()
    return None


def prompt_fingerprint(model: str) -> str:
    """What the annotation cache key hashes, besides the script."""
    return f"gemini/v{PROMPT_VERSION}/{model}/schema{SCHEMA_VERSION}"


# ---------------------------------------------------------------------------
# Response schema
#
# Hand-written rather than derived from the Pydantic models. Gemini's
# responseSchema is a restricted OpenAPI subset: no `anyOf` with null (so
# nullable fields need `nullable: true`), no `$defs`/`$ref` expansion in some
# paths, and `propertyOrdering` is the only way to control field order — which
# matters, because a model that emits `template_name` before deciding on content
# commits to a template it then cannot fill.
# ---------------------------------------------------------------------------

_VALUE_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "expr": {"type": "string", "nullable": True,
                 "description": "Fully numeric Python arithmetic, e.g. '2**8' or "
                                "'1000 * (1.07)**30'. No variable names — 'P * r**N' "
                                "is rejected and the value is discarded."},
        "text": {"type": "string", "nullable": True,
                 "description": "Literal non-numeric text. Rejected if all digits."},
        "format": {
            "type": "string",
            "enum": ["int", "thousands", "float", "tuple", "range", "percent", "raw"],
        },
        "unit": {"type": "string", "nullable": True},
        "cue_word": {"type": "string", "nullable": True,
                     "description": "A word copied verbatim from this segment's narration."},
    },
    "required": ["label", "format"],
    "propertyOrdering": ["label", "expr", "text", "format", "unit", "cue_word"],
}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "annotations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segment_index": {"type": "integer"},
                    "reasoning": {
                        "type": "string",
                        "description": "One sentence: why this template for this segment.",
                    },
                    "template_name": {
                        "type": "string",
                        "enum": ["TitleCard", "KeyValuePanel", "ExpressionCard",
                                 "BigNumber", "ComparisonGrid", "ProcessSteps",
                                 "Fallback"],
                    },
                    "title": {"type": "string"},
                    "subtitle": {"type": "string", "nullable": True},
                    "caption": {"type": "string", "nullable": True},
                    "headline": {**_VALUE_SPEC_SCHEMA, "nullable": True},
                    "items": {"type": "array", "items": _VALUE_SPEC_SCHEMA},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "detail": {"type": "string", "nullable": True},
                            },
                            "required": ["label"],
                            "propertyOrdering": ["label", "detail"],
                        },
                    },
                },
                "required": ["segment_index", "reasoning", "template_name", "title"],
                # reasoning before template_name: deciding out loud first measurably
                # improves template choice, and it costs one short string.
                "propertyOrdering": ["segment_index", "reasoning", "template_name",
                                     "title", "subtitle", "caption", "headline",
                                     "items", "steps"],
            },
        }
    },
    "required": ["annotations"],
}


def build_user_prompt(title: str | None, segments: list[Segment]) -> str:
    lines: list[str] = []
    if title:
        lines.append(f"Video title: {title}")
    lines.append(f"{len(segments)} segments. Return exactly one annotation per segment.\n")
    for seg in segments:
        lines.append(f"--- segment_index: {seg.index} ---")
        lines.append(seg.text)
        lines.append("")
    return "\n".join(lines)


class GeminiAnnotator:
    """Gemini-backed annotator (R7: swap by changing providers.llm)."""

    name = "gemini"

    def __init__(self, model: str = DEFAULT_MODEL, *, seed: int = 7,
                 temperature: float = 0.0, max_output_tokens: int = 32768) -> None:
        self.model = model
        self.seed = seed
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    # -- the R7 seam ------------------------------------------------------
    def annotate(self, title: str | None, segments: list[Segment]) -> list[Annotation]:
        raw = self.request(build_user_prompt(title, segments))
        return parse_response(raw)

    def request(self, user_prompt: str) -> str:
        """One call to Gemini, returning the raw JSON text."""
        key = api_key()
        if not key:
            raise LLMError(
                "No Gemini API key. Set GEMINI_API_KEY in .env (copy .env.example) "
                "or in the environment. Run with --annotator heuristic to skip the LLM."
            )
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise LLMError(
                "google-genai is not installed. `pip install -r requirements.txt`"
            ) from exc

        client = genai.Client(api_key=key)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=self.temperature,
            seed=self.seed,
            candidate_count=1,
            max_output_tokens=self.max_output_tokens,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        )
        try:
            response = client.models.generate_content(
                model=self.model, contents=user_prompt, config=config
            )
        except Exception as exc:  # noqa: BLE001 - SDK raises many shapes
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc

        text = getattr(response, "text", None)
        if not text or not text.strip():
            # A truncated or blocked response has no .text. The finish reason is
            # the only useful diagnostic, so surface it instead of "empty".
            reason = _finish_reason(response)
            raise LLMError(f"Gemini returned no text (finish_reason={reason})")
        return text


def _finish_reason(response: Any) -> str:
    try:
        candidate = response.candidates[0]
        return str(getattr(candidate, "finish_reason", "unknown"))
    except Exception:  # noqa: BLE001
        return "unknown"


def parse_response(raw: str) -> list[Annotation]:
    """Validate raw model JSON into Annotations.

    Tolerant of a fenced code block, which models emit occasionally even under a
    JSON mime type. Not tolerant of anything else: a malformed field raises, and
    the caller falls back to the heuristic annotator rather than rendering
    half-populated scenes.
    """
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Model returned non-JSON: {exc}. First 200 chars: {text[:200]!r}") from None

    if isinstance(payload, list):
        payload = {"annotations": payload}   # a plausible near-miss, cheap to accept

    try:
        parsed = AnnotationSet.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - pydantic ValidationError
        raise LLMError(f"Model JSON did not match the annotation schema: {exc}") from None

    if not parsed.annotations:
        raise LLMError("Model returned zero annotations")
    return parsed.annotations


# ---------------------------------------------------------------------------
# Anthropic path.
#
# Present so the R7 "swappable LLM" claim is demonstrable rather than asserted:
# both classes implement the same two-method surface, and the prompt and the
# response contract are shared. Two vendor differences are worth noting because
# they are the kind of thing that looks like a bug later:
#
#   * `temperature` returns HTTP 400 on Opus 5 / 4.8 / 4.7, where Gemini accepts
#     it. So the cold-cache determinism lever exists on one vendor and not the
#     other, and the cache is what actually carries R3.
#   * Anthropic structured outputs require `additionalProperties: false` and
#     strip numeric/length constraints, so the schema is derived from the
#     Pydantic model rather than reusing Gemini's OpenAPI-subset dict.
# ---------------------------------------------------------------------------


class ClaudeAnnotator:
    """Anthropic-backed annotator. Same contract as GeminiAnnotator."""

    name = "claude"

    def __init__(self, model: str = "claude-opus-5", *, max_tokens: int = 16000) -> None:
        self.model = model
        self.max_tokens = max_tokens

    def annotate(self, title: str | None, segments: list[Segment]) -> list[Annotation]:
        raw = self.request(build_user_prompt(title, segments))
        return parse_response(raw)

    def request(self, user_prompt: str) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise LLMError(
                "No Anthropic API key. Set ANTHROPIC_API_KEY in .env, or set "
                "providers.llm to a Gemini model id."
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMError("anthropic is not installed. `pip install -r requirements.txt`") from exc

        client = anthropic.Anthropic(api_key=key)
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": AnnotationSet.model_json_schema(),
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc

        parts = [b.text for b in response.content if getattr(b, "type", "") == "text"]
        if not parts:
            raise LLMError(f"Claude returned no text (stop_reason={response.stop_reason})")
        return "".join(parts)


def prompt_fingerprint_for(annotator: Any) -> str:
    """Cache fingerprint for whichever annotator is active."""
    model = getattr(annotator, "model", getattr(annotator, "name", "unknown"))
    return f"{annotator.name}/v{PROMPT_VERSION}/{model}/schema{SCHEMA_VERSION}"


def get_annotator(name: str):
    """Resolve `providers.llm` to an annotator (R7).

    Dispatch is by vendor prefix, so a new model id from a known vendor needs no
    code change. Unknown names fail loudly rather than silently degrading: a typo
    in config should not quietly produce a worse video.
    """
    from .heuristic_annotator import HeuristicAnnotator

    raw = (name or "").strip()
    lowered = raw.lower()
    if lowered in ("heuristic", "none", "offline", ""):
        return HeuristicAnnotator()
    if lowered.startswith("gemini"):
        return GeminiAnnotator(model=raw)
    if lowered.startswith("claude"):
        return ClaudeAnnotator(model=raw)
    raise ValueError(
        f"Unknown LLM provider {name!r}. Use a Gemini model id "
        "(gemini-2.5-flash, gemini-2.5-pro), a Claude model id (claude-opus-5), "
        "or 'heuristic'."
    )
