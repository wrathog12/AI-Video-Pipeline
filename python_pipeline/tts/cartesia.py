"""Cartesia Sonic provider — the primary TTS path.

Chosen over edge-tts for voice quality, and over the alternatives because it
reports word-level timings from the synthesiser itself. Sync therefore stays
exact-by-construction: no forced-alignment model, no torch download, no
inference pass (see context.md §6 Stage 4).

Two endpoints exist and only one of them is usable here:

    POST /tts/bytes   returns audio only. No timings. Unusable for R5.
    POST /tts/sse     Server-Sent Events, interleaving audio chunks with
                      `timestamps` events when `add_timestamps: true`.

So this provider always streams SSE, even though it wants the complete buffer:
the audio is the easy half, and the timings are the reason we are here.

Response shape (`word_timestamps`) is three PARALLEL ARRAYS, not a list of
objects:

    {"words": ["Hello", "world"], "start": [0, 0.5], "end": [0.4, 0.9]}

Times are SECONDS (floats), unlike edge-tts's 100-nanosecond ticks. Both are
normalised to milliseconds at the WordBoundary boundary so nothing downstream
has to know which provider ran.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Iterator

import numpy as np

from .base import TTSResult, WordBoundary

API_URL = "https://api.cartesia.ai/tts/sse"

# Pinned deliberately. The API version is part of the request contract, so an
# unpinned value would let the response shape change under us between runs —
# a determinism hazard as real as an unpinned model (context.md §7).
API_VERSION = "2026-03-01"

DEFAULT_MODEL = "sonic-3.5"

# Request raw float32 at the pipeline's canonical rate: no container to parse,
# no ffmpeg decode hop, and float32 is already the internal PCM format.
_ENCODING = "pcm_f32le"
_NUMPY_DTYPE = "<f4"


class CartesiaTTS:
    name = "cartesia"

    def __init__(self, *, model_id: str = DEFAULT_MODEL, api_key: str | None = None,
                 language: str = "en", timeout: float = 180.0) -> None:
        self.model_id = model_id
        self.language = language
        self.timeout = timeout
        self._api_key = api_key or os.environ.get("CARTESIA_API_KEY", "")

    @property
    def api_key(self) -> str:
        if not self._api_key:
            raise RuntimeError(
                "CARTESIA_API_KEY is not set. Export it, or select an offline "
                "provider with `providers.tts: edge-tts` in config.yaml."
            )
        return self._api_key

    def synthesize(self, text: str, *, voice: str, rate: str,
                   sample_rate: int) -> TTSResult:
        """Synthesise one scene's narration.

        `voice` is a Cartesia voice UUID. `rate` reuses the edge-tts "+10%"
        percentage form so config stays provider-agnostic; it is translated to
        Cartesia's multiplicative `generation_config.speed`.
        """
        import requests

        body: dict[str, object] = {
            "model_id": self.model_id,
            "transcript": text,
            "voice": {"mode": "id", "id": voice},
            "output_format": {
                "container": "raw",
                "encoding": _ENCODING,
                "sample_rate": sample_rate,
            },
            "language": self.language,
            "add_timestamps": True,          # the whole reason for /tts/sse
            "add_phoneme_timestamps": False,
        }
        speed = _rate_to_speed(rate)
        if speed is not None:
            body["generation_config"] = {"speed": speed}

        resp = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Cartesia-Version": API_VERSION,
                "Content-Type": "application/json",
            },
            json=body,
            stream=True,
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Cartesia TTS failed ({resp.status_code}): {resp.text[:500]}"
            )

        audio = bytearray()
        boundaries: list[WordBoundary] = []
        for event in _iter_sse(resp):
            etype = event.get("type")
            if etype == "chunk":
                audio.extend(base64.b64decode(event["data"]))
            elif etype == "timestamps":
                boundaries.extend(_parse_word_timestamps(event.get("word_timestamps")))
            elif etype == "error":
                raise RuntimeError(f"Cartesia stream error: {event}")
            elif etype == "done":
                break

        if not audio:
            raise RuntimeError("Cartesia returned no audio for this scene.")

        pcm = np.frombuffer(bytes(audio), dtype=_NUMPY_DTYPE).copy()

        if not boundaries:
            # Loud, not silent. An empty list would flow downstream as a video
            # with nothing cued to the narration — the exact failure mode that
            # edge-tts's default `boundary="SentenceBoundary"` produced.
            raise RuntimeError(
                "Cartesia returned audio but no word_timestamps events. "
                "Verify `add_timestamps` is honoured by this model, or set "
                "`providers.aligner: whisperx` to derive timings from audio."
            )

        # SSE delivers timestamps incrementally and events may overlap at chunk
        # seams; sort so downstream code can assume monotonic order.
        boundaries.sort(key=lambda b: b.start_ms)
        return TTSResult(pcm=pcm, sample_rate=sample_rate, word_boundaries=boundaries)


def _iter_sse(resp) -> "Iterator[dict]":
    """Yield decoded JSON payloads from an SSE stream.

    Written against the wire format rather than a helper library: the stream is
    plain `data: {...}` lines, and adding an SSE dependency to parse two line
    prefixes is not worth it.
    """
    for raw in resp.iter_lines(decode_unicode=True):
        if not raw:
            continue
        line = raw.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                continue


def _parse_word_timestamps(wt: object) -> list[WordBoundary]:
    """Convert the parallel-array form into WordBoundary objects.

    `words`, `start` and `end` are separate arrays that must be zipped. Times
    are seconds; WordBoundary is milliseconds.
    """
    if not isinstance(wt, dict):
        return []
    words = wt.get("words") or []
    starts = wt.get("start") or []
    ends = wt.get("end") or []

    out: list[WordBoundary] = []
    # zip() truncates to the shortest, which is the safe behaviour if a partial
    # event ever arrives with mismatched array lengths.
    for word, start_s, end_s in zip(words, starts, ends):
        if word is None or start_s is None or end_s is None:
            continue
        start_ms = float(start_s) * 1000.0
        end_ms = float(end_s) * 1000.0
        out.append(
            WordBoundary(
                text=str(word),
                start_ms=start_ms,
                duration_ms=max(0.0, end_ms - start_ms),
            )
        )
    return out


def _rate_to_speed(rate: str) -> float | None:
    """Translate the config's "+10%" / "-20%" rate into Cartesia's speed factor.

    Config uses the percentage form because that is what edge-tts accepts, and
    a provider swap must not require editing config.yaml (R7). Cartesia's range
    is 0.6–1.5, so the result is clamped rather than rejected.
    """
    if not rate:
        return None
    text = rate.strip().rstrip("%")
    try:
        percent = float(text)
    except ValueError:
        return None
    if percent == 0:
        return None
    return max(0.6, min(1.5, 1.0 + percent / 100.0))
