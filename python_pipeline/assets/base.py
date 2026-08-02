"""The AssetProvider seam (R7), plus the keyword extraction both providers share.

R7 names "visual asset generation" as a swappable component, so this is an
interface with two implementations rather than a function: `null` (returns
nothing) and `icon_pack` (looks up vendored SVGs by keyword).

## Why keywords are extracted here, in Python, and not by the LLM

Asking the annotator for keywords would be the obvious move and it is the wrong
one, for three reasons:

1.  It changes the prompt, which bumps `PROMPT_VERSION`, which invalidates every
    cached annotation on disk. Adding decoration should not cost the annotation
    cache.
2.  Icon choice would become model-dependent, so two runs against different model
    versions would produce different frames. That is R3's problem, imported into
    the visual layer for no benefit.
3.  It is not necessary. The narration already contains the nouns. Matching them
    against a fixed index is deterministic, instant, and free.

## Why matching is deliberately conservative

A wrong icon is worse than no icon — the same argument as guessing a colour from
two arbitrary numbers. An apple beside a segment about apples reads as
illustration; an apple beside a segment about compound interest reads as a bug,
and it undermines the numbers next to it. So a keyword must match an alias
*exactly* after normalisation. No stemming, no edit distance, no embeddings: each
of those buys coverage by trading away the property that a match means something.

`null` remains the honest fallback, and templates are designed to look complete
without assets.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Protocol

from ..schema import AssetRef

# Words that are never worth illustrating. Function words plus the vocabulary of
# explanation itself ("value", "number", "example"), which appears in every script
# about anything and would therefore match the same generic icon in every scene.
STOPWORDS: frozenset[str] = frozenset(
    """
    a an the this that these those it its and or but so if then than as at by for
    from in into of on to with without about over under is are was were be been
    being am do does did done have has had having will would can could should may
    might must not no nor only just very much many more most less least same
    other another each every both few all any some such how what when where
    which who whom why you your yours we our ours they them their he she his her
    i me my mine one two three first second next last thing things way ways
    value values number numbers example examples kind sort type types part parts
    time times case cases point points fact facts lot lots bit bits piece pieces
    something anything everything nothing someone anyone everyone
    """.split()
)

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")


def normalize_keyword(word: str) -> str:
    """Lowercase, strip apostrophes and hyphens. The one place this is decided."""
    return word.lower().replace("'", "").replace("-", "")


def keywords_of(text: str, *, limit: int = 24) -> list[str]:
    """Candidate illustration keywords from narration, in order of appearance.

    Order matters, which is why this returns a list and not a set: the first
    concrete noun in a segment is usually its subject, so a provider that can only
    place one icon should place that one. Deduplicated on the normalised form,
    stopworded, and capped so a long segment cannot make lookup cost grow without
    bound.

    Returns the words *as the narration spells them* — "Apples", not "apple".
    Normalisation happens inside a provider's index lookup instead, because the
    surface form is the only thing that can be matched against a word trigger: the
    aligner reports the token the voice actually said, so an icon cued on a
    normalised keyword would be cued on a word that is never spoken. Handing back
    surface tokens lets a provider set `cue_word` to something that can match.
    """
    out: list[str] = []
    seen: set[str] = set()
    for match in _WORD.finditer(text):
        surface = match.group(0)
        word = normalize_keyword(surface)
        if len(word) < 3 or word in STOPWORDS or word in seen:
            continue
        seen.add(word)
        out.append(surface)
        if len(out) >= limit:
            break
    return out


def rank_by_rarity(keywords: list[str], corpus: str) -> list[str]:
    """Re-order a scene's keywords so the rarest word in the whole script comes first.

    This is the same argument as rarest-token-wins in `annotate.repair_cue`, applied
    to a different problem. A word the script says *once* is what this particular
    scene is about; a word it repeats throughout is background vocabulary. Script A
    says "computer" four times and "apple" once, so first-position ordering picks a
    monitor for the opening scene and the apple is never drawn — which was the
    original complaint. Rarity ordering picks the apple.

    It also thins out repetition on its own. Once "computer" ranks last in every
    scene that mentions it, the same glyph stops being the answer for three scenes
    running, without any rule about repetition.

    Ties keep their original relative order, so the result is deterministic and, for
    a scene of entirely unique words, identical to what came in.
    """
    counts = Counter(normalize_keyword(w) for w in _WORD.findall(corpus))
    return sorted(keywords, key=lambda w: counts.get(normalize_keyword(w), 1))


def lookup_forms(word: str) -> tuple[str, ...]:
    """The normalised forms a provider should try for one narration word.

    Exactly two: the word itself, and the trivial `-s` plural stripped. That is the
    whole of the inflection handling, deliberately — a real stemmer maps
    "interesting" onto "interest" and "compression" onto "compress", producing a
    confident icon for a word whose meaning it got wrong. Two forms is the most
    that can be done without guessing.
    """
    key = normalize_keyword(word)
    if len(key) > 3 and key.endswith("s") and not key.endswith("ss"):
        return (key, key[:-1])
    return (key,)


class AssetProvider(Protocol):
    """R7 seam. Both implementations satisfy this and are interchangeable."""

    name: str

    def resolve(self, keywords: list[str]) -> list[AssetRef]:
        """Assets for these keywords, best first. May return []."""
        ...
