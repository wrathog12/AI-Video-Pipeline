"""TTSProvider interface (R7).

Providers return canonical decoded PCM, not an encoded file. Hashing and
concatenation both happen in the sample domain, so every provider must
normalise to the same sample rate / channel count / dtype before returning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass
class WordBoundary:
    """A word timing reported by the synthesiser itself.

    `text` is the token as it appeared in the *input* text (e.g. "255", not
    "two hundred fifty-five"), which is what a reviewer scrubbing the script
    will look for.
    """

    text: str
    start_ms: float
    duration_ms: float

    @property
    def end_ms(self) -> float:
        return self.start_ms + self.duration_ms


@dataclass
class TTSResult:
    # float32 mono in [-1, 1]. Canonical for hashing and for track assembly.
    pcm: np.ndarray
    sample_rate: int
    # None when the provider cannot report word timings; the Aligner then has
    # to derive them (see align/whisperx.py).
    word_boundaries: list[WordBoundary] | None = field(default=None)

    @property
    def duration_ms(self) -> float:
        return 1000.0 * len(self.pcm) / self.sample_rate


@runtime_checkable
class TTSProvider(Protocol):
    name: str

    def synthesize(self, text: str, *, voice: str, rate: str,
                   sample_rate: int) -> TTSResult: ...
