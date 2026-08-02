"""Final assembly: concatenate silent scene videos, mux the one audio track.

Video is stream-copied (`-c copy`), so scene renders are never re-encoded.
Audio is encoded exactly once, from the single continuous track, which is what
keeps cumulative A/V drift at zero.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def ffmpeg() -> str:
    import shutil

    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH.")
    return exe


def write_concat_list(scene_files: list[Path], list_path: Path) -> None:
    """Write the concat demuxer list.

    Paths are absolute with forward slashes and single quotes escaped. `-safe 0`
    plus a Windows backslash path is a known ffmpeg failure, so normalise here
    rather than trusting the caller.
    """
    lines = []
    for p in scene_files:
        resolved = str(Path(p).resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{resolved}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def concat_and_mux(
    scene_files: list[Path],
    audio_wav: Path,
    out_path: Path,
    *,
    audio_bitrate: str = "128k",
) -> None:
    if not scene_files:
        raise ValueError("concat_and_mux received no scene files")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = out_path.parent / f".{out_path.stem}.concat.txt"
    write_concat_list(scene_files, list_path)

    cmd = [
        ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-i", str(Path(audio_wav).resolve()),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", audio_bitrate,
        # No -shortest: the track is built to exactly the video length by
        # construction, and -shortest would silently mask a mismatch that
        # signals a bug in audio_track.py.
        "-movflags", "+faststart",
        str(out_path.resolve()),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(
            f"ffmpeg mux failed (exit {proc.returncode}):\n{proc.stderr[-3000:]}"
        )
    list_path.unlink(missing_ok=True)


def probe(path: Path) -> dict[str, str]:
    """Read back duration / stream info for the run summary and sync checks."""
    import json
    import shutil

    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    proc = subprocess.run(
        [exe, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(Path(path).resolve())],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return {}
    data = json.loads(proc.stdout)
    out: dict[str, str] = {"duration": data.get("format", {}).get("duration", "?")}
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            out["video"] = f"{s.get('codec_name')} {s.get('width')}x{s.get('height')} @ {s.get('r_frame_rate')}"
            out["frames"] = str(s.get("nb_frames", "?"))
        elif s.get("codec_type") == "audio":
            out["audio"] = f"{s.get('codec_name')} {s.get('sample_rate')}Hz ch{s.get('channels')}"
            out["audio_duration"] = str(s.get("duration", "?"))
    return out
