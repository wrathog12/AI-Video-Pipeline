"""Turn annotations into scenes.

This module owns the mapping from the flat `Annotation` contract to each
template's prop shape, plus the validation that stands between untrusted model
output and the renderer.

Keeping this in Python rather than in the prompt is what makes the annotator
swappable (R7): `llm_annotator` and `heuristic_annotator` both return
`Annotation` objects and neither knows what an ExpressionCard's props look like.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Protocol

from .schema import (
    Annotation,
    Scene,
    SceneSpec,
    StepSpec,
    TemplateName,
    Value,
    ValueSpec,
)
from .segmenter import Segment

VALID_TEMPLATES: tuple[str, ...] = (
    "TitleCard",
    "KeyValuePanel",
    "ExpressionCard",
    "BigNumber",
    "ComparisonGrid",
    "ProcessSteps",
    "Fallback",
)

# Templates with a React component registered in SceneDispatcher. Anything else
# renders as Fallback anyway, so the annotator is told to stay inside this set
# and an out-of-set choice is downgraded here rather than silently in the browser.
IMPLEMENTED_TEMPLATES: tuple[str, ...] = (
    "TitleCard",
    "KeyValuePanel",
    "ExpressionCard",
    "BigNumber",
    "ComparisonGrid",
    "ProcessSteps",
    "Fallback",
)


class Annotator(Protocol):
    """R7 seam. Both implementations satisfy this and are interchangeable."""

    name: str

    def annotate(self, title: str | None, segments: list[Segment]) -> list[Annotation]:
        ...


# ---------------------------------------------------------------------------
# Annotation -> props
# ---------------------------------------------------------------------------


def _values(specs: list[ValueSpec]) -> list[dict[str, Any]]:
    return [s.to_value().model_dump() for s in specs]


def _value(spec: ValueSpec | None) -> dict[str, Any] | None:
    return spec.to_value().model_dump() if spec else None


def build_props(ann: Annotation) -> dict[str, Any]:
    """Produce the props object for `ann.template_name`.

    Unset optional keys are omitted rather than set to None, so the props hash
    (and therefore the render cache key) does not change when an unrelated
    template gains a field.
    """
    name = ann.template_name
    props: dict[str, Any] = {}
    if ann.title:
        props["title"] = ann.title

    if name == "TitleCard":
        if ann.subtitle:
            props["subtitle"] = ann.subtitle
        return props

    if name == "ProcessSteps":
        props["steps"] = [s.model_dump() for s in ann.steps]
        return props

    if name in ("BigNumber", "ExpressionCard"):
        headline = _value(ann.headline)
        if headline:
            props["expression"] = headline
        if name == "BigNumber":
            if ann.caption:
                props["caption"] = ann.caption
        elif ann.items:
            props["items"] = _values(ann.items)
        return props

    if name == "KeyValuePanel":
        props["items"] = _values(ann.items)
        return props

    if name == "ComparisonGrid":
        # Flat items, same shape as KeyValuePanel. The template scales them into
        # proportional bars rather than laying out a table: the flat contract has
        # no notion of columns, and inventing rows the annotator never described
        # would put authored structure on screen. A comparison is about relative
        # magnitude, which a flat list of computed numbers already carries.
        props["items"] = _values(ann.items)
        if ann.caption:
            props["caption"] = ann.caption
        return props

    # Fallback and anything unrecognised: carry whatever values exist so the
    # generic template can still surface them.
    if ann.headline:
        props["expression"] = _value(ann.headline)
    if ann.items:
        props["items"] = _values(ann.items)
    return props


# ---------------------------------------------------------------------------
# Validation and repair
# ---------------------------------------------------------------------------


class AnnotationError(ValueError):
    """Raised when annotations cannot be matched to the segments."""


# ---------------------------------------------------------------------------
# Cue repair
#
# `word_triggers` are single tokens, because that is what a TTS provider emits.
# A cue_word that spans several tokens can therefore never match, and a miss is
# not fail-safe: `useCueProgress` treats "no trigger" as "show immediately", so
# the element silently ignores the audio instead of crashing.
#
# The failing case is real and predictable: asked to name the word where
# "(255, 0, 0)" appears, a model copies the whole tuple. It is a faithful reading
# of the instruction and it is unmatchable. Repairing it here rather than
# loosening the matcher in TSX keeps the decision next to the narration, which is
# the only place that can tell whether a candidate token was actually spoken.
# ---------------------------------------------------------------------------

# Internal hyphens, commas and dots are *part of* a token, not separators. This
# is not cosmetic: "8-bit" and "16,777,216" are each pronounced as one event and
# arrive as one trigger, so splitting them would rewrite a cue that already
# matched into one that matches worse. Only the punctuation *around* a token —
# the parentheses of "(255, 0, 0)", where a comma is followed by a space — splits.
_TOKEN = re.compile(r"[^\W_]+(?:[-,.][^\W_]+)*", re.UNICODE)


def _narration_tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def repair_cue(cue: str, narration: str) -> tuple[str | None, str | None]:
    """Reduce a cue_word to a single token that the narration actually contains.

    Returns (cue, reason_if_changed). A cue that is already a single token found
    in the narration passes through untouched.

    A multi-token cue is reduced to its **least ambiguous** token, not its first.
    "(255, 0, 0)" contains "255" once and "0" five times; anchoring to "0" would
    fire on whichever zero came first in the sentence, which is generally not the
    value's own. Picking the rarest token, earliest-wins on a tie, lands on "255" —
    the word a viewer actually hears for that value. When every candidate is
    equally common ("(0, 0, 0)") there is no better anchor available and the first
    occurrence is used; that is a documented limitation rather than a silent one,
    since the repair is logged either way.

    A cue with no usable token becomes None, which is honest: better an element
    that appears with its scene than one claiming an anchor it does not have.
    """
    parts = _narration_tokens(cue)
    spoken = [t.lower() for t in _narration_tokens(narration)]
    counts = Counter(spoken)
    if len(parts) == 1 and parts[0].lower() in counts:
        return cue, None

    present = [p for p in parts if p.lower() in counts]
    if not present:
        return None, f"cue {cue!r} does not occur in the narration; dropped"

    best = min(present, key=lambda p: counts[p.lower()])
    if best == cue:
        return cue, None
    return best, f"cue {cue!r} spans multiple tokens; anchored to {best!r}"


def repair_cues(props: Any, narration: str, path: str = "") -> list[str]:
    """Rewrite every cue_word in a props tree in place. Returns warnings."""
    warnings: list[str] = []
    if isinstance(props, dict):
        cue = props.get("cue_word")
        if isinstance(cue, str) and cue.strip():
            fixed, reason = repair_cue(cue.strip(), narration)
            if reason:
                props["cue_word"] = fixed
                warnings.append(f"{path}: {reason}")
        for key, value in props.items():
            warnings.extend(
                repair_cues(value, narration, f"{path}.{key}" if path else key)
            )
    elif isinstance(props, list):
        for i, item in enumerate(props):
            warnings.extend(repair_cues(item, narration, f"{path}[{i}]"))
    return warnings


def _has_content(ann: Annotation) -> bool:
    """Would this annotation render anything beyond a bare title?"""
    if ann.template_name == "TitleCard":
        return bool(ann.title or ann.subtitle)
    if ann.template_name == "ProcessSteps":
        return bool(ann.steps)
    if ann.template_name in ("BigNumber", "ExpressionCard"):
        return ann.headline is not None
    if ann.template_name in ("KeyValuePanel", "ComparisonGrid"):
        return bool(ann.items)
    return True  # Fallback always renders narration


def reconcile(
    annotations: list[Annotation], segments: list[Segment]
) -> tuple[list[Annotation], list[str]]:
    """Align annotations to segments one-to-one, repairing what can be repaired.

    A model returning the wrong number of annotations, duplicate indices, or an
    unimplemented template must not fail the run — R2 means an unseen script has
    to produce a video. Every repair is returned as a warning string so the run
    log shows exactly what the model got wrong, rather than hiding it.
    """
    warnings: list[str] = []
    by_index: dict[int, Annotation] = {}
    for ann in annotations:
        if ann.segment_index in by_index:
            warnings.append(f"duplicate segment_index {ann.segment_index}; keeping the first")
            continue
        if not 0 <= ann.segment_index < len(segments):
            warnings.append(f"segment_index {ann.segment_index} out of range; dropped")
            continue
        by_index[ann.segment_index] = ann

    out: list[Annotation] = []
    for seg in segments:
        ann = by_index.get(seg.index)
        if ann is None:
            warnings.append(f"segment {seg.index} unannotated; using Fallback")
            out.append(Annotation(segment_index=seg.index, template_name="Fallback"))
            continue

        if ann.template_name not in IMPLEMENTED_TEMPLATES:
            warnings.append(
                f"segment {seg.index}: template {ann.template_name!r} not implemented; "
                "using Fallback"
            )
            ann = ann.model_copy(update={"template_name": "Fallback"})

        if not _has_content(ann):
            warnings.append(
                f"segment {seg.index}: {ann.template_name} has no content to show; "
                "using Fallback"
            )
            ann = ann.model_copy(update={"template_name": "Fallback"})

        out.append(ann)

    return out, warnings


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_spec(
    project_title: str,
    segments: list[Segment],
    annotations: list[Annotation],
) -> tuple[SceneSpec, list[str]]:
    """Assemble the IR. Returns (spec, warnings).

    `narration_text` comes from the *segment*, never from the annotation. This is
    the structural half of the no-rewrite invariant: even a model that returns
    reworded narration cannot change what is spoken, because its text is not
    wired to anything.

    Cue words are repaired here, because this is the first point at which a props
    tree and the narration it belongs to are both in hand.
    """
    if len(annotations) != len(segments):
        raise AnnotationError(
            f"{len(annotations)} annotations for {len(segments)} segments — "
            "call reconcile() first."
        )

    scenes: list[Scene] = []
    warnings: list[str] = []
    for seg, ann in zip(segments, annotations):
        scene_id = f"scene_{seg.index + 1:02d}"
        props = build_props(ann)
        warnings.extend(repair_cues(props, seg.text, scene_id))
        scenes.append(
            Scene(
                scene_id=scene_id,
                template_name=ann.template_name,
                narration_text=seg.text,
                props=props,
            )
        )
    return SceneSpec(project_title=project_title, scenes=scenes), warnings


def cue_words(props: Any) -> list[str]:
    """Every cue_word present in a props tree, in document order."""
    found: list[str] = []
    if isinstance(props, dict):
        cue = props.get("cue_word")
        if isinstance(cue, str) and cue.strip():
            found.append(cue.strip())
        for value in props.values():
            found.extend(cue_words(value))
    elif isinstance(props, list):
        for item in props:
            found.extend(cue_words(item))
    return found
