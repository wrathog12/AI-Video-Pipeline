"""Continuous audio track assembly (R5).

Builds ONE audio track for the whole video by concatenating per-scene PCM in the
sample domain, padding each scene to an exact whole number of video frames.

This replaces the obvious approach of rendering audio into each scene MP4 and
stitching with `concat -c copy`. Concatenating AAC streams inserts encoder
priming (~20 ms) at every boundary; across ~15 scenes that accumulates well past
the ±150 ms sync budget and produces audible clicks at each seam. Assembling
uncompressed samples and encoding once at the end makes A/V sync structural:
scene N's audio starts at exactly `sum(previous frame counts) / fps` seconds.

Every scene's duration is therefore frame-quantised, and `scene.start_ms` /
`scene.duration_ms` in the IR are written from these quantised values so the
spec never disagrees with the rendered artifact.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ScenePlacement:
    """Where a scene sits in the finished video, in exact frames."""

    scene_id: str
    start_frame: int
    frames: int
    fps: int = 30

    @property
    def start_ms(self) -> float:
        return 1000.0 * self.start_frame / self.fps

    @property
    def duration_ms(self) -> float:
        return 1000.0 * self.frames / self.fps


def frames_for_ms(ms: float, fps: int) -> int:
    """Ceil, never round: truncating audio mid-word is worse than a few idle ms."""
    return max(1, int(np.ceil(ms * fps / 1000.0)))


def build_track(
    scene_pcms: list[tuple[str, np.ndarray]],
    *,
    sample_rate: int,
    fps: int,
    lead_in_ms: int = 0,
    scene_gap_ms: int = 0,
    tail_ms: int = 0,
) -> tuple[np.ndarray, list[ScenePlacement]]:
    """Concatenate scene audio into one track.

    Returns the track and, for each scene, its exact frame placement. The gap is
    added *after* every scene except the last; the tail closes out the video.
    """
    if not scene_pcms:
        raise ValueError("build_track received no scenes")

    chunks: list[np.ndarray] = []
    placements: list[ScenePlacement] = []
    cursor_frames = 0

    if lead_in_ms > 0:
        lead_frames = frames_for_ms(lead_in_ms, fps)
        chunks.append(_silence(lead_frames, fps, sample_rate))
        cursor_frames += lead_frames

    last = len(scene_pcms) - 1
    for i, (scene_id, pcm) in enumerate(scene_pcms):
        speech_ms = 1000.0 * len(pcm) / sample_rate
        gap_ms = scene_gap_ms if i < last else tail_ms
        # Quantise the whole scene (speech + trailing gap) to whole frames, so
        # the next scene starts precisely on a frame boundary.
        scene_frames = frames_for_ms(speech_ms + gap_ms, fps)
        target_samples = _samples_for_frames(scene_frames, fps, sample_rate)

        padded = np.zeros(target_samples, dtype=np.float32)
        n = min(len(pcm), target_samples)
        padded[:n] = pcm[:n].astype(np.float32, copy=False)
        chunks.append(padded)

        placements.append(
            ScenePlacement(
                scene_id=scene_id,
                start_frame=cursor_frames,
                frames=scene_frames,
                fps=fps,
            )
        )
        cursor_frames += scene_frames

    track = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    # Guard the invariant the whole design rests on: the track must be exactly
    # as long as the video. If this trips, a rounding path above is wrong.
    expected = _samples_for_frames(cursor_frames, fps, sample_rate)
    assert len(track) == expected, f"track {len(track)} != expected {expected} samples"
    return track, placements


def _samples_for_frames(frames: int, fps: int, sample_rate: int) -> int:
    # Integer arithmetic: float division here would drift over a long video.
    return frames * sample_rate // fps


def _silence(frames: int, fps: int, sample_rate: int) -> np.ndarray:
    return np.zeros(_samples_for_frames(frames, fps, sample_rate), dtype=np.float32)


def write_wav(path, pcm: np.ndarray, sample_rate: int) -> None:
    """Write 16-bit PCM WAV.

    16-bit rather than float32 because it is what the AAC encoder wants anyway,
    and it keeps the intermediate file half the size.

    `format` is passed explicitly rather than inferred from the extension, so
    that temp names like `<hash>.wav.tmp` (used for atomic cache writes) work.
    """
    import soundfile as sf

    # Write pre-quantised int16 so the on-disk samples are exactly what
    # pcm_hash() hashed — no reliance on libsndfile's internal rounding.
    sf.write(str(path), to_int16(pcm), sample_rate, subtype="PCM_16", format="WAV")


def to_int16(pcm: np.ndarray) -> np.ndarray:
    """Quantise float PCM to int16 on the same grid soundfile writes.

    The scale factor is 32768, not 32767, and rounding is explicit. This makes
    the mapping idempotent for samples that already sit on the int16 grid: a
    value read back from a PCM_16 WAV is exactly i/32768, and round(i) == i.

    Getting this wrong is not cosmetic. With `* 32767` the hash of freshly
    synthesised audio differed from the hash of the same audio after a WAV
    round-trip, so the alignment cache missed on every run and every scene
    re-hit the network — the audio cache would hit while the alignment cache
    never did.
    """
    clipped = np.clip(np.asarray(pcm, dtype=np.float64), -1.0, 1.0)
    return np.clip(np.round(clipped * 32768.0), -32768, 32767).astype(np.int16)


def pcm_hash(pcm: np.ndarray, sample_rate: int) -> str:
    """Hash decoded samples, never container bytes.

    Container headers differ run to run; samples do not. Quantising first makes
    the key stable across the float32 -> WAV -> float32 round-trip.
    """
    import hashlib

    from .cache import sha256_obj

    digest = hashlib.sha256(to_int16(pcm).tobytes()).hexdigest()
    return sha256_obj({"pcm": digest, "sr": sample_rate})
