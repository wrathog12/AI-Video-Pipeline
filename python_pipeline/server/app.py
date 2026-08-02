"""The dashboard: a thin HTTP shell over the same CLI a reviewer would type.

    python -m python_pipeline.server          # then open http://127.0.0.1:8000
    ./run --serve

## What this is and is not

It is a *window* onto the pipeline: watch the stages advance, change the settings
R7 says must be configurable, download what came out. It is not a second way to
build a video. Every run shells out to `python -m python_pipeline.main` with ordinary
flags, so there is exactly one code path (R1) and the dashboard cannot drift from the
CLI's behaviour — a class of bug that is otherwise guaranteed, because the UI path
gets exercised far less than the command it duplicates.

## Why it binds to 127.0.0.1 and has no auth

`POST /api/run` starts a subprocess and `POST /api/config` writes `config.yaml`, so
this server is remote code execution by design. That is fine for a tool bound to the
loopback interface on a developer's machine and indefensible on a public one, so the
host default is `127.0.0.1` and `--host` prints a warning. There is no login because
a login would imply this is safe to expose, which it is not.

## Why config edits go to a per-run file by default

A run writes the config it used into `.cache/runs/<id>/config.yaml` and points the
subprocess at it with `--config`. Two consequences worth having:

*   The committed `config.yaml` stays the reproducible default. Fiddling with a
    colour picker does not produce a git diff mid-review.
*   "Which config produced this video" is answerable by looking in the run
    directory, next to the video and the spec. Provenance in the spec records the
    theme hash; the file itself is right there.

`POST /api/config` writes `config.yaml` for real, but only when asked explicitly.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..assets.icon_pack import IconPackProvider
from ..env import load_env
from . import settings as settings_mod
from .runs import ROOT, Run, RunRegistry, iter_sse

STATIC = Path(__file__).resolve().parent / "static"
CONFIG_PATH = ROOT / "config.yaml"
SCRIPTS_DIR = ROOT / "scripts"

app = FastAPI(title="AI Video Engine", docs_url="/api/docs", redoc_url=None)
registry = RunRegistry()


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    """One run. Either an existing script by name, or pasted text, or a spec.

    `script_text` exists because the live review hands over a file: pasting it into
    the box is faster and less error-prone than saving it somewhere first, and the
    text is written to `.cache/runs/<id>/script.txt` so the run still has a real
    file on disk to hash into provenance.
    """

    script_name: str | None = None
    script_text: str | None = None
    spec_name: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)
    profile: str | None = None
    dry_run: bool = False
    no_cache: bool = False


class ConfigSaveRequest(BaseModel):
    overrides: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse("/static/index.html")


# ---------------------------------------------------------------------------
# Read-only introspection
# ---------------------------------------------------------------------------


def _load_raw_config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG_PATH.read_text("utf-8")) or {}


@app.get("/api/env")
def env_info() -> dict[str, Any]:
    """What the engine can see, without ever revealing a value.

    Key *names* only — the same discipline as `env.load_env`, which returns names so
    that a log line about credentials cannot become a leak. A dashboard is a log with
    a browser attached.
    """
    names = load_env()
    provider_opts = settings_mod.provider_options()
    return {
        "env_keys_loaded": sorted(names),
        "providers": provider_opts,
        "fonts": settings_mod.font_options(),
        "icons_vendored": IconPackProvider().available(),
        "config_path": str(CONFIG_PATH),
        "scripts": sorted(p.name for p in SCRIPTS_DIR.glob("*.txt")) if SCRIPTS_DIR.is_dir() else [],
        "specs": sorted(p.name for p in (ROOT / "output").glob("*.spec.json"))
        if (ROOT / "output").is_dir()
        else [],
        "profiles": sorted(_load_raw_config().get("profiles", {})),
    }


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    raw = _load_raw_config()
    return {
        "values": settings_mod.flatten(raw),
        "editable": sorted(settings_mod.EDITABLE),
        "raw": raw,
    }


@app.post("/api/config")
def save_config(body: ConfigSaveRequest) -> dict[str, Any]:
    """Write overrides into the committed `config.yaml`. Deliberate, never implicit.

    `yaml.safe_dump` loses the comments, and those comments carry the reasoning for
    every provider choice — so the file is rewritten by *patching the text* of the
    scalar lines it changes, leaving structure and comments intact. Slower to write
    than a dump, but a config.yaml stripped of its rationale would be a real loss.
    """
    patch, errors = settings_mod.coerce_overrides(body.overrides)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    if not patch:
        return {"written": False, "changed": [], "reason": "no changes requested"}

    # newline="" both ways, or Python's universal-newline translation rewrites every
    # line of the file: reading turns CRLF into LF and writing turns LF into CRLF on
    # Windows, so changing one colour produced an 87-line diff.
    with CONFIG_PATH.open("r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    changed: list[str] = []
    for dotted, value in _flat_items(patch):
        text, did = _patch_scalar(text, dotted.split("."), value)
        if did:
            changed.append(f"{dotted} = {value}")
        else:
            errors.append(f"{dotted}: not found as a plain scalar in config.yaml")
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    # Validate before writing: a config.yaml that no longer parses into Config would
    # break the CLI as well as the dashboard, and the dashboard is the thing that
    # broke it. Round-trip through the real loader, not just yaml.safe_load.
    from ..schema import Config

    try:
        Config.model_validate(yaml.safe_load(text) or {})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=422, detail=[f"result would not validate: {exc}"]
        ) from exc

    with CONFIG_PATH.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return {"written": True, "changed": changed}


def _flat_items(nested: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for key, value in nested.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.extend(_flat_items(value, path))
        else:
            out.append((path, value))
    return out


def _patch_scalar(text: str, path: list[str], value: Any) -> tuple[str, bool]:
    """Replace one `key: value` line inside a nested YAML block, comments intact.

    Indentation-based rather than a real YAML round-trip because the only shapes it
    must handle are two levels of plain mapping (`theme.font_family`,
    `providers.tts`), which is all `EDITABLE` contains. Anything else returns False
    and the caller reports it rather than writing something wrong.
    """
    lines = text.splitlines()
    depth = 0
    start = 0
    end = len(lines)
    for part in path[:-1]:
        found = None
        for i in range(start, end):
            stripped = lines[i].lstrip()
            indent = len(lines[i]) - len(stripped)
            if indent == depth and stripped.startswith(f"{part}:"):
                found = i
                break
        if found is None:
            return text, False
        # The block is everything more-indented that follows.
        start = found + 1
        depth += 2
        end = len(lines)
        for j in range(start, len(lines)):
            stripped = lines[j].lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            if len(lines[j]) - len(stripped) < depth:
                end = j
                break

    leaf = path[-1]
    rendered = _render_scalar(value)
    for i in range(start, end):
        stripped = lines[i].lstrip()
        indent = len(lines[i]) - len(stripped)
        if indent == depth and stripped.startswith(f"{leaf}:"):
            _, _, rest = lines[i].partition(":")
            comment = _trailing_comment(rest)
            lines[i] = f"{' ' * depth}{leaf}: {rendered}{comment}"
            newline = "\r\n" if "\r\n" in text else "\n"
            return newline.join(lines) + (newline if text.endswith("\n") else ""), True
    return text, False


def _trailing_comment(rest: str) -> str:
    """The `# …` after a value, or "" — quotes respected.

    `primary_color: "#E63946"` has no comment. Searching for the first `#` found one
    anyway and preserved `#E63946"` as trailing text, so saving a colour appended the
    old colour to the line as garbage. Every value this module writes for `theme.*` is
    a hex colour, so this is the common case, not an edge one.
    """
    quote = ""
    for i, char in enumerate(rest):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (i == 0 or rest[i - 1] in " \t"):
            # YAML requires whitespace before an inline comment, which is what makes
            # the distinction decidable at all.
            return "   " + rest[i:].rstrip()
    return ""


def _render_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    # Quote anything YAML would otherwise reinterpret: a bare #E63946 is a comment,
    # and a bare "null" is not the string "null".
    return json.dumps(text)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@app.post("/api/run")
def start_run(body: RunRequest) -> dict[str, Any]:
    if registry.active() is not None:
        # One run at a time. Both a rendering run and a second run would compete for
        # CPU (Remotion already runs Chromium) and, worse, for the same cache staging
        # paths — and a dashboard that lets you start six renders on a laptop is a
        # footgun disguised as a feature.
        raise HTTPException(status_code=409, detail="a run is already in progress")

    patch, errors = settings_mod.coerce_overrides(body.overrides)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    run_id = registry.new_id()
    run_dir = registry.root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Config for this run only. The committed config.yaml is never touched here.
    merged = settings_mod.deep_merge(_load_raw_config(), patch)
    config_path = run_dir / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(merged, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    args: list[str] = ["--config", str(config_path)]
    label: str
    source_spec: Path | None = None
    if body.spec_name:
        spec = (ROOT / "output" / body.spec_name).resolve()
        if not spec.is_file() or ROOT not in spec.parents:
            raise HTTPException(status_code=400, detail=f"no such spec: {body.spec_name}")
        args += ["--spec", str(spec)]
        source_spec = spec
        label = f"{body.spec_name} (re-render)"
    elif body.script_text and body.script_text.strip():
        script_path = run_dir / "script.txt"
        script_path.write_text(body.script_text, encoding="utf-8")
        args += ["--script", str(script_path)]
        label = "pasted script"
    elif body.script_name:
        script = (SCRIPTS_DIR / body.script_name).resolve()
        if not script.is_file() or SCRIPTS_DIR.resolve() not in script.parents:
            raise HTTPException(status_code=400, detail=f"no such script: {body.script_name}")
        args += ["--script", str(script)]
        label = body.script_name
    else:
        raise HTTPException(status_code=400, detail="give a script, pasted text, or a spec")

    out_path = run_dir / "video.mp4"
    args += ["--out", str(out_path), "--explain-cache", "--explain-values"]
    if body.profile:
        args += ["--profile", body.profile]
    if body.dry_run:
        args.append("--dry-run")
    if body.no_cache:
        args.append("--no-cache")

    run = Run(
        run_id=run_id,
        script_name=label,
        out_path=out_path,
        config_path=config_path,
        args=args,
        settings=settings_mod.flatten(merged),
        source_spec=source_spec,
    )
    registry.add(run)
    run.start()
    return run.summary()


@app.get("/api/runs")
def list_runs() -> dict[str, Any]:
    return {"runs": registry.list(), "active": (a := registry.active()) and a.run_id}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return run.summary()


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str) -> StreamingResponse:
    run = registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return StreamingResponse(
        iter_sse(run),
        media_type="text/event-stream",
        # Buffering is what breaks SSE behind a proxy; being explicit costs nothing.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, Any]:
    run = registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return {"cancelled": run.cancel(), "state": run.state}


@app.get("/api/runs/{run_id}/download/{kind}")
def download(run_id: str, kind: str) -> FileResponse:
    run = registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    path = run.artifact_path(kind)
    if path is None:
        raise HTTPException(status_code=404, detail=f"no {kind} artifact for this run")
    # A stable, descriptive download name: `video.mp4` in a Downloads folder tells
    # you nothing about which run produced it.
    stem = run.script_name.replace(" ", "_").replace("(", "").replace(")", "")
    return FileResponse(
        path,
        filename=f"{run_id}_{stem}_{path.name}",
        media_type="application/octet-stream" if kind == "video" else None,
    )


@app.get("/api/runs/{run_id}/spec")
def run_spec(run_id: str) -> JSONResponse:
    """The IR as JSON, for the timeline to read real per-scene timings from.

    The log gives scene ids and templates as they are decided; only the spec has
    `start_ms`/`duration_ms`, which is what makes the timeline a timeline rather than
    a checklist. Available the moment stage 8 has written it, including after a
    `--dry-run` where no video exists at all.
    """
    run = registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    path = run.out_path.with_suffix(".spec.json")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="spec not written yet")
    return JSONResponse(json.loads(path.read_text("utf-8")))


@app.get("/api/runs/{run_id}/video")
def run_video(run_id: str) -> FileResponse:
    """Inline playback (not a download), so the page can preview the result."""
    run = registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    if not run.out_path.is_file():
        raise HTTPException(status_code=404, detail="no video yet")
    return FileResponse(run.out_path, media_type="video/mp4")


app.mount("/static", StaticFiles(directory=str(STATIC), html=True), name="static")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m python_pipeline.server",
        description="Serve the pipeline dashboard (timeline, settings, downloads).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "localhost"):
        print(
            "WARNING: this server starts subprocesses and can rewrite config.yaml.\n"
            f"WARNING: binding to {args.host} exposes that to the network. There is\n"
            "WARNING: no authentication. Use 127.0.0.1 unless you are certain.",
            flush=True,
        )

    import uvicorn

    print(f"[dashboard] http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0
