"""Pipeline orchestrator and CLI (R1).

Phase 0 scope — the walking skeleton. A hardcoded two-scene spec is carried all
the way to a playing MP4, exercising every structural decision that is expensive
to change later: the IR, ms-based timings, native word alignment, the two-tier
cache, video-only rendering, continuous-audio assembly, single mux.

Script ingestion (segmenter, annotator, evaluator) lands in Phase 2 and slots in
ahead of stage 2 below without disturbing anything downstream.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import audio_track, mux
from .align.native import NativeAligner
from .cache import Cache, CacheReport, align_key, audio_key, render_key, sha256_obj, sha256_text
from .renderer import get_renderer
from .schema import Config, DerivedFrom, Provenance, Scene, SceneSpec, WordTrigger
from .tts.edge import EdgeTTS

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Provider selection (R7). Adding a provider means adding a row here, not
# touching the pipeline.
# ---------------------------------------------------------------------------


def get_tts(cfg: Config):
    name = cfg.providers.tts
    if name == "edge-tts":
        return EdgeTTS()
    if name == "piper":
        from .tts.piper import PiperTTS  # Phase 3

        return PiperTTS()
    raise ValueError(f"Unknown TTS provider: {name!r} (available: edge-tts, piper)")


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
# The pipeline
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    cfg = load_config(Path(args.config), args.profile)
    cache_root = Path(args.cache_dir)
    use_cache = not args.no_cache
    report = CacheReport()

    audio_cache = Cache(cache_root, "audio", enabled=use_cache)
    align_cache = Cache(cache_root, "align", enabled=use_cache)
    scene_cache = Cache(cache_root, "scenes", enabled=use_cache)

    # -- stage 1: obtain the IR ------------------------------------------------
    if args.spec:
        # R6: re-render from an edited spec, no LLM involved.
        spec = SceneSpec.model_validate_json(Path(args.spec).read_text("utf-8"))
        log(f"loaded spec: {args.spec} ({len(spec.scenes)} scenes)")
    elif args.script:
        # Phase 2 replaces this branch with segmenter -> annotator -> evaluator.
        log(f"NOTE: script ingestion arrives in Phase 2; using the skeleton spec")
        spec = skeleton_spec()
    else:
        spec = skeleton_spec()
        log("no --script/--spec given; using the built-in skeleton spec")

    theme_sha = sha256_obj(cfg.theme.model_dump())
    tts = get_tts(cfg)
    aligner = get_aligner(cfg)
    renderer = get_renderer(cfg, ROOT / "remotion_engine")

    # -- stage 2: audio + alignment per scene ---------------------------------
    scene_pcms: list[tuple[str, Any]] = []
    for scene in spec.scenes:
        a_key = audio_key(
            normalized_text=scene.narration_text,
            provider=tts.name,
            voice=cfg.tts.voice,
            rate=cfg.tts.rate,
            sample_rate=cfg.tts.sample_rate,
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
                voice=cfg.tts.voice,
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
                    voice=cfg.tts.voice,
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
            word_triggers=[t.model_dump() for t in scene.word_triggers],
            duration_ms=frames * 1000 // cfg.project.fps,
            theme_sha256=theme_sha,
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
        llm_model=cfg.providers.llm,
        tts_provider=tts.name,
        tts_voice=cfg.tts.voice,
        aligner=aligner.name,
        fps=cfg.project.fps,
        width=cfg.project.width,
        height=cfg.project.height,
        output_scale=cfg.project.output_scale,
        theme_sha256=theme_sha,
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
    p.add_argument("--cache-dir", default=str(ROOT / ".cache"))
    p.add_argument("--no-cache", action="store_true", help="ignore all cached artifacts")
    p.add_argument(
        "--explain-cache", action="store_true", help="print per-scene cache hit/miss (R8)"
    )
    p.add_argument(
        "--from-stage",
        choices=["audio", "align", "render", "mux"],
        help="resume from a stage (reserved for Phase 1)",
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
