"""Turn annotations into scenes.

This module owns the mapping from the flat `Annotation` contract to each
template's prop shape, plus the validation that stands between untrusted model
output and the renderer.

Keeping this in Python rather than in the prompt is what makes the annotator
swappable (R7): `llm_annotator` and `heuristic_annotator` both return
`Annotation` objects and neither knows what an ExpressionCard's props look like.
"""

from __future__ import annotations

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
        # Modelled as a one-column grid over items: the flat contract has no
        # notion of columns, and inventing rows the annotator never described
        # would put authored structure on screen.
        props["columns"] = ["Value"]
        props["rows"] = [
            {"label": v["label"], "cells": [v["resolved"]]} for v in _values(ann.items)
        ]
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
) -> SceneSpec:
    """Assemble the IR.

    `narration_text` comes from the *segment*, never from the annotation. This is
    the structural half of the no-rewrite invariant: even a model that returns
    reworded narration cannot change what is spoken, because its text is not
    wired to anything.
    """
    if len(annotations) != len(segments):
        raise AnnotationError(
            f"{len(annotations)} annotations for {len(segments)} segments — "
            "call reconcile() first."
        )

    scenes = [
        Scene(
            scene_id=f"scene_{seg.index + 1:02d}",
            template_name=ann.template_name,
            narration_text=seg.text,
            props=build_props(ann),
        )
        for seg, ann in zip(segments, annotations)
    ]
    return SceneSpec(project_title=project_title, scenes=scenes)


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
