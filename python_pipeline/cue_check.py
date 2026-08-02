"""Verify cue-to-word matching and measure sync accuracy (R5).

    python -m python_pipeline.cue_check output/video.spec.json

R5 asks for word-level sync within ±150 ms. That budget decomposes into three
independent error sources, and only one of them is inside this pipeline:

1.  **Matching.** Does each `cue_word` find a word trigger at all? A miss is the
    dangerous case, because `useCueProgress` treats "no trigger" as "show
    immediately" — the element appears at frame 0 and nothing warns you. This
    module's main job is to count those.
2.  **Quantisation.** ms → frame rounding, bounded by half a frame (16.7 ms at
    30 fps). Measured here.
3.  **The provider's own timestamp accuracy.** Not measurable without
    hand-labelled ground truth, so it is not claimed. edge-tts boundaries come
    from Azure's synthesiser and are exact by construction — the engine reports
    when it emitted each word — but a forced aligner (whisperx) would have real
    error here.

The matching rules mirror `findTrigger` in remotion_engine/src/components/
WordCue.tsx. The duplication is deliberate — the renderer cannot import Python —
but it means the two must be changed together, and this module is what catches it
if they drift.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_NON_ALNUM = re.compile(r"[^0-9a-z]")


def normalize_word(text: str) -> str:
    return _NON_ALNUM.sub("", text.lower())


def find_trigger(triggers: list[dict], word: str) -> tuple[dict | None, str]:
    """Mirror of findTrigger in WordCue.tsx. Returns (trigger, how_it_matched)."""
    target = normalize_word(word)
    if not target:
        return None, "empty"

    for t in triggers:
        if normalize_word(t["word"]) == target:
            return t, "exact"
    for t in triggers:
        if normalize_word(t["word"]).startswith(target):
            return t, "prefix"
    for t in triggers:
        if any(normalize_word(part) == target for part in t["word"].split()):
            return t, "sub-token"
    return None, "none"


@dataclass
class CueResult:
    scene_id: str
    path: str
    cue_word: str
    how: str
    start_ms: int | None
    quantisation_ms: float


def _walk_cues(obj: Any, path: str = "") -> list[tuple[str, str]]:
    """Every (path, cue_word) in a props tree."""
    found: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        cue = obj.get("cue_word")
        if isinstance(cue, str) and cue.strip():
            found.append((path or "value", cue.strip()))
        for key, value in obj.items():
            if key == "cue_word":
                continue
            found.extend(_walk_cues(value, f"{path}.{key}" if path else key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            found.extend(_walk_cues(value, f"{path}[{i}]"))
    return found


def check(spec_path: Path, *, lead_ms: int = 80, budget_ms: float = 150.0) -> int:
    spec = json.loads(spec_path.read_text("utf-8"))
    fps = spec.get("provenance", {}).get("fps") or 30
    frame_ms = 1000.0 / fps

    results: list[CueResult] = []
    for scene in spec.get("scenes", []):
        triggers = scene.get("word_triggers", [])
        for path, cue in _walk_cues(scene.get("props", {})):
            trigger, how = find_trigger(triggers, cue)
            if trigger is None:
                results.append(CueResult(scene["scene_id"], path, cue, how, None, 0.0))
                continue
            # The renderer starts the reveal `lead_ms` early, then rounds to a
            # frame. The error is the gap between that frame and the intended time.
            target = max(0, trigger["start_ms"] - lead_ms)
            frame = round(target / 1000 * fps)
            results.append(
                CueResult(
                    scene["scene_id"], path, cue, how,
                    trigger["start_ms"], abs(frame * frame_ms - target),
                )
            )

    if not results:
        print(f"{spec_path.name}: no cue words in the spec (nothing to check)")
        return 0

    matched = [r for r in results if r.start_ms is not None]
    missing = [r for r in results if r.start_ms is None]
    worst = max((r.quantisation_ms for r in matched), default=0.0)

    width = max(len(f"{r.scene_id}.{r.path}") for r in results)
    print(f"{spec_path.name} — {fps} fps, one frame = {frame_ms:.1f} ms\n")
    print(f"{'CUE'.ljust(width)}  WORD          MATCH      START     QUANT")
    for r in results:
        location = f"{r.scene_id}.{r.path}".ljust(width)
        start = f"{r.start_ms} ms" if r.start_ms is not None else "—"
        quant = f"{r.quantisation_ms:.1f} ms" if r.start_ms is not None else "—"
        print(f"{location}  {r.cue_word[:12].ljust(12)}  {r.how.ljust(9)}  {start:>9}  {quant:>8}")

    exact = sum(1 for r in matched if r.how == "exact")
    print(
        f"\n{len(matched)}/{len(results)} cues matched "
        f"({exact} exact, {len(matched) - exact} via fallback)"
    )
    print(f"worst quantisation error: {worst:.1f} ms (budget {budget_ms:.0f} ms)")

    failures = []
    if missing:
        failures.append(
            f"{len(missing)} cue word(s) matched no trigger — these elements ignore the "
            "audio and appear at frame 0: "
            + ", ".join(f"{r.scene_id}.{r.path}={r.cue_word!r}" for r in missing)
        )
    if worst > budget_ms:
        failures.append(f"quantisation error {worst:.1f} ms exceeds the {budget_ms:.0f} ms budget")

    if failures:
        print("\nFAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="cue_check",
        description="Check that every cue_word resolves to a word trigger (R5).",
    )
    p.add_argument("spec", type=Path, nargs="+", help="scene_spec.json path(s)")
    p.add_argument("--lead-ms", type=int, default=80,
                   help="reveal lead used by the renderer (default 80)")
    p.add_argument("--budget-ms", type=float, default=150.0)
    args = p.parse_args(argv)

    status = 0
    for i, spec in enumerate(args.spec):
        if i:
            print()
        if not spec.exists():
            print(f"not found: {spec}")
            status = 1
            continue
        status |= check(spec, lead_ms=args.lead_ms, budget_ms=args.budget_ms)
    return status


if __name__ == "__main__":
    sys.exit(main())
