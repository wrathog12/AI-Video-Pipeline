"""Renderer interface + Remotion implementation (R7).

Renders each scene as SILENT video at an exact frame count. Audio never enters
Remotion; it is assembled as one continuous track and muxed once at the end
(see audio_track.py).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .schema import Config, Scene


@runtime_checkable
class Renderer(Protocol):
    name: str

    def render_scene(self, scene: Scene, cfg: Config, *, frames: int,
                     out_path: Path) -> None: ...


class RemotionRenderer:
    name = "remotion"

    def __init__(self, engine_dir: Path) -> None:
        self.engine_dir = Path(engine_dir)
        self.entry = self.engine_dir / "index.ts"
        if not self.entry.exists():
            raise FileNotFoundError(f"Remotion entry point not found: {self.entry}")
        if not (self.engine_dir / "node_modules").exists():
            raise RuntimeError(
                f"Remotion dependencies not installed. Run: npm install --prefix {self.engine_dir}"
            )

    def scene_payload(self, scene: Scene, cfg: Config, *, frames: int) -> dict[str, Any]:
        """The exact props object handed to Remotion.

        Also the thing the render cache hashes, so it must contain every input
        that can change a pixel — theme and dimensions included.
        """
        return {
            "scene_id": scene.scene_id,
            "template_name": scene.template_name,
            "narration_text": scene.narration_text,
            "props": scene.props,
            "word_triggers": [t.model_dump() for t in scene.word_triggers],
            "theme": cfg.theme.model_dump(),
            "orientation": cfg.project.resolved_orientation,
            "output_scale": cfg.project.output_scale,
            "width": cfg.project.width,
            "height": cfg.project.height,
            "fps": cfg.project.fps,
            "duration_in_frames": frames,
        }

    def render_scene(self, scene: Scene, cfg: Config, *, frames: int,
                     out_path: Path) -> None:
        payload = self.scene_payload(scene, cfg, frames=frames)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Props go via a temp file, not argv: a JSON blob on a Windows command
        # line hits quoting rules and the ~8191-character limit.
        tmp = Path(tempfile.mkdtemp(prefix="remotion-props-")) / "props.json"
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        cmd = [
            _npx(), "remotion", "render", "index.ts", "Scene", str(out_path.resolve()),
            f"--props={tmp}",
            f"--scale={cfg.project.output_scale}",
            "--codec=h264",
            # Silent video. Audio is muxed in later from the continuous track.
            "--muted",
            "--log=error",
            "--concurrency=1",
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=self.engine_dir, capture_output=True, text=True, check=False
            )
            if proc.returncode != 0 or not out_path.exists():
                raise RuntimeError(
                    f"Remotion render failed for {scene.scene_id} "
                    f"(exit {proc.returncode})\nSTDOUT:\n{proc.stdout[-2000:]}\n"
                    f"STDERR:\n{proc.stderr[-2000:]}"
                )
        finally:
            shutil.rmtree(tmp.parent, ignore_errors=True)

    def chromium_version(self) -> str:
        """Recorded in provenance; a browser change can alter raster output."""
        try:
            proc = subprocess.run(
                [_npx(), "remotion", "versions"],
                cwd=self.engine_dir, capture_output=True, text=True, check=False, timeout=120,
            )
            for line in (proc.stdout or "").splitlines():
                if "chrome" in line.lower() or "shell" in line.lower():
                    return line.strip()
        except Exception:
            pass
        return "unknown"


def _npx() -> str:
    """Resolve npx.

    On Windows the executable is npx.cmd, and subprocess without shell=True will
    not find a bare "npx".
    """
    for name in ("npx.cmd", "npx"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("npx not found on PATH; Node.js is required to render.")


def get_renderer(cfg: Config, engine_dir: Path) -> Renderer:
    name = cfg.providers.renderer
    if name == "remotion":
        return RemotionRenderer(engine_dir)
    raise ValueError(f"Unknown renderer provider: {name!r} (available: remotion)")
