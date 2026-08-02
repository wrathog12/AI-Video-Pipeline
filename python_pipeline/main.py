"""Pipeline orchestrator and CLI (R1).

Stages, in order:

    script  --1--> segments        deterministic Python (segmenter.py)
            --2--> annotations     the LLM's only job (llm_annotator.py)
            --3--> resolved IR     symbolic evaluation (evaluator.py)
            --4--> audio + timings TTS + aligner, per scene, cached
            --5--> one PCM track   frame-quantised (audio_track.py)
            --6--> scene MP4s      video-only, cached (renderer.py)
            --7--> final MP4       a single mux (mux.py)
            --8--> scene_spec.json the inspectable artifact (R6)

Every stage boundary is a cache boundary, which is what makes an edit to one
scene re-render one scene (R8).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import annotate as annotate_mod
from . import audio_track, evaluator, mux, segmenter
from .align.native import NativeAligner
from .assets import base as assets_base
from .cache import (
    Cache,
    CacheReport,
    align_key,
    audio_key,
    engine_fingerprint,
    render_key,
    sha256_obj,
    sha256_text,
    spec_key,
)
from .env import load_env, redact
from .llm_annotator import LLMError, get_annotator, prompt_fingerprint_for
from .renderer import get_renderer
from .schema import (
    Annotation,
    Config,
    DerivedFrom,
    Provenance,
    Scene,
    SceneSpec,
    WordTrigger,
)
from .tts.edge import EdgeTTS

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Provider selection (R7). Adding a provider means adding a row here, not
# touching the pipeline.
# ---------------------------------------------------------------------------


def get_tts(cfg: Config):
    name = cfg.providers.tts
    if name == "cartesia":
        from .tts.cartesia import CartesiaTTS

        return CartesiaTTS(model_id=cfg.tts.model, language=cfg.tts.language)
    if name == "edge-tts":
        return EdgeTTS()
    if name == "piper":
        from .tts.piper import PiperTTS  # Phase 3

        return PiperTTS()
    raise ValueError(
        f"Unknown TTS provider: {name!r} (available: cartesia, edge-tts, piper)"
    )


def get_asset_provider(cfg: Config):
    name = cfg.providers.assets
    if name == "null":
        from .assets.null import NullAssetProvider

        return NullAssetProvider()
    if name == "icon_pack":
        from .assets.icon_pack import IconPackProvider

        return IconPackProvider()
    raise ValueError(f"Unknown asset provider: {name!r} (available: null, icon_pack)")


def get_aligner(cfg: Config):
    name = cfg.providers.aligner
    if name == "native":
        return NativeAligner()
    if name == "whisperx":
        from .align.whisperx import WhisperXAligner  # Phase 3

        return WhisperXAligner()
    raise ValueError(f"Unknown aligner: {name!r} (available: native, whisperx)")


def load_config(path: Path, profile: str | None = None) -> Config:
    import yaml

    raw = yaml.safe_load(Path(path).read_text("utf-8")) or {}
    if profile:
        profiles = raw.get("profiles") or {}
        if profile not in profiles:
            raise ValueError(
                f"Profile {profile!r} not in config. Available: {sorted(profiles)}"
            )
        raw = _deep_merge(raw, profiles[profile])
    return Config.model_validate(raw)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Phase 0 placeholder spec. Replaced by segmenter + annotator in Phase 2.
# Values carry resolved strings that Python would have computed, so the R4
# boundary (templates display, never compute) is already exercised.
# ---------------------------------------------------------------------------


def skeleton_spec() -> SceneSpec:
    return SceneSpec(
        project_title="Walking Skeleton",
        scenes=[
            Scene(
                scene_id="scene_01",
                template_name="ExpressionCard",
                narration_text=(
                    "Standard color imaging uses an 8-bit system for each channel. "
                    "Mathematically, this means two to the eighth power, resulting in "
                    "256 possible combinations per channel."
                ),
                props={
                    "title": "Bit depth",
                    "expression": {
                        "label": "Two to the eighth power",
                        "expr": "2**8",
                        "format": "int",
                        "unit": "levels per channel",
                        "resolved": "256",
                    },
                    "items": [
                        {
                            "label": "Value range",
                            "expr": None,
                            "format": "range",
                            "unit": None,
                            "resolved": "0-255",
                        }
                    ],
                },
            ),
            Scene(
                scene_id="scene_02",
                template_name="Fallback",
                narration_text=(
                    "256 times 256 times 256 equals 16,777,216. That is nearly "
                    "16.8 million unique colors."
                ),
                props={
                    "title": "Total addressable colors",
                    "expression": {
                        "label": "256 x 256 x 256",
                        "expr": "256**3",
                        "format": "thousands",
                        "unit": None,
                        "resolved": "16,777,216",
                    },
                },
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Stage 1-3: script -> annotated, resolved IR
# ---------------------------------------------------------------------------


def build_spec_from_script(
    script_text: str, cfg: Config, spec_cache: Cache, report: CacheReport, args: argparse.Namespace
) -> tuple[SceneSpec, str, str]:
    """Run segmentation, annotation and evaluation.

    Returns (spec, annotator_name, model_id). The annotator actually used is
    returned rather than the one configured, because falling back to the
    heuristic path silently would make provenance a lie.
    """
    # -- stage 1: deterministic segmentation --------------------------------
    title, segments = segmenter.segment_and_verify(
        script_text,
        target_seconds=cfg.segmentation.target_seconds,
        min_seconds=cfg.segmentation.min_seconds,
        max_seconds=cfg.segmentation.max_seconds,
        words_per_minute=cfg.segmentation.words_per_minute,
    )
    total_words = sum(s.word_count for s in segments)
    log(
        f"segmented: {len(segments)} scenes, {total_words} words, "
        f"~{sum(s.estimated_seconds for s in segments) / 60:.1f} min estimated"
    )
    if title:
        log(f"title: {title!r} (not narrated)")

    # -- stage 2: annotation ------------------------------------------------
    requested = args.annotator or cfg.providers.llm
    annotator = get_annotator(requested)
    model_id = getattr(annotator, "model", annotator.name)
    fingerprint = prompt_fingerprint_for(annotator)

    # The cache key covers the *normalised* script, so trailing-whitespace edits
    # do not force a re-annotation, while any real text change does.
    normalized = segmenter.normalize_script(script_text)
    key = spec_key(
        script=normalized,
        prompt=fingerprint,
        model_id=model_id,
        schema_version=cfg.schema_version,
    )

    cached = spec_cache.get_json(key)
    if cached is not None:
        annotations = [Annotation.model_validate(a) for a in cached]
        report.record("annotate", "script", True, key)
        log(f"annotations: {len(annotations)} from cache ({annotator.name})")
    else:
        log(f"annotating {len(segments)} segments with {annotator.name} ({model_id})")
        try:
            annotations = annotator.annotate(title, segments)
        except LLMError as exc:
            # R2: the demo must still produce a video. Loud, not silent.
            log(f"WARNING: {annotator.name} annotation failed: {exc}")
            log("WARNING: falling back to the offline heuristic annotator")
            from .heuristic_annotator import HeuristicAnnotator

            annotator = HeuristicAnnotator()
            model_id = annotator.name
            annotations = annotator.annotate(title, segments)
            key = spec_key(
                script=normalized,
                prompt=prompt_fingerprint_for(annotator),
                model_id=model_id,
                schema_version=cfg.schema_version,
            )
        spec_cache.put_json(key, [a.model_dump() for a in annotations])
        report.record("annotate", "script", False, key, "script/prompt/model changed")

    annotations, warnings = annotate_mod.reconcile(annotations, segments)
    for warning in warnings:
        log(f"  annotation repair: {warning}")

    spec, cue_warnings = annotate_mod.build_spec(title or "Untitled", segments, annotations)
    for warning in cue_warnings:
        log(f"  cue repair: {warning}")

    # -- stage 3: symbolic evaluation (R4) ----------------------------------
    resolutions, value_warnings = evaluator.resolve_scene_spec(spec)
    for warning in value_warnings:
        log(f"  value dropped: {warning}")
    ok = [r for r in resolutions if not r.error]
    computed = sum(1 for r in ok if r.computed)
    log(f"resolved {len(ok)} on-screen values, {computed} computed from expressions")
    if args.explain_values:
        print("\n" + evaluator.render_resolutions(resolutions) + "\n")

    # -- stage 3.5: visual assets (R7) --------------------------------------
    #
    # Runs here rather than in `run()` so that it applies to the script path only.
    # A spec handed in with --spec is rendered exactly as written: R6's promise is
    # that the artifact is the contract, and silently adding icons to a loaded spec
    # would mean the video no longer matches the file that produced it. Delete an
    # asset from a spec and re-render, and it stays deleted.
    provider = get_asset_provider(cfg)
    matched = 0
    for scene in spec.scenes:
        # Ranked against the whole script, not this segment: a word the script says
        # once is what this scene is about, while a word it repeats throughout is
        # background vocabulary. Same rarest-wins reasoning as cue repair.
        keywords = assets_base.rank_by_rarity(
            assets_base.keywords_of(scene.narration_text), script_text
        )
        scene.assets = provider.resolve(keywords)
        if scene.assets:
            matched += 1
    if provider.name == "null":
        log(f"assets: none ({provider.name} provider)")
    else:
        picked = ", ".join(
            f"{s.scene_id}:{s.assets[0].id}" for s in spec.scenes if s.assets
        )
        log(f"assets: {provider.name} matched {matched}/{len(spec.scenes)} scenes")
        if picked:
            log(f"  {picked}")

    templates = ", ".join(f"{s.scene_id}:{s.template_name}" for s in spec.scenes)
    log(f"templates: {templates}")
    return spec, annotator.name, model_id


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    loaded_keys = load_env()
    if loaded_keys:
        log(f".env loaded: {', '.join(sorted(loaded_keys))}")

    cfg = load_config(Path(args.config), args.profile)
    cache_root = Path(args.cache_dir)
    use_cache = not args.no_cache
    report = CacheReport()

    # The spec tier is flagged non-deterministic: its content comes from an LLM, so
    # --no-cache must not write through and clobber a good entry with a different
    # one. See Cache.deterministic_content.
    spec_cache = Cache(cache_root, "spec", enabled=use_cache, deterministic_content=False)
    audio_cache = Cache(cache_root, "audio", enabled=use_cache)
    align_cache = Cache(cache_root, "align", enabled=use_cache)
    scene_cache = Cache(cache_root, "scenes", enabled=use_cache)

    # -- stage 1: obtain the IR ------------------------------------------------
    annotator_name = "none"
    llm_model = "none"
    if args.spec:
        # R6: re-render from an edited spec, no LLM and no segmentation involved.
        spec = SceneSpec.model_validate_json(Path(args.spec).read_text("utf-8"))
        log(f"loaded spec: {args.spec} ({len(spec.scenes)} scenes)")
        # An edited spec may carry a new `expr`, so values are re-resolved. Edit
        # expr="2**8" to expr="2**10" and re-run: the number on screen changes
        # without the LLM being consulted. That is the R4 + R6 demo.
        resolutions, value_warnings = evaluator.resolve_scene_spec(spec)
        for warning in value_warnings:
            log(f"  value dropped: {warning}")
        log(f"re-resolved {len(resolutions)} values from the edited spec")
        if args.explain_values:
            print("\n" + evaluator.render_resolutions(resolutions) + "\n")
    elif args.script:
        script_text = Path(args.script).read_text("utf-8")
        spec, annotator_name, llm_model = build_spec_from_script(
            script_text, cfg, spec_cache, report, args
        )
    else:
        spec = skeleton_spec()
        log("no --script/--spec given; using the built-in skeleton spec")

    if args.dry_run:
        # Annotation and evaluation take seconds; TTS and rendering take minutes.
        # Iterating on a prompt or a template choice should not pay for a render.
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path = out_path.with_suffix(".spec.json")
        spec.provenance = Provenance(
            script_sha256=sha256_text(Path(args.script).read_text("utf-8")) if args.script else "",
            llm_model=llm_model,
            annotator=annotator_name,
            theme_sha256=sha256_obj(cfg.theme.model_dump()),
            fps=cfg.project.fps,
            width=cfg.project.width,
            height=cfg.project.height,
            output_scale=cfg.project.output_scale,
        )
        spec_path.write_text(
            json.dumps(spec.model_dump(), indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        log(f"dry run: wrote {spec_path} (no audio, no render)")
        log(f"elapsed {time.perf_counter() - t0:.1f}s")
        if args.explain_cache:
            print("\n" + report.render())
        return 0

    theme_sha = sha256_obj(cfg.theme.model_dump())
    # Fingerprint the renderer sources so a template edit invalidates the render
    # cache. Without this, changing a component serves last run's frames and the
    # edit looks broken rather than cached (see cache.engine_fingerprint).
    engine_sha = engine_fingerprint(ROOT / "remotion_engine")
    tts = get_tts(cfg)
    aligner = get_aligner(cfg)
    renderer = get_renderer(cfg, ROOT / "remotion_engine")

    # Voice identifiers are provider-specific, so resolve once against the
    # active provider rather than assuming a single global voice string.
    voice = cfg.tts.voice_for(tts.name)
    # Only Cartesia varies by model; other providers report "-" so the value is
    # still a stable, explicit part of the cache key.
    tts_model = cfg.tts.model if tts.name == "cartesia" else "-"
    log(f"tts: {tts.name} voice={voice} model={tts_model} | aligner: {aligner.name}")

    # -- stage 2: audio + alignment per scene ---------------------------------
    scene_pcms: list[tuple[str, Any]] = []
    for scene in spec.scenes:
        a_key = audio_key(
            normalized_text=scene.narration_text,
            provider=tts.name,
            voice=voice,
            rate=cfg.tts.rate,
            sample_rate=cfg.tts.sample_rate,
            model=tts_model,
        )
        cached_wav = audio_cache.get(a_key, ".wav")
        if cached_wav:
            import soundfile as sf

            pcm, sr = sf.read(str(cached_wav), dtype="float32", always_2d=False)
            boundaries = None
            report.record("audio", scene.scene_id, True, a_key)
        else:
            log(f"synthesising {scene.scene_id} ({len(scene.narration_text)} chars)")
            result = tts.synthesize(
                scene.narration_text,
                voice=voice,
                rate=cfg.tts.rate,
                sample_rate=cfg.tts.sample_rate,
            )
            pcm, sr = result.pcm, result.sample_rate
            boundaries = result.word_boundaries
            staged = audio_cache.staging_path(a_key, ".wav")
            audio_track.write_wav(staged, pcm, sr)
            audio_cache.put_file(a_key, ".wav", staged)
            report.record("audio", scene.scene_id, False, a_key, "not synthesised before")

        pcm_sha = audio_track.pcm_hash(pcm, sr)

        # Alignment. Cached separately from audio so a re-run with the same audio
        # never re-aligns, and so the cache survives an audio cache hit (where
        # the provider's native boundaries are no longer in hand).
        al_key = align_key(
            audio_pcm_sha256=pcm_sha, text=scene.narration_text, aligner=aligner.name
        )
        cached_align = align_cache.get_json(al_key)
        if cached_align is not None:
            scene.word_triggers = [WordTrigger.model_validate(t) for t in cached_align]
            report.record("align", scene.scene_id, True, al_key)
        else:
            if boundaries is None:
                # Audio came from cache, so native boundaries are gone. Re-synthesise
                # once to recover them; the result then caches for good.
                log(f"re-synthesising {scene.scene_id} to recover word timings")
                result = tts.synthesize(
                    scene.narration_text,
                    voice=voice,
                    rate=cfg.tts.rate,
                    sample_rate=cfg.tts.sample_rate,
                )
                boundaries = result.word_boundaries
            triggers = aligner.align(pcm, sr, scene.narration_text, boundaries)
            scene.word_triggers = triggers
            align_cache.put_json(al_key, [t.model_dump() for t in triggers])
            report.record("align", scene.scene_id, False, al_key, "no alignment cached")

        scene.derived_from = DerivedFrom(
            audio_pcm_sha256=pcm_sha, narration_sha256=sha256_text(scene.narration_text)
        )
        scene_pcms.append((scene.scene_id, pcm))

    # -- stage 3: continuous audio track (frame-aligned) ----------------------
    track, placements = audio_track.build_track(
        scene_pcms,
        sample_rate=cfg.tts.sample_rate,
        fps=cfg.project.fps,
        lead_in_ms=cfg.audio.lead_in_ms,
        scene_gap_ms=cfg.audio.scene_gap_ms,
        tail_ms=cfg.audio.tail_ms,
    )
    # Write quantised placements back into the IR so the spec cannot disagree
    # with the artifact.
    for scene, place in zip(spec.scenes, placements):
        scene.start_ms = round(place.start_ms)
        scene.duration_ms = round(place.duration_ms)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    track_wav = out_path.parent / f".{out_path.stem}.track.wav"
    audio_track.write_wav(track_wav, track, cfg.tts.sample_rate)
    total_frames = sum(p.frames for p in placements) + (
        audio_track.frames_for_ms(cfg.audio.lead_in_ms, cfg.project.fps)
        if cfg.audio.lead_in_ms > 0
        else 0
    )
    log(
        f"audio track: {len(track) / cfg.tts.sample_rate:.2f}s, "
        f"{total_frames} frames @ {cfg.project.fps}fps"
    )

    # -- stage 4: render scenes (video only, cached) --------------------------
    scene_files: list[Path] = []

    # A lead-in gap needs a matching video segment or A/V would offset by its
    # length. Render it as a held first frame... simplest correct approach is to
    # fold the lead-in into scene 1's frame count.
    lead_frames = (
        audio_track.frames_for_ms(cfg.audio.lead_in_ms, cfg.project.fps)
        if cfg.audio.lead_in_ms > 0
        else 0
    )

    for i, (scene, place) in enumerate(zip(spec.scenes, placements)):
        frames = place.frames + (lead_frames if i == 0 else 0)
        payload = renderer.scene_payload(scene, cfg, frames=frames)
        r_key = render_key(
            template_name=scene.template_name,
            props=scene.props,
            assets=[a.model_dump() for a in scene.assets],
            word_triggers=[t.model_dump() for t in scene.word_triggers],
            duration_ms=frames * 1000 // cfg.project.fps,
            theme_sha256=theme_sha,
            engine_sha256=engine_sha,
            fps=cfg.project.fps,
            width=cfg.project.width,
            height=cfg.project.height,
            output_scale=cfg.project.output_scale,
        )
        cached_mp4 = scene_cache.get(r_key, ".mp4")
        if cached_mp4:
            scene_files.append(cached_mp4)
            report.record("render", scene.scene_id, True, r_key)
            continue

        log(f"rendering {scene.scene_id} [{scene.template_name}] {frames} frames")
        staged_mp4 = scene_cache.staging_path(r_key, ".mp4")
        renderer.render_scene(scene, cfg, frames=frames, out_path=staged_mp4)
        final = scene_cache.put_file(r_key, ".mp4", staged_mp4)
        scene_files.append(final)
        report.record("render", scene.scene_id, False, r_key, "props/theme/dims changed")

    # -- stage 5: single mux --------------------------------------------------
    log("muxing")
    mux.concat_and_mux(scene_files, track_wav, out_path)
    track_wav.unlink(missing_ok=True)

    # -- stage 6: write the IR alongside the video (R6 submission artifact) ---
    spec.provenance = Provenance(
        script_sha256=sha256_text(Path(args.script).read_text("utf-8")) if args.script else "",
        # What actually ran, not what config would have used. When the LLM is
        # unreachable the run falls back to the heuristic annotator, and a
        # provenance block claiming a model that never answered is worse than
        # one that admits it.
        llm_model=llm_model,
        annotator=annotator_name,
        tts_provider=tts.name,
        tts_voice=voice,
        tts_model=tts_model,
        aligner=aligner.name,
        fps=cfg.project.fps,
        width=cfg.project.width,
        height=cfg.project.height,
        output_scale=cfg.project.output_scale,
        theme_sha256=theme_sha,
        engine_sha256=engine_sha,
    )
    spec_path = out_path.with_suffix(".spec.json")
    spec_path.write_text(
        json.dumps(spec.model_dump(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )

    info = mux.probe(out_path)
    log(f"wrote {out_path}  ({out_path.stat().st_size / 1e6:.2f} MB)")
    log(f"      {info.get('video', '?')}, frames={info.get('frames', '?')}, "
        f"duration={info.get('duration', '?')}s")
    log(f"      audio: {info.get('audio', '?')}, duration={info.get('audio_duration', '?')}s")
    log(f"wrote {spec_path}")
    log(f"elapsed {time.perf_counter() - t0:.1f}s")

    if args.explain_cache:
        print("\n" + report.render())
    return 0


def log(msg: str) -> None:
    print(f"[engine] {msg}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run",
        description="Turn a narration script into a finished explainer video.",
    )
    src = p.add_argument_group("input")
    src.add_argument("--script", help="path to a plain-text narration script")
    src.add_argument(
        "--spec",
        help="render from an existing (possibly hand-edited) scene spec, skipping the LLM (R6)",
    )
    p.add_argument("--out", default="output/video.mp4", help="output .mp4 path")
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    p.add_argument("--profile", help="config profile to overlay, e.g. portrait")
    p.add_argument(
        "--annotator",
        help="override providers.llm for this run: a model id, or 'heuristic' to skip the LLM",
    )
    p.add_argument("--cache-dir", default=str(ROOT / ".cache"))
    p.add_argument("--no-cache", action="store_true", help="ignore all cached artifacts")
    p.add_argument(
        "--explain-cache", action="store_true", help="print per-scene cache hit/miss (R8)"
    )
    p.add_argument(
        "--explain-values",
        action="store_true",
        help="print every on-screen value with the expression it came from (R4)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="stop after writing the spec: no TTS, no render (fast annotation check)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001 - a CLI should not dump a traceback
        print(f"\n[engine] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
