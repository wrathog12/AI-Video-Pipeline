"""Content-addressed caching and canonical hashing (R3, R8).

Two disciplines here are load-bearing:

*   `canonical_json` sorts keys and uses fixed separators. An unsorted
    `json.dumps` silently produces a different hash for identical data.
*   Audio is hashed as *decoded PCM*, never as WAV file bytes. Container
    headers differ between runs and would bust every entry on every run.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


@dataclass
class CacheReport:
    """Per-item hit/miss record backing `--explain-cache` (the R8 demo)."""

    entries: list[dict[str, str]] = field(default_factory=list)

    def record(self, tier: str, item: str, hit: bool, key: str, reason: str = "") -> None:
        self.entries.append(
            {
                "tier": tier,
                "item": item,
                "status": "HIT" if hit else "MISS",
                "key": key[:12],
                "reason": reason,
            }
        )

    def render(self) -> str:
        if not self.entries:
            return "(no cache activity)"
        w = max(len(e["item"]) for e in self.entries)
        lines = [f"{'TIER':<8} {'ITEM':<{w}} {'STATUS':<6} {'KEY':<12} REASON"]
        for e in self.entries:
            lines.append(
                f"{e['tier']:<8} {e['item']:<{w}} {e['status']:<6} {e['key']:<12} {e['reason']}"
            )
        hits = sum(1 for e in self.entries if e["status"] == "HIT")
        lines.append(f"\n{hits}/{len(self.entries)} cache hits")
        return "\n".join(lines)


class Cache:
    """A tier of the content-addressed store: `<root>/<tier>/<key><suffix>`."""

    def __init__(
        self,
        root: Path,
        tier: str,
        *,
        enabled: bool = True,
        deterministic_content: bool = True,
    ) -> None:
        self.dir = Path(root) / tier
        self.tier = tier
        self.enabled = enabled
        # Whether `key -> content` is a *function*. True for audio, alignment and
        # rendered frames: same inputs, same bytes, so re-writing an entry is
        # idempotent and `--no-cache` can safely write through (it must — the
        # renderer's output path IS the cache path, so a no-op write would leave
        # the muxer with a missing file).
        #
        # False for the annotation tier, where the content comes from an LLM. Two
        # cold calls with the same key legitimately return different annotations,
        # so writing through on `--no-cache` silently *replaces* a good entry with
        # a different one — and the next cached run serves the replacement. That
        # turns a read-only diagnostic flag into a mutation, which is how a
        # determinism check ends up comparing two different videos.
        self.deterministic_content = deterministic_content
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def writes_enabled(self) -> bool:
        return self.enabled or self.deterministic_content

    def path_for(self, key: str, suffix: str) -> Path:
        return self.dir / f"{key}{suffix}"

    def staging_path(self, key: str, suffix: str) -> Path:
        """A scratch path for a caller to write before committing via put_file.

        Process-unique so two concurrent runs cannot stomp each other, and
        distinct from the internal temp name used by put_file.

        The real extension stays last: both soundfile and Remotion infer the
        container format from it, so `<key>.staging.wav`, not `<key>.wav.staging`.
        """
        return self.dir / f"{key}.{os.getpid()}.staging{suffix}"

    def get(self, key: str, suffix: str) -> Path | None:
        if not self.enabled:
            return None
        p = self.path_for(key, suffix)
        # Guard against a zero-byte file left behind by an interrupted write.
        return p if p.exists() and p.stat().st_size > 0 else None

    def put_bytes(self, key: str, suffix: str, data: bytes) -> Path:
        dest = self.path_for(key, suffix)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)  # atomic: a killed run never leaves a truncated entry
        return dest

    def put_file(self, key: str, suffix: str, src: Path, *, move: bool = True) -> Path:
        """Commit an already-written file into the cache.

        Defaults to a move, which is atomic on the same filesystem and avoids
        copying a multi-megabyte render twice.
        """
        dest = self.path_for(key, suffix)
        src = Path(src)
        if src.resolve() == dest.resolve():
            return dest
        if move:
            src.replace(dest)
        else:
            tmp = self.dir / f"{key}{suffix}.{os.getpid()}.copying"
            shutil.copyfile(src, tmp)
            tmp.replace(dest)
        return dest

    def put_json(self, key: str, obj: Any) -> Path:
        if not self.writes_enabled:
            # --no-cache on a non-deterministic tier: return the path this *would*
            # have been written to, without writing. Callers use the return value
            # only for logging.
            return self.path_for(key, ".json")
        return self.put_bytes(key, ".json", canonical_json(obj).encode("utf-8"))

    def get_json(self, key: str) -> Any | None:
        p = self.get(key, ".json")
        return json.loads(p.read_text("utf-8")) if p else None


# --------------------------------------------------------------------------
# Key derivation. Each function names every input that can change the output;
# an omitted input means stale artifacts get served.
# --------------------------------------------------------------------------


def audio_key(*, normalized_text: str, provider: str, voice: str, rate: str,
              sample_rate: int, model: str = "-") -> str:
    """Audio cache key.

    `model` is included because a provider can change voice output without the
    voice id changing — Cartesia's sonic-3 and sonic-3.5 read the same text
    differently, and omitting it would serve last model's audio.
    """
    return sha256_obj(
        {
            "text": normalized_text,
            "provider": provider,
            "voice": voice,
            "model": model,
            "rate": rate,
            "sample_rate": sample_rate,
        }
    )


def align_key(*, audio_pcm_sha256: str, text: str, aligner: str) -> str:
    return sha256_obj({"pcm": audio_pcm_sha256, "text": text, "aligner": aligner})


def render_key(*, template_name: str, props: Any, word_triggers: Any, duration_ms: int,
               theme_sha256: str, engine_sha256: str, fps: int, width: int, height: int,
               output_scale: float, assets: Any = ()) -> str:
    """Render cache key.

    `assets` is a component for the same reason props are: switching
    providers.assets from null to icon_pack changes what is on screen without
    touching a single prop, so omitting it would serve icon-free frames from cache
    and make the new provider look like it does nothing.

    theme/fps/dimensions are mandatory components: without them, changing the
    palette or aspect ratio in config.yaml serves last run's frames — and
    "change the palette and re-run" is the obvious thing a reviewer tries.

    `engine_sha256` fingerprints the renderer's own source (see
    `engine_fingerprint`). Without it, editing a template hands back last run's
    frames: the props are unchanged, so the key is unchanged, so the cache is
    "correct" and the new component never appears. That failure mode is
    especially nasty because it looks like the edit didn't work rather than like
    a stale cache, so it gets debugged in the wrong file.
    """
    return sha256_obj(
        {
            "template": template_name,
            "props": props,
            "assets": list(assets),
            "triggers": word_triggers,
            "duration_ms": duration_ms,
            "theme": theme_sha256,
            "engine": engine_sha256,
            "fps": fps,
            "width": width,
            "height": height,
            "scale": round(output_scale, 6),
        }
    )


def engine_fingerprint(engine_root: Path) -> str:
    """Hash every renderer source file that can affect a rendered frame.

    Deliberately coarse: one digest over all of `src/` plus the Remotion config,
    so *any* template or component edit invalidates *every* scene. A per-scene
    dependency graph (this template imports that component) would re-render less,
    but getting it wrong serves a stale frame — and being wrong in the direction
    of "re-render too much" costs render minutes, while being wrong in the other
    direction costs correctness. Take the render time.

    node_modules is excluded: it is pinned by package-lock.json, which IS
    included, so a dependency change still moves the digest.
    """
    parts: list[str] = []
    for rel in ("src", "remotion.config.ts", "package-lock.json", "package.json"):
        target = engine_root / rel
        if target.is_file():
            parts.append(f"{rel}:{sha256_bytes(target.read_bytes())}")
        elif target.is_dir():
            for path in sorted(target.rglob("*")):
                if path.is_file():
                    key = path.relative_to(engine_root).as_posix()
                    parts.append(f"{key}:{sha256_bytes(path.read_bytes())}")
    if not parts:
        raise FileNotFoundError(f"no renderer sources found under {engine_root}")
    return sha256_obj(parts)


def spec_key(*, script: str, prompt: str, model_id: str, schema_version: int) -> str:
    """Annotation cache key — this is what makes R3 hold across runs."""
    return sha256_obj(
        {
            "script": script,
            "prompt": prompt,
            "model": model_id,
            "schema_version": schema_version,
        }
    )
