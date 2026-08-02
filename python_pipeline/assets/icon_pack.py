"""The vendored-icon provider: narration words -> Noto Emoji SVGs.

Answers the complaint that a video narrating "an apple" shows no apple. Keywords
are lifted from the narration by `base.keywords_of`, looked up in the hand-curated
`vendor_icons.CATALOG`, and returned as `AssetRef`s the renderer can draw.

## One icon per scene, by default

`limit=1` is a design choice, not a placeholder. An icon here is a *label on the
content*, not the content — the swatch strip and the comparison bars are the
content, because they encode the values. Two or three emoji beside a value card
stop reading as illustration and start reading as clip-art, which makes the frame
busier and cheaper at the same time. One small glyph anchors the topic; the rest of
the frame stays typographic.

The cap lives here rather than in a template so that a theme wanting more only
changes a constructor argument, and so no template has to know how many icons it
might be handed.

## The match must be to a word that is actually spoken

Each ref carries the `cue_word` it matched, in the narration's own spelling. This
is why `keywords_of` hands back surface forms: the aligner reports the token the
voice emitted, so an icon cued on a normalised key would be cued on a word no
trigger contains — and a missed cue is not fail-safe (`useCueProgress` treats "no
trigger" as "show immediately"), so it would silently un-sync rather than fail.

## Each glyph is used at most once per video

The provider remembers what it has already handed out. Without that, script A gets
a monitor in three of seven scenes and script B a calendar in four — and a glyph
that recurs every other scene stops reading as illustration and starts reading as
a template artifact, which is worse than a plain frame. `base.rank_by_rarity`
thins repetition out but cannot eliminate it, because two scenes' rarest words can
still map to one icon.

When a scene's only match is already spent it gets no icon. Handing back the same
glyph a third time would be the confidently-wrong-match failure again, just spread
across scenes instead of within one.

This makes `resolve` stateful and order-dependent, which is worth naming: the same
scene resolves differently depending on what came before it. That is safe here
because scene order is deterministic — the segmenter is pure Python over the script
— so a run is reproducible, which is what R3 actually requires. It does mean the
provider is per-run, not a shared singleton; `main.get_asset_provider` constructs a
fresh one each time.

## Missing files degrade to no icon, never to a broken image

The catalog is the vocabulary; the files on disk are what exists. If the vendoring
step has not been run, or one glyph failed to download, that keyword resolves to
nothing and the scene renders exactly as it does under the `null` provider. A
missing pack must not be a render error, because R2 says an unseen script still
produces a video — and the same reasoning covers an un-vendored checkout.
"""

from __future__ import annotations

from pathlib import Path

from ..schema import AssetRef
from .base import lookup_forms
from .vendor_icons import CATALOG, icons_dir

# Path prefix the renderer resolves with Remotion's staticFile(). Relative to
# remotion_engine/public/, which is where the vendoring step writes.
STATIC_PREFIX = "icons"


def build_index(catalog: dict[str, tuple[str, tuple[str, ...]]] | None = None) -> dict[str, str]:
    """keyword -> icon id.

    Iterated in sorted order so that a keyword claimed by two icons resolves the
    same way on every machine. A collision is a data bug in the catalog rather
        than a runtime condition, so it is reported by `index_conflicts` (which the
    vendoring step prints) instead of raising here — a typo in an alias list should
    not stop a render.
    """
    entries = CATALOG if catalog is None else catalog
    index: dict[str, str] = {}
    for icon_id, (_codepoint, keywords) in sorted(entries.items()):
        for keyword in keywords:
            index.setdefault(keyword, icon_id)
    return index


def index_conflicts(
    catalog: dict[str, tuple[str, tuple[str, ...]]] | None = None,
) -> dict[str, list[str]]:
    """Keywords claimed by more than one icon. Empty for a healthy catalog."""
    entries = CATALOG if catalog is None else catalog
    claims: dict[str, list[str]] = {}
    for icon_id, (_codepoint, keywords) in sorted(entries.items()):
        for keyword in keywords:
            claims.setdefault(keyword, []).append(icon_id)
    return {k: v for k, v in claims.items() if len(v) > 1}


class IconPackProvider:
    """Looks up vendored SVGs by keyword. Satisfies `base.AssetProvider`."""

    name = "icon_pack"

    def __init__(
        self, *, root: Path | None = None, limit: int = 1, reuse: bool = False
    ) -> None:
        self.root = Path(root) if root is not None else icons_dir()
        self.limit = max(0, limit)
        # False by default: see "Each glyph is used at most once per video". Kept as
        # a flag rather than hardcoded so the once-only rule is inspectable, and so a
        # caller resolving a single scene in isolation can opt out.
        self.reuse = reuse
        self.index = build_index()
        self._spent: set[str] = set()

    def available(self) -> int:
        """How many catalog icons are actually present on disk."""
        return sum(1 for icon_id in CATALOG if (self.root / f"{icon_id}.svg").is_file())

    def reset(self) -> None:
        """Forget what has been handed out. Call between independent videos."""
        self._spent.clear()

    def resolve(self, keywords: list[str]) -> list[AssetRef]:
        refs: list[AssetRef] = []
        taken: set[str] = set()
        for word in keywords:
            if len(refs) >= self.limit:
                break
            icon_id = next(
                (self.index[form] for form in lookup_forms(word) if form in self.index),
                None,
            )
            if icon_id is None or icon_id in taken:
                continue
            if not self.reuse and icon_id in self._spent:
                # Already used by an earlier scene. Keep scanning this scene's other
                # keywords — a second-choice icon that is new beats a first-choice
                # one the viewer has already seen.
                continue
            if not (self.root / f"{icon_id}.svg").is_file():
                # Vendoring not run, or this glyph failed to fetch. Keep looking:
                # a later keyword may have an icon that is present.
                continue
            taken.add(icon_id)
            self._spent.add(icon_id)
            refs.append(
                AssetRef(
                    kind="svg",
                    id=icon_id,
                    path=f"{STATIC_PREFIX}/{icon_id}.svg",
                    # The surface form, so it can match a word trigger (R5).
                    cue_word=word,
                )
            )
        return refs
