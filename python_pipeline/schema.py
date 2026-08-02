"""Pydantic models for the inspectable IR (R6) and for config.

The IR is the contract between every stage. Two rules it enforces structurally:

1.  Timings are milliseconds, never frames. fps is configuration (R7), so an IR
    carrying frame numbers would have config baked into the artifact.
2.  Every displayed value carries both `expr` (what the LLM proposed) and
    `resolved` (what Python computed). Templates render `resolved` only.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 2

TemplateName = Literal[
    "TitleCard",
    "KeyValuePanel",
    "ExpressionCard",
    "BigNumber",
    "ComparisonGrid",
    "ProcessSteps",
    "Fallback",
]

ValueFormat = Literal["int", "thousands", "float", "tuple", "range", "percent", "raw"]


class Strict(BaseModel):
    """Base for IR models.

    `extra="forbid"` mirrors the `additionalProperties: false` that Anthropic
    structured outputs require, so the same models can be reused as the LLM
    output schema in Phase 2 without divergence.
    """

    model_config = ConfigDict(extra="forbid")


class Value(Strict):
    """A displayed value. The LLM fills `expr`; `evaluator.py` fills `resolved`."""

    label: str = ""
    expr: str | None = None
    format: ValueFormat = "raw"
    unit: str | None = None
    # None until the evaluator has run. Renderers must refuse to display a None.
    resolved: str | None = None
    # The narrated word this value should appear on (R5). Named explicitly by the
    # annotator rather than guessed by the template: fuzzy-matching `resolved`
    # against the transcript mis-fires whenever the spoken form differs from the
    # displayed form ("sixteen point eight million" vs "16,777,216").
    cue_word: str | None = None
    # Numeric components of a tuple-valued result, filled by the evaluator — NOT
    # by the annotator, and never parsed out of `resolved` by a template.
    #
    # The evaluator already computes `(255, 0, 0)` as a real Python tuple and then
    # formats it to a string. Discarding the structure would force any component
    # that wants to *draw* the value (a colour swatch, a bar) to parse the display
    # string back into numbers — arithmetic in the renderer, which is precisely
    # the R4 boundary violation the expr/resolved split exists to prevent. So the
    # structure is preserved here instead. Empty for non-tuple values.
    channels: list[float] = Field(default_factory=list)


class WordTrigger(Strict):
    """A word's timing, in ms relative to the start of its own scene's audio."""

    word: str
    start_ms: int
    end_ms: int


class AssetRef(Strict):
    kind: Literal["svg", "image", "none"] = "none"
    id: str = ""
    # Renderer-relative path, resolved with Remotion's staticFile(). Never
    # absolute: an absolute path bakes this machine's directory layout into the
    # IR, and the spec is meant to be re-renderable elsewhere.
    path: str | None = None
    # The narration word that selected this asset, spelled as the narration spells
    # it — so it can match a word trigger and be revealed as it is spoken (R5).
    # Normalising it here would produce a cue no trigger contains, and a missed
    # cue shows immediately rather than failing, so it would silently un-sync.
    cue_word: str | None = None


class DerivedFrom(Strict):
    """Provenance for staleness detection.

    If a human edits `narration_text` in the spec, `narration_sha256` no longer
    matches and only that scene is re-synthesised and re-aligned (R8).
    """

    audio_pcm_sha256: str | None = None
    narration_sha256: str | None = None


class Scene(Strict):
    scene_id: str
    template_name: TemplateName
    narration_text: str
    start_ms: int = 0
    duration_ms: int = 0
    props: dict[str, Any] = Field(default_factory=dict)
    assets: list[AssetRef] = Field(default_factory=list)
    word_triggers: list[WordTrigger] = Field(default_factory=list)
    derived_from: DerivedFrom = Field(default_factory=DerivedFrom)


class Transition(Strict):
    """Transitions sit *between* scenes.

    A `transition_type` field on a scene has no defined meaning for the final
    scene and no second clip to blend with.
    """

    after_scene: str
    type: Literal["none", "fade"] = "fade"
    duration_ms: int = 400


class Provenance(Strict):
    script_sha256: str = ""
    llm_model: str = ""
    # Which annotator actually produced the scenes. Distinct from llm_model
    # because a failed API call falls back to the heuristic path, and the spec
    # must say so.
    annotator: str = ""
    prompt_sha256: str = ""
    tts_provider: str = ""
    tts_voice: str = ""
    tts_model: str = ""
    aligner: str = ""
    fps: int = 30
    width: int = 1920
    height: int = 1080
    output_scale: float = 1.0
    theme_sha256: str = ""
    # Digest of the renderer's own sources. Two runs with identical props but
    # different engine_sha256 are expected to differ visually — which is exactly
    # what someone comparing an old spec against a new render needs to know.
    engine_sha256: str = ""
    chromium_version: str = ""
    # Deliberately NOT a wall-clock default: a timestamp baked in at model
    # construction would change on every run and defeat spec comparison.
    generated_at_utc: str | None = None


class SceneSpec(Strict):
    schema_version: int = SCHEMA_VERSION
    project_title: str
    provenance: Provenance = Field(default_factory=Provenance)
    transitions: list[Transition] = Field(default_factory=list)
    scenes: list[Scene]

    def scene_by_id(self, scene_id: str) -> Scene | None:
        return next((s for s in self.scenes if s.scene_id == scene_id), None)


# --------------------------------------------------------------------------
# The annotation contract — what an annotator (LLM or heuristic) returns.
#
# Deliberately NOT the Scene model. An annotator returns a flat, uniform
# description of one segment; `annotate.build_props` turns that into the
# template-specific prop shape. Two reasons:
#
#   * A model asked to emit seven different nested prop shapes gets them subtly
#     wrong. One shape it always fills correctly.
#   * Prop layout is a renderer concern. Keeping it in Python means adding a
#     template does not change the LLM's output schema, so cached annotations
#     stay valid.
# --------------------------------------------------------------------------


class ValueSpec(Strict):
    """An annotator's proposal for one on-screen value.

    `expr` is an arithmetic expression for evaluator.py to compute. `text` is
    literal non-numeric text. Exactly one should be set; supplying `text` that
    looks like a number is rejected downstream (R4).
    """

    label: str = ""
    expr: str | None = None
    text: str | None = None
    format: ValueFormat = "raw"
    unit: str | None = None
    cue_word: str | None = None

    def to_value(self) -> Value:
        return Value(
            label=self.label,
            expr=self.expr,
            format=self.format,
            unit=self.unit,
            resolved=None if self.expr else self.text,
            cue_word=self.cue_word,
        )


class StepSpec(Strict):
    label: str
    detail: str | None = None


class Annotation(Strict):
    """One annotated segment. Order matches the segments handed to the annotator."""

    segment_index: int
    template_name: TemplateName
    title: str = ""
    subtitle: str | None = None
    caption: str | None = None
    headline: ValueSpec | None = None
    items: list[ValueSpec] = Field(default_factory=list)
    steps: list[StepSpec] = Field(default_factory=list)
    # Free-text rationale. Not rendered; it makes a bad annotation diagnosable in
    # the cached JSON without re-running the model.
    reasoning: str = ""


class AnnotationSet(Strict):
    annotations: list[Annotation]


# --------------------------------------------------------------------------
# Config models. Separate from the IR: config is an input, the IR is an artifact.
# --------------------------------------------------------------------------


class ProjectConfig(BaseModel):
    fps: int = 30
    width: int = 1920
    height: int = 1080
    output_scale: float = 1.0
    orientation: Literal["auto", "landscape", "portrait"] = "auto"

    @property
    def resolved_orientation(self) -> str:
        if self.orientation != "auto":
            return self.orientation
        return "portrait" if self.height > self.width else "landscape"


class ThemeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")  # palettes are user-extensible

    font_family: str = "Inter"
    type_scale_base_vmin: float = 3.2
    min_font_px: int = 22
    primary_color: str = "#E63946"
    secondary_color: str = "#4EA8DE"
    background_color: str = "#0D1117"
    text_color: str = "#F8F9FA"
    muted_color: str = "#8B949E"


class SegmentationConfig(BaseModel):
    target_seconds: float = 12.0
    min_seconds: float = 4.0
    max_seconds: float = 20.0
    words_per_minute: float = 165.0


class ProvidersConfig(BaseModel):
    llm: str = "gemini-2.5-flash"
    tts: str = "edge-tts"
    aligner: str = "native"
    assets: str = "null"
    renderer: str = "remotion"


class TTSConfig(BaseModel):
    """TTS settings.

    `voices` holds one entry per provider because a voice identifier is
    provider-specific (a Cartesia voice is a UUID; an edge-tts voice is a name
    like "en-US-AriaNeural"). Without this, switching providers would mean
    hand-editing the voice string too — which is the "rewrite" R7 forbids.

    `rate` stays in the provider-neutral "+10%" form; each provider translates.
    """

    voices: dict[str, str] = Field(
        default_factory=lambda: {
            # Cartesia: "Sophie" — a stock English conversational voice.
            "cartesia": "bf0a246a-8642-498a-9950-80c35e9276b5",
            "edge-tts": "en-US-AriaNeural",
            "piper": "en_US-lessac-medium",
        }
    )
    # Explicit override; wins over the per-provider table when set.
    voice: str | None = None
    rate: str = "+0%"
    model: str = "sonic-3.5"   # Cartesia model_id; ignored by other providers
    language: str = "en"
    sample_rate: int = 24000
    channels: int = 1

    def voice_for(self, provider: str) -> str:
        if self.voice:
            return self.voice
        try:
            return self.voices[provider]
        except KeyError:
            raise ValueError(
                f"No voice configured for TTS provider {provider!r}. "
                f"Add tts.voices.{provider} to config.yaml, or set tts.voice."
            ) from None


class AudioConfig(BaseModel):
    scene_gap_ms: int = 250
    lead_in_ms: int = 300
    tail_ms: int = 500


class DeterminismConfig(BaseModel):
    require_cache: bool = False
    ban_wallclock: bool = True


class QAConfig(BaseModel):
    ocr_gate: bool = False
    ocr_min_confidence: int = 60


class Config(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = SCHEMA_VERSION
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    theme: ThemeConfig = Field(default_factory=ThemeConfig)
    segmentation: SegmentationConfig = Field(default_factory=SegmentationConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    determinism: DeterminismConfig = Field(default_factory=DeterminismConfig)
    qa: QAConfig = Field(default_factory=QAConfig)
    profiles: dict[str, dict[str, Any]] = Field(default_factory=dict)
