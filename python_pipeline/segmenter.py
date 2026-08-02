"""Deterministic scene segmentation — no LLM (R2, R3, R4).

The LLM does not decide where scenes begin or end, for two reasons:

*   **Text drift.** A model asked to segment will occasionally reword, drop a
    clause, normalise a quote, or merge sentences. The TTS then speaks words the
    author never wrote, and no downstream stage can detect it.
*   **Reproducibility.** Scene count would become model-dependent, so every
    cache key downstream would shift between runs and R3 could not hold.

So segmentation is pure Python: normalise, split into sentences, group by
estimated speaking duration. The LLM's only job (llm_annotator.py) is to label
segments it is handed.

The `assert_fidelity` gate at the bottom is the load-bearing part: it proves the
concatenated narration still equals the input script, so any drift introduced
anywhere upstream fails the run loudly instead of shipping.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Abbreviations that end in a period but do not end a sentence. Kept small and
# explicit rather than using an ML sentence splitter, which would be another
# non-deterministic dependency (and would need a model download).
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "etc",
    "e.g", "i.e", "eg", "ie", "approx", "no", "fig", "al", "inc", "ltd", "co",
    "dept", "est", "min", "max", "avg", "ca", "cf", "ed", "vol", "pp",
}

# A sentence ends at .?!… possibly followed by closing quotes/brackets, then
# whitespace, then something that can start a sentence.
_SENTENCE_END = re.compile(
    r"""
    (?<=[.!?…])                 # a terminator
    (?P<close>["'”’)\]]*)       # optional closing punctuation
    \s+                         # required whitespace
    (?=["'“‘(\[]?[A-Z0-9])      # next thing looks like a sentence start
    """,
    re.VERBOSE,
)

_WORD = re.compile(r"[^\s]+")


@dataclass
class Segment:
    """One prospective scene: text plus its duration estimate."""

    index: int
    text: str
    word_count: int
    estimated_seconds: float


def normalize_script(raw: str) -> str:
    """Canonicalise the input script.

    NFC so visually identical text hashes identically; CRLF folded; tabs and
    runs of spaces collapsed; trailing whitespace stripped per line. Paragraph
    breaks are preserved because they are a structural signal.

    This is the ONLY text transformation applied. It is deliberately not a
    "cleanup": curly quotes, unicode symbols and unusual spellings all survive,
    because the narration must remain what the author wrote.
    """
    text = unicodedata.normalize("NFC", raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ")          # nbsp -> space
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)      # cap blank runs at one
    return text.strip()


def split_title(script: str) -> tuple[str | None, str]:
    """Peel off a leading title line.

    A first line with no terminal punctuation, followed by a blank line, is a
    title (Script A has one). Titles are not narrated — sending one to TTS makes
    the voice read a heading aloud.
    """
    parts = script.split("\n\n", 1)
    if len(parts) == 2:
        head, rest = parts[0].strip(), parts[1].strip()
        one_line = "\n" not in head
        unterminated = not head.endswith((".", "!", "?", "…", ":"))
        if one_line and unterminated and len(head.split()) <= 14 and rest:
            return head, rest
    return None, script


def split_sentences(text: str) -> list[str]:
    """Split into sentences, respecting abbreviations."""
    # Paragraph boundaries are always sentence boundaries.
    out: list[str] = []
    for para in [p for p in text.split("\n\n") if p.strip()]:
        flat = para.replace("\n", " ").strip()
        pieces = _protected_split(flat)
        out.extend(p for p in (s.strip() for s in pieces) if p)
    return out


def _protected_split(text: str) -> list[str]:
    pieces: list[str] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        # The token immediately before the terminator; skip if abbreviation.
        head = text[start:m.start()]
        last = re.split(r"[\s(\[]", head.rstrip())[-1] if head.strip() else ""
        stripped = last.rstrip(".!?…\"')]")
        if stripped.lower() in _ABBREVIATIONS:
            continue
        # A single capital letter before the period is an initial ("J. R. Tolkien").
        # Case-sensitive, so this must test the unlowered token.
        if re.fullmatch(r"[A-Z]", stripped):
            continue
        end = m.start() + len(m.group("close"))
        pieces.append(text[start:end])
        start = m.end()
    pieces.append(text[start:])
    return pieces


def count_words(text: str) -> int:
    return len(_WORD.findall(text))


def estimate_seconds(text: str, words_per_minute: float) -> float:
    """Estimate speaking time.

    Only used for *grouping* decisions. Real durations come from the synthesised
    audio, so an imperfect estimate costs a slightly uneven scene length, never
    a sync error.
    """
    if words_per_minute <= 0:
        raise ValueError("words_per_minute must be positive")
    return count_words(text) / (words_per_minute / 60.0)


def segment_script(
    script: str,
    *,
    target_seconds: float = 12.0,
    min_seconds: float = 4.0,
    max_seconds: float = 20.0,
    words_per_minute: float = 165.0,
) -> list[Segment]:
    """Group sentences into segments of roughly `target_seconds`.

    Greedy and fully deterministic: the same script always yields the same
    segments. A sentence longer than `max_seconds` becomes its own segment
    rather than being split mid-thought — a scene running long is better than
    narration cut in half.
    """
    sentences = split_sentences(script)
    if not sentences:
        raise ValueError("Script contains no sentences after normalisation.")

    groups: list[list[str]] = []
    current: list[str] = []
    current_seconds = 0.0

    for sentence in sentences:
        secs = estimate_seconds(sentence, words_per_minute)

        if current and current_seconds + secs > max_seconds:
            groups.append(current)
            current, current_seconds = [sentence], secs
            continue

        current.append(sentence)
        current_seconds += secs

        # Close the group once past target; the next sentence starts a new one.
        if current_seconds >= target_seconds:
            groups.append(current)
            current, current_seconds = [], 0.0

    if current:
        groups.append(current)

    groups = _merge_runts(groups, min_seconds, max_seconds, words_per_minute)

    return [
        Segment(
            index=i,
            text=" ".join(g),
            word_count=count_words(" ".join(g)),
            estimated_seconds=estimate_seconds(" ".join(g), words_per_minute),
        )
        for i, g in enumerate(groups)
    ]


def _merge_runts(
    groups: list[list[str]], min_seconds: float, max_seconds: float, wpm: float
) -> list[list[str]]:
    """Fold too-short groups into a neighbour.

    A 1.5-second scene reads as a glitch. Merging backwards keeps narrative
    order; if that would exceed max_seconds the runt is left alone, since an
    overlong scene is the lesser evil.
    """
    if len(groups) < 2:
        return groups

    out: list[list[str]] = []
    for group in groups:
        secs = estimate_seconds(" ".join(group), wpm)
        if out and secs < min_seconds:
            merged = out[-1] + group
            if estimate_seconds(" ".join(merged), wpm) <= max_seconds * 1.25:
                out[-1] = merged
                continue
        out.append(group)

    # A leading runt has no previous neighbour; push it into the next group.
    if len(out) >= 2 and estimate_seconds(" ".join(out[0]), wpm) < min_seconds:
        candidate = out[0] + out[1]
        if estimate_seconds(" ".join(candidate), wpm) <= max_seconds * 1.25:
            out = [candidate] + out[2:]
    return out


# ---------------------------------------------------------------------------
# The fidelity gate
# ---------------------------------------------------------------------------


def _comparable(text: str) -> str:
    """Reduce text to what must be preserved exactly: the words themselves.

    Whitespace is ignored because segmentation legitimately rejoins lines.
    Everything else — every character, digit, quote and symbol — must survive.
    """
    return re.sub(r"\s+", "", text)


class FidelityError(AssertionError):
    """Raised when segment text no longer reproduces the source script."""


def assert_fidelity(script: str, segments: list[Segment]) -> None:
    """Prove the segments still say exactly what the script said.

    This is the guard that makes it safe to hand text to a model at all: if any
    stage ever rewords, drops or reorders narration, the run dies here with a
    diff instead of producing a video that misquotes the author.
    """
    expected = _comparable(script)
    actual = _comparable(" ".join(s.text for s in segments))
    if expected == actual:
        return

    # Locate the first divergence and show its neighbourhood — "they differ" is
    # not actionable at 2000 characters.
    i = next(
        (i for i, (a, b) in enumerate(zip(expected, actual)) if a != b),
        min(len(expected), len(actual)),
    )
    lo, hi = max(0, i - 40), i + 40
    raise FidelityError(
        "Segmented narration does not reproduce the source script.\n"
        f"  first difference at comparable-character {i}\n"
        f"  script:   …{expected[lo:hi]}…\n"
        f"  segments: …{actual[lo:hi]}…\n"
        f"  lengths:  script={len(expected)} segments={len(actual)}"
    )


def segment_and_verify(script_text: str, **kwargs) -> tuple[str | None, list[Segment]]:
    """Full stage 1: normalise, peel the title, segment, then verify.

    Returns (title, segments). The title is excluded from narration but used as
    the project title in the IR.
    """
    normalized = normalize_script(script_text)
    title, body = split_title(normalized)
    segments = segment_script(body, **kwargs)
    assert_fidelity(body, segments)
    return title, segments
