"""Run supervision: launch the pipeline, stream its progress, expose its artifacts.

## Why a subprocess and not an in-process call

`main.run()` is a synchronous function that takes minutes and prints to stdout. The
dashboard could import and call it in a thread, and that would be less code. It
would also mean:

*   a crash in a render takes the web server down with it, so the page that is
    supposed to *report* the failure dies alongside it;
*   the pipeline's `print()` output has to be intercepted by rebinding `sys.stdout`,
    which is process-global and therefore breaks as soon as two runs overlap;
*   `--no-cache`, `--profile` and every other flag would need a second, parallel
    invocation path, which is exactly the kind of drift that made the CLI and the
    dashboard disagree in every project that has tried it.

Running `python -m python_pipeline.main` as a child process means **the dashboard is
a client of the same CLI a reviewer types**. There is one entry point (R1), not one
for humans and one for the UI. Progress comes from the stdout it already prints.

## Why progress is parsed from log lines rather than reported by the pipeline

The engine prints `[engine] rendering scene_03 [BigNumber] 240 frames` today. Adding
a structured progress channel — a callback, a socket, a JSON event stream — would
mean editing every stage to report itself, and those edits would exist solely to
serve the UI. Parsing is uglier, but it keeps the pipeline unaware that a dashboard
exists, and an unrecognised line still appears in the log verbatim rather than being
dropped. The parser is best-effort by design: it can fail to *classify* a line, and
the worst outcome is a stage bar that doesn't advance while the raw log still scrolls.

## What a run owns

Each run gets `.cache/runs/<run_id>/` holding the config it actually used, the log,
and its outputs. The config is written there before launch, which is what makes the
dashboard's "run with these settings" honest: the file that produced the video sits
next to the video, and `config.yaml` on disk is never touched by a run.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal

ROOT = Path(__file__).resolve().parents[2]

RunState = Literal["queued", "running", "succeeded", "failed", "cancelled"]

# The pipeline's stages, in the order they appear. `key` matches what the parser
# emits; `label` is what the timeline shows. Kept here rather than in the frontend
# so the stage list has one definition.
STAGES: list[dict[str, str]] = [
    {"key": "segment", "label": "Segment", "detail": "deterministic, no LLM"},
    {"key": "annotate", "label": "Annotate", "detail": "the LLM's only job"},
    {"key": "evaluate", "label": "Evaluate", "detail": "Python computes every value"},
    {"key": "assets", "label": "Assets", "detail": "keyword → vendored icon"},
    {"key": "audio", "label": "Speak + align", "detail": "TTS, then word timings"},
    {"key": "track", "label": "Audio track", "detail": "one frame-aligned PCM"},
    {"key": "render", "label": "Render", "detail": "silent scene MP4s"},
    {"key": "mux", "label": "Mux", "detail": "single ffmpeg pass"},
    {"key": "spec", "label": "Write IR", "detail": "scene_spec.json (R6)"},
]

_STAGE_KEYS = [s["key"] for s in STAGES]


# ---------------------------------------------------------------------------
# Log line -> event
# ---------------------------------------------------------------------------

# One space only, not `\s*`. Indentation is meaningful in the engine's output: a
# top-level line is a stage announcement and a two-space line is a detail belonging to
# it, which is exactly how the asset-picks line is distinguished from anything else.
# Stripping all leading whitespace erased that distinction and made every scene report
# no icon while the log plainly listed six.
_PREFIX = re.compile(r"^\[engine\] ?")

# Ordered: the first pattern that matches wins, so more specific lines come first.
# Each entry is (compiled pattern, stage key, whether the stage is finished by it).
_PATTERNS: list[tuple[re.Pattern[str], str, bool]] = [
    (re.compile(r"^segmented:"), "segment", True),
    (re.compile(r"^annotating\b"), "annotate", False),
    (re.compile(r"^annotations:"), "annotate", True),
    (re.compile(r"^resolved \d+ on-screen values"), "evaluate", True),
    # The `--spec` path re-resolves rather than resolves: same stage, different verb,
    # because an edited `expr` must be recomputed and that is the R4+R6 demo.
    (re.compile(r"^re-resolved \d+ values"), "evaluate", True),
    (re.compile(r"^assets:"), "assets", True),
    (re.compile(r"^synthesising\b|^re-synthesising\b"), "audio", False),
    (re.compile(r"^tts:"), "audio", False),
    (re.compile(r"^audio track:"), "track", True),
    (re.compile(r"^rendering\b"), "render", False),
    (re.compile(r"^muxing"), "mux", False),
    # A dry run's only spec line reads `dry run: wrote …spec.json`, so the generic
    # `^wrote` pattern below never fires and the final stage stayed pending on a run
    # that had in fact finished everything it was asked to do.
    (re.compile(r"^dry run: wrote .*\.spec\.json"), "spec", True),
    (re.compile(r"^wrote .*\.spec\.json"), "spec", True),
    (re.compile(r"^wrote .*\.mp4"), "mux", True),
]

# `templates: scene_01:TitleCard, scene_02:...` — the line that tells the timeline
# how many scenes there are and what each one is, before any of them render.
_TEMPLATES = re.compile(r"^templates:\s*(.+)$")
_ASSET_PICKS = re.compile(r"^\s{2}(scene_\d+:\w+(?:,\s*scene_\d+:\w+)*)\s*$")
_RENDERING = re.compile(r"^rendering (scene_\w+) \[(\w+)\] (\d+) frames")
_SYNTH = re.compile(r"^(?:re-)?synthesising (scene_\w+)")
_SEGMENTED = re.compile(r"^segmented: (\d+) scenes, (\d+) words")
_ELAPSED = re.compile(r"^elapsed ([\d.]+)s")


def classify(line: str) -> dict[str, Any] | None:
    """Best-effort structure for one engine log line. None when unrecognised.

    Returning None is a normal outcome, not an error: the raw line is still shown.
    """
    body = _PREFIX.sub("", line).rstrip()
    if not body:
        return None

    out: dict[str, Any] = {}

    if m := _SEGMENTED.match(body):
        out["scene_count"] = int(m.group(1))
        out["word_count"] = int(m.group(2))
    if m := _TEMPLATES.match(body):
        scenes = []
        for pair in m.group(1).split(","):
            scene_id, _, template = pair.strip().partition(":")
            if scene_id and template:
                scenes.append({"scene_id": scene_id, "template_name": template})
        out["scenes"] = scenes
    if m := _ASSET_PICKS.match(body):
        picks = {}
        for pair in m.group(1).split(","):
            scene_id, _, icon = pair.strip().partition(":")
            if scene_id and icon:
                picks[scene_id] = icon
        if picks:
            out["asset_picks"] = picks
    if m := _RENDERING.match(body):
        out["active_scene"] = m.group(1)
        out["frames"] = int(m.group(3))
    if m := _SYNTH.match(body):
        out["active_scene"] = m.group(1)
    if m := _ELAPSED.match(body):
        out["elapsed_s"] = float(m.group(1))

    for pattern, stage, done in _PATTERNS:
        if pattern.match(body):
            out["stage"] = stage
            out["stage_done"] = done
            break

    # A WARNING or FAILED line must reach the UI as such: the fallback-annotator
    # path logs warnings and still exits 0, so "succeeded" alone would hide it.
    lowered = body.lower()
    if body.startswith("WARNING") or "warning:" in lowered:
        out["level"] = "warn"
    elif body.startswith("FAILED") or "failed:" in lowered:
        out["level"] = "error"

    return out or None


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@dataclass
class Run:
    run_id: str
    script_name: str
    out_path: Path
    config_path: Path
    args: list[str]
    settings: dict[str, Any] = field(default_factory=dict)
    source_spec: Path | None = None

    state: RunState = "queued"
    returncode: int | None = None
    started_at: float | None = None
    ended_at: float | None = None

    # Timeline state, accumulated from the log.
    # key -> pending|active|done|skipped
    stages: dict[str, str] = field(default_factory=dict)
    # key -> why it was skipped. A dash with no explanation reads as a failure.
    stage_notes: dict[str, str] = field(default_factory=dict)
    scenes: list[dict[str, Any]] = field(default_factory=list)
    scene_count: int | None = None
    word_count: int | None = None
    active_scene: str | None = None
    asset_picks: dict[str, str] = field(default_factory=dict)
    elapsed_s: float | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    _proc: subprocess.Popen[str] | None = None
    _lines: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _seq: int = 0

    def __post_init__(self) -> None:
        for key in _STAGE_KEYS:
            self.stages.setdefault(key, "pending")
        # `--dry-run` stops after the spec, so audio through mux never run. Marking
        # them 'skipped' up front matters because stage promotion is ordered: reaching
        # the spec stage would otherwise back-fill all four as 'done' and the page
        # would claim a render that never happened.
        if "--dry-run" in self.args:
            for key in ("audio", "track", "render", "mux"):
                self._skip(key, "--dry-run stops after the spec")
        # `--spec` is the R6 re-render path: the IR already exists, so there is nothing
        # to segment and nothing for the LLM to annotate. Showing those two as 'done'
        # would credit the run with work it deliberately avoided — and avoiding it is
        # the entire point of the flag.
        if "--spec" in self.args:
            self._skip("segment", "the IR was handed in, not derived")
            self._skip("annotate", "no LLM on the --spec path")
            # Asset matching is also script-only: a spec is rendered exactly as
            # written, so its existing icons are used and no new ones are chosen.
            # The ribbon still shows icons, which is why this needs a reason and not
            # just a dash — the two facts look contradictory without one.
            self._skip("assets", "using the icons already in the spec")
            self._seed_from_spec()

    def _skip(self, key: str, why: str) -> None:
        self.stages[key] = "skipped"
        self.stage_notes[key] = why

    def _seed_from_spec(self) -> None:
        """Populate the timeline from the input spec, before anything renders.

        The scene list normally arrives on the `templates:` line, which only the script
        path prints — a `--spec` re-render is silent about its scenes and would show an
        empty timeline for the whole run, which on a 400-second render is most of the
        experience. The spec is on disk and already has ids, templates and real
        timings, so read it instead of asking the engine to announce what the input
        file already says.
        """
        if self.source_spec is None or not self.source_spec.is_file():
            return
        try:
            data = json.loads(self.source_spec.read_text("utf-8"))
        except (OSError, ValueError):
            return  # An unreadable spec is the pipeline's error to report, not ours.
        self.scenes = [
            {
                "scene_id": scene.get("scene_id", ""),
                "template_name": scene.get("template_name", "?"),
                "duration_ms": scene.get("duration_ms"),
            }
            for scene in data.get("scenes", [])
        ]
        self.scene_count = len(self.scenes) or None
        self.asset_picks = {
            scene["scene_id"]: scene["assets"][0]["id"]
            for scene in data.get("scenes", [])
            if scene.get("assets")
        }

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        cmd = [sys.executable, "-m", "python_pipeline.main", *self.args]
        # PYTHONUNBUFFERED so the log streams live rather than arriving in 8 KB
        # blocks at the end — a progress view fed by a buffered pipe shows nothing
        # for four minutes and then everything at once.
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        self.state = "running"
        self.started_at = time.time()
        self._append("engine", f"$ {' '.join(cmd[2:])}", {"level": "meta"})
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except OSError as exc:
            self.state = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
            self.ended_at = time.time()
            self._append("engine", f"FAILED to launch: {self.error}", {"level": "error"})
            return
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        log_file = self.config_path.parent / "run.log"
        with log_file.open("w", encoding="utf-8") as handle:
            for raw in self._proc.stdout:
                line = raw.rstrip("\n")
                handle.write(line + "\n")
                handle.flush()
                self._absorb(line)
        self._proc.wait()
        self.returncode = self._proc.returncode
        self.ended_at = time.time()
        if self.state == "cancelled":
            pass
        elif self.returncode == 0:
            self.state = "succeeded"
            # Any stage still 'active' at a clean exit did finish; the log just had
            # no closing line for it (mux prints one line for two stages).
            for key, value in self.stages.items():
                if value == "active":
                    self.stages[key] = "done"
        else:
            self.state = "failed"
            self.error = self.error or f"pipeline exited {self.returncode}"
        self._append("engine", f"— process exited {self.returncode} —", {"level": "meta"})

    def cancel(self) -> bool:
        if self._proc is None or self._proc.poll() is not None:
            return False
        self.state = "cancelled"
        self._proc.terminate()
        return True

    # -- log ingestion -----------------------------------------------------

    def _absorb(self, line: str) -> None:
        info = classify(line) or {}
        if stage := info.get("stage"):
            # Stages are strictly ordered, so reaching stage N implies every earlier
            # stage finished. Without this, a stage whose only log line is its
            # completion (evaluate, track) would never leave 'pending'.
            idx = _STAGE_KEYS.index(stage)
            for earlier in _STAGE_KEYS[:idx]:
                if self.stages[earlier] in ("pending", "active"):
                    self.stages[earlier] = "done"
            self.stages[stage] = "done" if info.get("stage_done") else "active"
        if "scene_count" in info:
            self.scene_count = info["scene_count"]
        if "word_count" in info:
            self.word_count = info["word_count"]
        if scenes := info.get("scenes"):
            self.scenes = scenes
        if picks := info.get("asset_picks"):
            self.asset_picks.update(picks)
        if active := info.get("active_scene"):
            self.active_scene = active
        if "elapsed_s" in info:
            self.elapsed_s = info["elapsed_s"]
        if info.get("level") == "warn":
            self.warnings.append(_PREFIX.sub("", line).strip())
        if info.get("level") == "error":
            self.error = _PREFIX.sub("", line).strip()
        self._append("engine", line, info)

    def _append(self, source: str, text: str, info: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            self._lines.append(
                {
                    "seq": self._seq,
                    "t": round(time.time() - (self.started_at or time.time()), 2),
                    "source": source,
                    "text": text,
                    "level": info.get("level", "info"),
                    "stage": info.get("stage"),
                }
            )

    def lines_since(self, seq: int) -> list[dict[str, Any]]:
        with self._lock:
            return [entry for entry in self._lines if entry["seq"] > seq]

    # -- outputs -----------------------------------------------------------

    def artifacts(self) -> list[dict[str, Any]]:
        """Downloadable files this run produced, newest facts first.

        Existence is checked at request time rather than recorded at write time:
        the pipeline may fail after the spec but before the video, and a download
        button for a file that isn't there is worse than no button.
        """
        candidates = [
            (self.out_path, "video", "The rendered MP4"),
            (self.out_path.with_suffix(".spec.json"), "spec",
             "The inspectable IR (R6) — edit it and re-render"),
            (self.config_path, "config", "The exact config this run used"),
            (self.config_path.parent / "run.log", "log", "Full engine log"),
        ]
        out = []
        for path, kind, description in candidates:
            if path.is_file() and path.stat().st_size > 0:
                out.append(
                    {
                        "kind": kind,
                        "name": path.name,
                        "bytes": path.stat().st_size,
                        "description": description,
                    }
                )
        return out

    def artifact_path(self, kind: str) -> Path | None:
        for path, k, _ in [
            (self.out_path, "video", ""),
            (self.out_path.with_suffix(".spec.json"), "spec", ""),
            (self.config_path, "config", ""),
            (self.config_path.parent / "run.log", "log", ""),
        ]:
            if k == kind and path.is_file():
                return path
        return None

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "script_name": self.script_name,
            "state": self.state,
            "returncode": self.returncode,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": round((self.ended_at or time.time()) - self.started_at, 1)
            if self.started_at
            else None,
            "stages": [
                {
                    **stage,
                    "status": self.stages.get(stage["key"], "pending"),
                    # The skip reason replaces the stage's usual blurb: "keyword →
                    # vendored icon" is misleading on a run that did no matching.
                    "detail": self.stage_notes.get(stage["key"]) or stage["detail"],
                }
                for stage in STAGES
            ],
            "scene_count": self.scene_count,
            "word_count": self.word_count,
            "scenes": [
                {**scene, "icon": self.asset_picks.get(scene["scene_id"])}
                for scene in self.scenes
            ],
            "active_scene": self.active_scene,
            "elapsed_s": self.elapsed_s,
            "warnings": self.warnings,
            "error": self.error,
            "settings": self.settings,
            "artifacts": self.artifacts(),
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class RunRegistry:
    """In-memory run index.

    Deliberately not persisted. A dashboard restart losing its run *history* is
    acceptable; the artifacts themselves live on disk under `.cache/runs/` and are
    still there. Persisting would mean a schema, a migration and a stale-PID
    problem, for a feature nobody asked for.
    """

    def __init__(self, root: Path | None = None, keep: int = 40) -> None:
        self.root = Path(root) if root else ROOT / ".cache" / "runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.keep = keep
        self._runs: dict[str, Run] = {}
        self._order: deque[str] = deque()
        self._lock = threading.Lock()
        self._counter = 0

    def new_id(self) -> str:
        # Monotonic and human-sortable, with no wall-clock in the identifier. A
        # timestamp id would be friendlier to read but would make two runs of the
        # same script non-comparable by name, which is the R3 demo.
        with self._lock:
            self._counter += 1
            return f"run_{self._counter:04d}"

    def add(self, run: Run) -> None:
        with self._lock:
            self._runs[run.run_id] = run
            self._order.append(run.run_id)
            while len(self._order) > self.keep:
                self._runs.pop(self._order.popleft(), None)

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(self._order)
        return [self._runs[i].summary() for i in reversed(ids) if i in self._runs]

    def active(self) -> Run | None:
        for run in self._runs.values():
            if run.state == "running":
                return run
        return None


def iter_sse(run: Run, *, poll: float = 0.25) -> Iterator[str]:
    """Server-sent events for one run: every log line, then a final summary.

    Polling a list rather than using a queue per subscriber, because two viewers of
    the same run is a real case (a second browser tab) and a queue would deliver
    each line to only one of them.
    """
    seq = 0
    yield f"event: summary\ndata: {json.dumps(run.summary())}\n\n"
    while True:
        batch = run.lines_since(seq)
        if batch:
            seq = batch[-1]["seq"]
            for entry in batch:
                yield f"event: line\ndata: {json.dumps(entry)}\n\n"
            yield f"event: summary\ndata: {json.dumps(run.summary())}\n\n"
        elif run.state in ("succeeded", "failed", "cancelled"):
            # One last summary after the process exits, so a client that connected
            # late still gets terminal state and the artifact list.
            yield f"event: summary\ndata: {json.dumps(run.summary())}\n\n"
            yield "event: done\ndata: {}\n\n"
            return
        else:
            # Comment frame doubles as a keepalive: some proxies close an idle SSE
            # stream, and annotation can be silent for 30+ seconds.
            yield ": keepalive\n\n"
        time.sleep(poll)
