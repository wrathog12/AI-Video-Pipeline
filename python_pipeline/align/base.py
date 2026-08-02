"""Aligner interface (R5, R7).

An aligner turns (audio, transcript) into word timings in milliseconds. Two
implementations ship: `native` (timings straight from the synthesiser) and
`whisperx` (forced alignment, for providers that report nothing). Both satisfy
this one signature, which is the concrete evidence for R7's swappability claim.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from ..schema import WordTrigger
from ..tts.base import WordBoundary


@runtime_checkable
class Aligner(Protocol):
    name: str

    def align(
        self,
        pcm: np.ndarray,
        sample_rate: int,
        text: str,
        hint: list[WordBoundary] | None = None,
    ) -> list[WordTrigger]: ...
