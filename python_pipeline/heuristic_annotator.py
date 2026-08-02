"""Rule-based annotator — the no-LLM path.

Exists for three reasons, in order of how often each matters:

1.  **The demo must not depend on a network.** A dead key, a rate limit or
    airplane wifi during the live review would otherwise mean no video at all.
    R2 says an unseen script runs; it does not say "if the API is up".
2.  **A baseline.** Comparing this against the LLM output shows what the model
    is actually buying. If they look the same, the model is not earning its cost.
3.  **Determinism.** Pure Python, so `--annotator heuristic` reproduces exactly.

It is deliberately unambitious: find numbers in the narration, reconstruct an
expression when the surrounding words describe one, and pick a template by shape.
It will never notice that a segment is a conclusion rather than a fact. That gap
is the LLM's job.
"""

from __future__ import annotations

import re

from .schema import Annotation, StepSpec, ValueSpec
from .segmenter import Segment

# Numbers as written in narration: 256, 16,777,216, 3.14, 16.8
#
# The trailing guard is `(?![\d.]\d)` — NOT `(?![\w.])`. A plain `.` there makes
# a sentence-final "16,777,216." backtrack to "16,777", because the greedy group
# consumes ",777,216" and then the lookahead rejects the full stop. The guard has
# to reject only a *digit-bearing* continuation, so "2.5.1" still fails to match
# as "2.5" while "16,777,216." matches whole.
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\d.]\d|\w)")

# Number words that appear in arithmetic prose. Restricted to what a narrator
# actually says out loud; a full number-word parser is not worth the surface area.
_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "sixteen": 16, "twenty": 20, "thirty": 30,
    "hundred": 100, "thousand": 1000, "million": 1000000, "billion": 1000000000,
}

_ORDINALS = {
    "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

# Prose that signals a step-by-step passage.
_PROCESS_MARKERS = ("first,", "then,", "next,", "finally,", "after that", "step ")


def _clean_number(text: str) -> str:
    return text.replace(",", "")


def _numbers_in(text: str) -> list[tuple[str, str]]:
    """Return [(as_written, as_expression)] for every numeral in the text."""
    return [(m.group(1), _clean_number(m.group(1))) for m in _NUMBER.finditer(text)]


def _detect_power(text: str) -> tuple[str, str] | None:
    """Recognise "two to the eighth power" -> ("2**8", "two to the eighth power").

    Narration describes exponentiation in words far more often than with a caret,
    and this is precisely the case where an authored number ("256") would be
    tempting and wrong.
    """
    lowered = text.lower()
    pattern = re.compile(
        r"\b(?P<base>" + "|".join(_WORD_NUMBERS) + r"|\d+)\s+to\s+the\s+"
        r"(?P<exp>" + "|".join(_ORDINALS) + r"|\d+)(?:\s+power)?"
    )
    m = pattern.search(lowered)
    if not m:
        return None
    base_text, exp_text = m.group("base"), m.group("exp")
    base = _WORD_NUMBERS.get(base_text, None)
    if base is None:
        base = int(base_text) if base_text.isdigit() else None
    exp = _ORDINALS.get(exp_text, None)
    if exp is None:
        exp = int(exp_text) if exp_text.isdigit() else None
    if base is None or exp is None:
        return None
    return f"{base}**{exp}", m.group(0)


def _detect_product(text: str) -> tuple[str, str] | None:
    """Recognise "256 times 256 times 256" -> "256**3" style products."""
    lowered = text.lower()
    m = re.search(
        r"(\d[\d,]*)\s*(?:times|multiplied by|x)\s*(\d[\d,]*)"
        r"(?:\s*(?:times|multiplied by|x)\s*(\d[\d,]*))?",
        lowered,
    )
    if not m:
        return None
    factors = [_clean_number(g) for g in m.groups() if g]
    if len(factors) < 2:
        return None
    # Equal factors read better as a power, which is also what the narration means.
    if len(set(factors)) == 1:
        return f"{factors[0]}**{len(factors)}", m.group(0)
    return "*".join(factors), m.group(0)


def _detect_tuple(text: str) -> tuple[str, str] | None:
    """Recognise a written coordinate triple: "(255, 0, 0)"."""
    m = re.search(r"\((\s*\d+\s*(?:,\s*\d+\s*){1,3})\)", text)
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split(",")]
    return "(" + ", ".join(parts) + ")", m.group(0)


def _detect_range(text: str) -> tuple[str, str] | None:
    """Recognise "a range from 0 to 255"."""
    m = re.search(r"\bfrom\s+(\d[\d,]*)\s+to\s+(\d[\d,]*)", text.lower())
    if not m:
        return None
    lo, hi = _clean_number(m.group(1)), _clean_number(m.group(2))
    return f"({lo}, {hi})", m.group(0)


def _format_for(expr: str) -> str:
    if expr.startswith("("):
        return "tuple"
    try:
        # A magnitude test needs the value, and the evaluator is the only thing
        # allowed to produce it.
        from .evaluator import evaluate

        value = evaluate(expr)
    except Exception:
        return "raw"
    if isinstance(value, tuple):
        return "tuple"
    if isinstance(value, float) and not float(value).is_integer():
        return "float"
    return "thousands" if abs(value) >= 10000 else "int"


def _headline_for(text: str) -> tuple[ValueSpec, str] | None:
    """Best available expression for a segment, with the phrase it came from."""
    for detector in (_detect_power, _detect_product, _detect_tuple, _detect_range):
        found = detector(text)
        if found:
            expr, phrase = found
            fmt = "range" if detector is _detect_range else _format_for(expr)
            spec = ValueSpec(
                label=phrase.strip().rstrip(".,").capitalize(),
                expr=expr,
                format=fmt,
                cue_word=_last_number_word(phrase),
            )
            return spec, phrase
    return None


