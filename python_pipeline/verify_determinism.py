"""Prove two runs produced the same video (R3).

Usage:
    python -m python_pipeline.verify_determinism a.mp4 b.mp4

Why not just compare file bytes? Because byte equality is the wrong claim. It
holds today and breaks the day ffmpeg is upgraded, since the encoder writes its
version into the container and may reorder atoms — while every decoded frame stays
identical. Claiming byte equality would therefore promise something the design
does not control, and the promise would fail in front of a reviewer for a reason
that has nothing to do with the pipeline.

So the claim made here is: **every decoded video frame and every decoded audio
sample is identical.** That is what "the same video" means, it is what a viewer
can perceive, and it survives a toolchain upgrade.

Frames are hashed one at a time out of an ffmpeg rawvideo pipe rather than
decoded into memory, so a 4000-frame 1080p video does not need 25 GB of RAM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class StreamInfo:
    width: int
    height: int
    fps_num: int
    fps_den: int
    frames: int
    sample_rate: int
    channels: int

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * 3   # rgb24

    def describe(self) -> str:
        fps = self.fps_num / max(self.fps_den, 1)
        return (
            f"{self.width}x{self.height} @ {fps:g}fps, {self.frames} frames, "
            f"audio {self.sample_rate}Hz ch{self.channels}"
        )


def probe(path: Path) -> StreamInfo:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_streams", "-count_frames", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    streams = json.loads(out)["streams"]
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise SystemExit(f"{path}: no video stream")

    num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
    return StreamInfo(
        width=int(video["width"]),
        height=int(video["height"]),
        fps_num=int(num or 0),
        fps_den=int(den or 1),
        frames=int(video.get("nb_read_frames") or video.get("nb_frames") or 0),
        sample_rate=int(audio["sample_rate"]) if audio else 0,
        channels=int(audio.get("channels", 0)) if audio else 0,
    )


def frame_hashes(path: Path, info: StreamInfo) -> list[str]:
    """SHA-256 of every decoded frame, in order.

    `-f rawvideo -pix_fmt rgb24` is deliberate: hashing the compressed stream
    would compare encoder output rather than pixels, which is the byte-equality
    trap this module exists to avoid.
    """
    proc = subprocess.Popen(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        stdout=subprocess.PIPE,
    )
    hashes: list[str] = []
    size = info.frame_bytes
    assert proc.stdout is not None
    try:
        while True:
            # readinto-style exact reads: a short read means end of stream, and a
            # partial frame must not be hashed as if it were whole.
            buf = proc.stdout.read(size)
            if not buf:
                break
            if len(buf) < size:
                raise SystemExit(
                    f"{path}: truncated frame ({len(buf)} of {size} bytes) at frame {len(hashes)}"
                )
            hashes.append(hashlib.sha256(buf).hexdigest())
    finally:
        proc.stdout.close()
        proc.wait()
    if proc.returncode not in (0, None):
        raise SystemExit(f"{path}: ffmpeg exited {proc.returncode}")
    return hashes


def audio_hash(path: Path) -> str:
    """SHA-256 of the decoded audio as 16-bit PCM.

    s16le rather than f32le: AAC decoding is not bit-exact across ffmpeg builds
    in the last float bits, so a float comparison would report a difference no
    listener could hear. 16-bit is the resolution actually delivered.
    """
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-f", "s16le", "-acodec", "pcm_s16le", "-",
        ],
        capture_output=True, check=True,
    ).stdout
    return hashlib.sha256(raw).hexdigest()


def compare(a: Path, b: Path, *, quiet: bool = False) -> int:
    def say(msg: str) -> None:
        if not quiet:
            print(msg)

    info_a, info_b = probe(a), probe(b)
    say(f"A  {a.name}: {info_a.describe()}")
    say(f"B  {b.name}: {info_b.describe()}")

    failures: list[str] = []

    if (info_a.width, info_a.height) != (info_b.width, info_b.height):
        failures.append(
            f"dimensions differ: {info_a.width}x{info_a.height} vs {info_b.width}x{info_b.height}"
        )
    if (info_a.fps_num, info_a.fps_den) != (info_b.fps_num, info_b.fps_den):
        failures.append("frame rates differ")

    say("hashing frames...")
    hashes_a, hashes_b = frame_hashes(a, info_a), frame_hashes(b, info_b)

    if len(hashes_a) != len(hashes_b):
        failures.append(f"frame count differs: {len(hashes_a)} vs {len(hashes_b)}")

    first_diff = next(
        (i for i, (x, y) in enumerate(zip(hashes_a, hashes_b)) if x != y), None
    )
    if first_diff is not None:
        differing = sum(1 for x, y in zip(hashes_a, hashes_b) if x != y)
        fps = info_a.fps_num / max(info_a.fps_den, 1)
        failures.append(
            f"{differing} of {min(len(hashes_a), len(hashes_b))} frames differ; "
            f"first at index {first_diff} (t={first_diff / max(fps, 1):.3f}s)\n"
            f"    A: {hashes_a[first_diff][:16]}\n"
            f"    B: {hashes_b[first_diff][:16]}\n"
            f"    inspect: ffmpeg -i {a.name} -vf select=eq(n\\,{first_diff}) -vframes 1 a.png"
        )
    else:
        say(f"video: {len(hashes_a)} frames identical")

    say("hashing audio...")
    audio_a, audio_b = audio_hash(a), audio_hash(b)
    if audio_a != audio_b:
        failures.append(f"audio differs\n    A: {audio_a[:16]}\n    B: {audio_b[:16]}")
    else:
        say(f"audio: identical ({audio_a[:16]}…)")

    if failures:
        print("\nNOT DETERMINISTIC")
        for f in failures:
            print(f"  - {f}")
        return 1

    # Reported, not asserted: byte equality is a nice-to-have, and stating it
    # separately keeps the actual claim honest.
    same_bytes = a.read_bytes() == b.read_bytes()
    print("\nDETERMINISTIC")
    print(f"  frames:     {len(hashes_a)} identical")
    print(f"  audio:      identical")
    print(f"  file bytes: {'identical' if same_bytes else 'differ (expected; not the claim)'}")
    return 0


def compare_specs(a: Path, b: Path) -> int:
    """Compare two scene_spec.json files, ignoring provenance.

    Provenance carries the model id and other run metadata; the *content* claim
    is that scenes, props, resolved values and word timings match.
    """
    def load(path: Path) -> dict:
        data = json.loads(path.read_text("utf-8"))
        data.pop("provenance", None)
        return data

    left, right = load(a), load(b)
    if left == right:
        print(f"specs identical (excluding provenance): {a.name} == {b.name}")
        return 0

    print(f"specs DIFFER: {a.name} != {b.name}")
    scenes_a = {s["scene_id"]: s for s in left.get("scenes", [])}
    scenes_b = {s["scene_id"]: s for s in right.get("scenes", [])}
    for scene_id in sorted(set(scenes_a) | set(scenes_b)):
        if scenes_a.get(scene_id) != scenes_b.get(scene_id):
            print(f"  - {scene_id} differs")
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="verify_determinism",
        description="Compare two runs frame by frame and sample by sample (R3).",
    )
    p.add_argument("a", type=Path)
    p.add_argument("b", type=Path)
    p.add_argument("--specs", action="store_true",
                   help="also compare the sibling .spec.json files")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    for path in (args.a, args.b):
        if not path.exists():
            raise SystemExit(f"not found: {path}")

    status = compare(args.a, args.b, quiet=args.quiet)

    if args.specs:
        spec_a, spec_b = args.a.with_suffix(".spec.json"), args.b.with_suffix(".spec.json")
        if spec_a.exists() and spec_b.exists():
            print()
            status |= compare_specs(spec_a, spec_b)
        else:
            print(f"\n(no sibling specs to compare)")
    return status


if __name__ == "__main__":
    sys.exit(main())
