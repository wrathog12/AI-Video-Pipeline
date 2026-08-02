"""One-time fetch of the Noto Emoji SVGs this project can use.

    python -m python_pipeline.assets.vendor_icons

## Why a build-time bake rather than a runtime download

The rendered frame must be a function of the repository, not of the network. A
render-time fetch would make the output depend on GitHub's availability and on
whatever the upstream file happens to contain today — R3 broken by a moving
dependency, and broken *intermittently*, which is the worst way to break.

So this runs once, writes SVGs into `assets/icons/`, and records a manifest with a
SHA-256 per file. After that the renderer reads local files, exactly like it reads
a bundled font. The manifest is what makes the vendored set auditable: a reviewer
can verify the bytes on disk are the bytes that were fetched.

This is the same reasoning that rules out a diffusion model for these assets, one
step further along: even baked offline, a generated icon has no upstream anyone can
check it against. Noto Emoji is Apache-2.0, ~2-8 KB per glyph, and identical for
everyone who runs this.

## Why a curated alias list rather than the full 3,600

Two reasons, and the second is the real one:

*   Size. The full set is several megabytes of SVG for a project that will use a
    dozen.
*   Precision. Emoji shortnames are semantically noisy. `:chart_increasing:` is
    genuinely about growth; `:apple:` is about fruit, but `:money_with_wings:` is
    about *losing* money and would be an actively wrong illustration for compound
    interest. Choosing the mapping by hand is the difference between illustration
    and decoration, and it is not work an automatic import can do.

Keys here are the *narration words* a script actually uses, so this list reads as
"vocabulary this engine can illustrate" rather than "emoji that exist".
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from ..cache import sha256_bytes

RAW_BASE = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/svg"
LICENSE_NOTE = (
    "Noto Emoji, Apache License 2.0. "
    "https://github.com/googlefonts/noto-emoji/blob/main/LICENSE"
)

# icon id -> (codepoint filename stem, narration keywords that select it)
#
# Three rules govern this table, and all three are about refusing coverage:
#
# *   Singulars only. `base.lookup_forms` tries the trivial `-s` strip, so "trees"
#     reaches "tree" on its own. Listing both forms adds nothing and invites the
#     cross-icon keyword collision that `vendor()` warns about.
# *   No word with a dominant non-literal sense. "power" is excluded from the
#     battery because "two to the power of eight" is the likeliest phrase in an
#     explainer script; "right" is excluded from the tick because it is usually
#     "right?" or "the right-hand side"; "note", "drop", "fall" and "space" go the
#     same way. The tempting mapping is the one that misfires.
# *   Where two icons could claim a word, the commoner reading wins outright.
#     "growth" is a rising chart, not a seedling, so the seedling keeps only the
#     words that are literally about planting.
CATALOG: dict[str, tuple[str, tuple[str, ...]]] = {
    # --- concrete objects, the ones that answer "I don't see an apple" ---
    "apple":        ("1f34e", ("apple", "fruit")),
    "artist_palette": ("1f3a8", ("palette", "paint", "painting", "artist")),
    "rainbow":      ("1f308", ("rainbow", "spectrum")),
    "light_bulb":   ("1f4a1", ("bulb", "lightbulb", "lamp")),
    "camera":       ("1f4f7", ("camera", "photo", "photograph")),
    "eye":          ("1f441", ("eye", "vision", "eyesight", "retina")),
    "desktop":      ("1f5a5", ("computer", "monitor", "screen", "desktop")),
    "mobile_phone": ("1f4f1", ("phone", "smartphone")),
    "television":   ("1f4fa", ("television",)),
    "printer":      ("1f5a8", ("printer", "printing")),
    "magnifier":    ("1f50d", ("magnify", "magnifier", "magnified", "zoom")),
    "microscope":   ("1f52c", ("microscope", "microscopic")),
    "clock":        ("1f552", ("clock", "hour", "minute", "oclock")),
    "calendar":     ("1f4c5", ("calendar", "year", "decade", "century")),
    "hourglass":    ("23f3", ("hourglass", "patience", "waiting")),
    "seedling":     ("1f331", ("seed", "seedling", "sprout", "planting")),
    "deciduous_tree": ("1f333", ("tree", "forest")),
    "snowflake":    ("2744", ("snow", "snowflake", "freeze", "frozen")),
    "fire":         ("1f525", ("fire", "flame", "burning", "heat")),
    "droplet":      ("1f4a7", ("water", "droplet", "liquid")),
    "sun":          ("2600", ("sun", "sunlight", "solar", "daylight")),
    "star":         ("2b50", ("star", "starlight")),
    "brain":        ("1f9e0", ("brain", "mind", "memory", "neuron")),
    "gear":         ("2699", ("gear", "mechanism", "machinery", "machine")),
    "battery":      ("1f50b", ("battery", "recharge")),
    "key":          ("1f511", ("key", "unlock")),
    "lock":         ("1f512", ("lock", "locked", "encrypted", "security")),
    "package":      ("1f4e6", ("package", "parcel", "container")),
    "books":        ("1f4da", ("book", "library", "encyclopedia")),
    "pencil":       ("270f", ("pencil", "writing", "drawing", "sketch")),
    "ruler":        ("1f4cf", ("ruler", "measure", "measuring", "measurement")),
    "balance_scale": ("2696", ("scale", "balance", "weigh", "tradeoff")),
    "puzzle_piece": ("1f9e9", ("puzzle", "jigsaw")),
    "telescope":    ("1f52d", ("telescope", "astronomy")),
    "rocket":       ("1f680", ("rocket", "launch", "spacecraft")),
    "globe":        ("1f30d", ("earth", "world", "globe", "planet")),
    "brick":        ("1f9f1", ("brick", "block", "building")),
    "abacus":       ("1f9ee", ("abacus", "count", "counting", "arithmetic")),

    # --- money and quantity, for scripts like B ---
    "money_bag":    ("1f4b0", ("money", "cash", "wealth", "saving", "fund")),
    "dollar_banknote": ("1f4b5", ("dollar", "banknote", "bill")),
    "coin":         ("1fa99", ("coin", "cent", "penny", "principal")),
    "bank":         ("1f3e6", ("bank", "banking", "deposit", "account")),
    "credit_card":  ("1f4b3", ("credit", "debit")),
    "chart_increasing": ("1f4c8", ("growth", "grow", "growing", "increase",
                                   "rising", "compound", "interest", "profit")),
    "chart_decreasing": ("1f4c9", ("decrease", "decline", "declining", "shrink")),
    "bar_chart":    ("1f4ca", ("chart", "graph", "statistic", "comparison")),

    # --- abstract, and only where the word cannot mean anything else ---
    "check_mark":   ("2705", ("correct", "valid", "verified")),
    "cross_mark":   ("274c", ("incorrect", "invalid", "mistake")),
    "warning":      ("26a0", ("warning", "caution", "danger")),
    "question":     ("2753", ("question", "puzzling")),
    "sparkles":     ("2728", ("magic", "magical", "sparkle")),
    "repeat":       ("1f501", ("repeat", "loop", "iterate")),
    "chequered_flag": ("1f3c1", ("finish", "conclusion")),
}


def icons_dir() -> Path:
    """Where vendored SVGs live: `remotion_engine/public/icons/`.

    Deliberately inside the renderer's static directory rather than beside this
    module. Remotion serves `public/` and resolves `staticFile("icons/apple.svg")`
    against it, so writing here means the provider's existence check and the
    renderer's fetch look at the same bytes. A copy step between two directories
    would be one more place for the two to disagree — and they would disagree
    silently, as a scene whose `AssetRef` resolves but whose image 404s.
    """
    return Path(__file__).resolve().parents[2] / "remotion_engine" / "public" / "icons"


def manifest_path() -> Path:
    return icons_dir() / "manifest.json"


def _fetch(stem: str, timeout: float) -> bytes:
    url = f"{RAW_BASE}/emoji_u{stem}.svg"
    request = urllib.request.Request(url, headers={"User-Agent": "ai-video-engine/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def vendor(*, timeout: float = 30.0, force: bool = False) -> int:
    """Fetch every catalog icon that is not already on disk. Returns exit status."""
    out_dir = icons_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    entries: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    fetched = skipped = 0

    for icon_id, (stem, keywords) in sorted(CATALOG.items()):
        dest = out_dir / f"{icon_id}.svg"
        if dest.exists() and dest.stat().st_size > 0 and not force:
            data = dest.read_bytes()
            skipped += 1
        else:
            try:
                data = _fetch(stem, timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                # One unreachable icon must not abandon the rest: a partial pack is
                # useful, and the provider treats a missing file as "no icon".
                print(f"  FAILED {icon_id} (emoji_u{stem}): {type(exc).__name__}: {exc}")
                failures.append(icon_id)
                continue
            if not data.lstrip().startswith(b"<?xml") and b"<svg" not in data[:400]:
                print(f"  FAILED {icon_id}: response is not SVG ({len(data)} bytes)")
                failures.append(icon_id)
                continue
            dest.write_bytes(data)
            fetched += 1
            print(f"  fetched {icon_id:<20} {len(data):>6} bytes")

        entries[icon_id] = {
            "codepoint": stem,
            "keywords": list(keywords),
            "file": dest.name,
            "bytes": len(data),
            "sha256": sha256_bytes(data),
        }

    manifest = {
        "source": RAW_BASE,
        "license": LICENSE_NOTE,
        "icons": entries,
    }
    manifest_path().write_text(json.dumps(manifest, indent=2, sort_keys=True), "utf-8")

    print(
        f"\n{len(entries)} icons available "
        f"({fetched} fetched, {skipped} already present, {len(failures)} failed)"
    )
    print(f"manifest: {manifest_path()}")
    if failures:
        print(f"missing: {', '.join(failures)}")

    # A keyword claimed by two icons is a data bug in this file, and the only place
    # it can be noticed is here — at runtime the index just resolves it one way
    # (sorted order, so at least consistently) and nothing looks wrong.
    from .icon_pack import index_conflicts

    for keyword, owners in sorted(index_conflicts().items()):
        print(f"  WARNING keyword {keyword!r} claimed by {', '.join(owners)}")

    return 0 if entries else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--force", action="store_true", help="re-fetch existing files")
    args = parser.parse_args(argv)
    return vendor(timeout=args.timeout, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
