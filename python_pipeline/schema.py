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


class WordTrigger(Strict):
    """A word's timing, in ms relative to the start of its own scene's audio."""

    word: str
    start_ms: int
    end_ms: int


class AssetRef(Strict):
    kind: Literal["svg", "image", "none"] = "none"
    id: str = ""
    path: str | None = None


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
    prompt_sha256: str = ""
    tts_provider: str = ""
    tts_voice: str = ""
    aligner: str = ""
    fps: int = 30
    width: int = 1920
    height: int = 1080
    output_scale: float = 1.0
    theme_sha256: str = ""
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
    llm: str = "claude-opus-5"
    tts: str = "edge-tts"
    aligner: str = "native"
    assets: str = "null"
    renderer: str = "remotion"


class TTSConfig(BaseModel):
    voice: str = "en-US-AriaNeural"
    rate: str = "+0%"
    sample_rate: int = 24000
    channels: int = 1


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
