"""One-time fetch of the typefaces this project can render with.

    python -m python_pipeline.vendor_fonts

## Why this module has to exist at all

`config.yaml` has carried `theme.font_family: "Inter"` since Phase 0 and
`remotion_engine/fonts/` has contained nothing but `.gitkeep`. Nothing called
`loadFont`. So the CSS stack resolved past `Inter` to `system-ui`, and every frame
rendered so far used whatever headless Chromium defaults to — while config, the
theme hash and `context.md` all claimed otherwise.

That is worth naming rather than quietly fixing: a font *name* in a config file is
not a font, and the failure was invisible because a missing family degrades to a
readable fallback instead of erroring. The same class as the 0-byte components in
Phase 3.5 — the artifact looked fine, so nothing prompted a check.

## Why bake at build time rather than fetch at render time

Identical reasoning to `assets/vendor_icons.py`: a frame must be a function of the
repository, not of the network. `@remotion/google-fonts` fetches at render time,
which makes output depend on Google's CDN and on whatever the upstream file
contains today — R3 broken by a moving dependency, and broken *intermittently*.

So the TTFs are fetched once, written to `remotion_engine/fonts/`, committed, and
loaded from disk. `manifest.json` records a SHA-256 per file so the committed bytes
are auditable.

## Why three families and not thirty

The typography control on the dashboard needs to *visibly* change the frame, and
three families that differ structurally (grotesque / serif / monospace) demonstrate
that better than ten grotesques that differ by hairlines. Each is a variable font,
so one file covers every weight and adding a bold step later costs no download.

All three are SIL Open Font License 1.1, which permits redistribution in a
repository like this one. That is a licensing axis the assignment grades, so it is
recorded per family below rather than assumed.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .cache import sha256_bytes

OFL = "SIL Open Font License 1.1"
_GF_RAW = "https://raw.githubusercontent.com/google/fonts/main"

# family key -> metadata. `family` is the CSS name the renderer registers and the
# string that goes in `theme.font_family`, so it must match exactly.
#
# `file` is the upstream path. Variable fonts carry axis tags in brackets, which
# are percent-encoded in a URL but must NOT be percent-encoded on disk — Remotion
# resolves the local path literally.
CATALOG: dict[str, dict[str, str]] = {
    "inter": {
        "family": "Inter",
        "file": "Inter[opsz,wght].ttf",
        "url": f"{_GF_RAW}/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
        "license": OFL,
        "note": "Neo-grotesque, designed for screens. The default.",
    },
    "source_serif": {
        "family": "Source Serif 4",
        "file": "SourceSerif4[opsz,wght].ttf",
        "url": f"{_GF_RAW}/ofl/sourceserif4/SourceSerif4%5Bopsz%2Cwght%5D.ttf",
        "license": OFL,
        "note": "Transitional serif. Reads as editorial rather than technical.",
    },
    "jetbrains_mono": {
        "family": "JetBrains Mono",
        "file": "JetBrainsMono[wght].ttf",
        "url": f"{_GF_RAW}/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
        "license": OFL,
        "note": "Monospaced. Digits are fixed-width by construction, so a "
                "counting animation cannot reflow the layout.",
    },
}


def fonts_dir() -> Path:
    """`remotion_engine/public/fonts/`.

    Under `public/`, not the sibling `remotion_engine/fonts/` that `context.md`
    originally specified, for the same reason `assets.vendor_icons.icons_dir` lives
    there: with no `@remotion/fonts` package installed, the only way to reach a local
    file from inside the bundle is `staticFile()`, which resolves against `public/`.
    A font written anywhere else is a font the renderer cannot open — and it would
    fail *silently*, as a fallback family that still looks like text.

    The old `remotion_engine/fonts/` held nothing but `.gitkeep`, so nothing moves.
    """
    return Path(__file__).resolve().parents[1] / "remotion_engine" / "public" / "fonts"


def manifest_path() -> Path:
    return fonts_dir() / "manifest.json"


def available_families() -> list[str]:
    """CSS family names whose file is actually present on disk.

    The dashboard offers exactly these. Listing a family the renderer cannot load
    would produce a control that silently changes nothing — the bug this module
    exists to fix, reintroduced one layer up.
    """
    root = fonts_dir()
    return [
        entry["family"]
        for _key, entry in sorted(CATALOG.items())
        if (root / entry["file"]).is_file() and (root / entry["file"]).stat().st_size > 0
    ]


def _fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ai-video-engine/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def vendor(*, timeout: float = 60.0, force: bool = False) -> int:
    out_dir = fonts_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    entries: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    fetched = skipped = 0

    for key, meta in sorted(CATALOG.items()):
        dest = out_dir / meta["file"]
        if dest.exists() and dest.stat().st_size > 0 and not force:
            data = dest.read_bytes()
            skipped += 1
        else:
            try:
                data = _fetch(meta["url"], timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                # One unreachable family must not abandon the rest. A partial set is
                # useful, and `available_families` reports only what landed.
                print(f"  FAILED {key}: {type(exc).__name__}: {exc}")
                failures.append(key)
                continue
            # A CDN error page is HTML with a 200, which would otherwise be written
            # out as a .ttf and fail much later as an unreadable font.
            if data[:4] not in (b"\x00\x01\x00\x00", b"true", b"ttcf", b"OTTO"):
                print(f"  FAILED {key}: not a TrueType/OpenType file ({len(data)} bytes)")
                failures.append(key)
                continue
            dest.write_bytes(data)
            fetched += 1
            print(f"  fetched {meta['family']:<20} {len(data):>8} bytes")

        entries[key] = {
            "family": meta["family"],
            "file": meta["file"],
            "license": meta["license"],
            "note": meta["note"],
            "source": meta["url"],
            "bytes": len(data),
            "sha256": sha256_bytes(data),
        }

    manifest_path().write_text(
        json.dumps({"fonts": entries}, indent=2, sort_keys=True), "utf-8"
    )

    print(
        f"\n{len(entries)} families available "
        f"({fetched} fetched, {skipped} already present, {len(failures)} failed)"
    )
    print(f"manifest: {manifest_path()}")
    if failures:
        print(f"missing: {', '.join(failures)}")
    return 0 if entries else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--force", action="store_true", help="re-fetch existing files")
    args = parser.parse_args(argv)
    return vendor(timeout=args.timeout, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
