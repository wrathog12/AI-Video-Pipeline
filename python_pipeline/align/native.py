"""Native aligner: word timings taken from the TTS provider itself.

This is the primary path. The synthesiser knows exactly when it emitted each
word, so the timings are exact by construction, cost nothing extra, and require
no model download. Forced alignment (whisperx.py) exists only for providers
that report nothing.

It also sidesteps the numeral problem entirely: edge-tts reports boundary text
as the *input* token ("255"), so a reviewer scrubbing to `255` in the script
lands on the right frame. A forced aligner sees only the audio, which says
"two hundred fifty-five", and has to normalise and map back.
"""

from __future__ import annotations

import numpy as np

from ..schema import WordTrigger
from ..tts.base import WordBoundary


class NativeAligner:
    name = "native"

    def align(
        self,
        pcm: np.ndarray,
        sample_rate: int,
        text: str,
        hint: list[WordBoundary] | None = None,
    ) -> list[WordTrigger]:
        if not hint:
            raise RuntimeError(
                "The 'native' aligner needs word boundaries from the TTS provider, "
                "but none were supplied. Either the provider cannot report them "
                "(set providers.aligner: whisperx) or edge-tts was constructed "
                "without boundary='WordBoundary'."
            )

        duration_ms = 1000.0 * len(pcm) / sample_rate
        triggers: list[WordTrigger] = []
        for wb in hint:
            start = max(0.0, wb.start_ms)
            end = min(wb.end_ms, duration_ms)
            if end <= start:
                # Zero-length or past-the-end event; a downstream `frame >= start`
                # test would fire immediately and desync the cue.
                continue
            triggers.append(
                WordTrigger(word=wb.text, start_ms=round(start), end_ms=round(end))
            )
        return triggers