def _last_number_word(phrase: str) -> str | None:
    numbers = _numbers_in(phrase)
    return numbers[-1][0] if numbers else None


def _title_for(segment: Segment) -> str:
    """A short label from the segment's first clause.

    Not a summary — summarising is a language task and this module does not do
    language tasks. Truncating the opening clause is honest about that.
    """
    first = re.split(r"(?<=[.!?])\s", segment.text.strip())[0]
    words = re.split(r"[,;:]", first)[0].split()
    label = " ".join(words[:6]).rstrip(".,;:")
    return label if len(label) >= 3 else f"Part {segment.index + 1}"


class HeuristicAnnotator:
    """Deterministic, offline annotator."""

    name = "heuristic"

    def annotate(self, title: str | None, segments: list[Segment]) -> list[Annotation]:
        out: list[Annotation] = []
        for i, seg in enumerate(segments):
            out.append(self._annotate_one(seg, title, is_first=i == 0, is_last=i == len(segments) - 1))
        return out

    def _annotate_one(
        self, seg: Segment, doc_title: str | None, *, is_first: bool, is_last: bool
    ) -> Annotation:
        text = seg.text
        lowered = text.lower()

        # 1. A first segment with a document title is the title card.
        if is_first and doc_title:
            return Annotation(
                segment_index=seg.index,
                template_name="TitleCard",
                title=doc_title,
                subtitle=_first_sentence(text),
                reasoning="first segment with a document title",
            )

        # 2. An expression the narration describes arithmetically.
        found = _headline_for(text)
        if found:
            headline, phrase = found
            # A lone big number with little else around it reads better huge.
            template = "BigNumber" if seg.word_count <= 28 else "ExpressionCard"
            items = _supporting_items(
                text,
                exclude_phrase=phrase,
                exclude_value=_try_evaluate(headline.expr or ""),
            )
            return Annotation(
                segment_index=seg.index,
                template_name=template,
                title=_title_for(seg),
                caption=_first_sentence(text) if template == "BigNumber" else None,
                headline=headline,
                items=items[:3] if template == "ExpressionCard" else [],
                reasoning=f"detected expression {headline.expr!r}",
            )

        # 3. Sequential prose.
        if any(marker in lowered for marker in _PROCESS_MARKERS):
            steps = _steps_from(text)
            if len(steps) >= 2:
                return Annotation(
                    segment_index=seg.index,
                    template_name="ProcessSteps",
                    title=_title_for(seg),
                    steps=steps,
                    reasoning="sequential markers present",
                )

        # 4. Two or more bare numbers -> a panel of values.
        items = _supporting_items(text)
        if len(items) >= 2:
            return Annotation(
                segment_index=seg.index,
                template_name="KeyValuePanel",
                title=_title_for(seg),
                items=items[:4],
                reasoning=f"{len(items)} numeric values found",
            )

        # 5. Nothing numeric: narration on the theme background.
        return Annotation(
            segment_index=seg.index,
            template_name="Fallback",
            title="" if is_last else _title_for(seg),
            reasoning="no values detected",
        )


def _first_sentence(text: str) -> str | None:
    parts = re.split(r"(?<=[.!?])\s", text.strip())
    return parts[0] if parts and len(parts[0]) <= 140 else None


def _try_evaluate(expr: str):
    from .evaluator import evaluate

    try:
        return evaluate(expr)
    except Exception:
        return None


def _supporting_items(
    text: str, *, exclude_phrase: str = "", exclude_value: object = None
) -> list[ValueSpec]:
    """Bare numerals in the text as standalone values.

    Each becomes `expr` (the numeral itself), never authored text — so even a
    quoted figure passes through the evaluator and the one formatter (R4).

    Two exclusions, and both are needed:

    *   `exclude_phrase` — the span the headline was derived from. Excluding by
        *span* rather than by the headline's expression string matters: the
        headline for "256 times 256 times 256" is `256**3`, whose text shares no
        digits with the narration, so a string comparison would re-emit 256.
    *   `exclude_value` — the headline's computed result. Narration usually states
        the answer out loud ("256 times 256 times 256 equals 16,777,216"), and
        that numeral sits outside the headline's span, so without this the same
        number appears twice on screen.
    """
    seen: set[str] = {expr for _, expr in _numbers_in(exclude_phrase)}
    items: list[ValueSpec] = []
    for as_written, as_expr in _numbers_in(text):
        if as_expr in seen:
            continue
        seen.add(as_expr)
        if exclude_value is not None and _try_evaluate(as_expr) == exclude_value:
            continue
        items.append(
            ValueSpec(
                label=_label_near(text, as_written),
                expr=as_expr,
                # Preserve what the narration said: "16.8" formatted as an int
                # renders 17, which is a mangled digit even though the
                # arithmetic is right.
                format=_format_for(as_expr),
                cue_word=as_written,
            )
        )
    return items


def _label_near(text: str, number: str) -> str:
    """Up to three words following a number, as its label.

    "255 means the light is at maximum brightness" -> "means the light". Crude,
    and visibly so; the LLM path is what produces real labels.
    """
    m = re.search(re.escape(number) + r"\s+((?:[\w'-]+\s*){1,3})", text)
    if not m:
        return "Value"
    label = " ".join(m.group(1).split()).rstrip(".,;:")
    return label.capitalize() if label else "Value"


def _steps_from(text: str) -> list[StepSpec]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s", text) if s.strip()]
    steps: list[StepSpec] = []
    for sentence in sentences:
        words = sentence.split()
        label = " ".join(words[:5]).rstrip(".,;:")
        detail = " ".join(words[5:]).rstrip() or None
        steps.append(StepSpec(label=label, detail=detail))
    return steps[:5]
