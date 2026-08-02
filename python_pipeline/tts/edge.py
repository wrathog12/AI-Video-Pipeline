"""edge-tts provider: Microsoft Edge's online neural voices.

Chosen as the default for Phase 0 because it reports word-level timings from the
synthesiser itself — the ±150 ms sync requirement (R5) is then satisfied by
construction rather than by forced alignment.

Two things learned the hard way, both worth keeping in the log:

1.  edge-tts 7.x defaults to `boundary="SentenceBoundary"`. Constructing
    `Communicate(...)` without the explicit `boundary="WordBoundary"` yields
    zero word events and no error — the stream just contains sentence chunks.
2.  Offsets are in 100-nanosecond ticks (WebVTT/SSML convention), so the
    conversion is `/ 10_000` to reach milliseconds, not `/ 1_000`.

Caveat for the log's risk section: this speaks to an undocumented, reverse
engineered Microsoft endpoint. It needs network and can change without notice,
which is why `piper.py` exists as an offline provider.
"""

from __future__ import annotations

import asyncio
import io
import subprocess

import numpy as np

from .base import TTSResult, WordBoundary

# edge-tts reports offsets in 100ns ticks.
_TICKS_PER_MS = 10_000


class EdgeTTS:
    name = "edge-tts"

    def synthesize(self, text: str, *, voice: str, rate: str,
                   sample_rate: int) -> TTSResult:
        mp3, boundaries = asyncio.run(self._stream(text, voice, rate))
        if not mp3:
            raise RuntimeError(
                "edge-tts returned no audio. The service is unreachable or the "
                "voice name is invalid; set providers.tts: piper for an offline run."
            )
        pcm = _decode_to_pcm(mp3, sample_rate)
        return TTSResult(pcm=pcm, sample_rate=sample_rate, word_boundaries=boundaries)

    async def _stream(self, text: str, voice: str,
                      rate: str) -> tuple[bytes, list[WordBoundary]]:
        import edge_tts

        # boundary="WordBoundary" is REQUIRED; the default is SentenceBoundary.
        comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")

        audio = io.BytesIO()
        boundaries: list[WordBoundary] = []
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                audio.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                boundaries.append(
                    WordBoundary(
                        text=chunk["text"],
                        start_ms=chunk["offset"] / _TICKS_PER_MS,
                        duration_ms=chunk["duration"] / _TICKS_PER_MS,
                    )
                )
        return audio.getvalue(), boundaries


def _decode_to_pcm(mp3: bytes, sample_rate: int) -> np.ndarray:
    """Decode MP3 to canonical float32 mono PCM via ffmpeg.

    ffmpeg is already a hard dependency for muxing, so this avoids adding a
    second audio-decoding library. Decoding here (rather than storing the MP3)
    is what lets the cache key be a hash of samples instead of container bytes.
    """
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-f", "f32le", "-acodec", "pcm_f32le",
            "-ac", "1", "-ar", str(sample_rate),
            "pipe:1",
        ],
        input=mp3,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to decode TTS audio: {proc.stderr.decode(errors='replace')}")
    return np.frombuffer(proc.stdout, dtype="<f4").copy()
