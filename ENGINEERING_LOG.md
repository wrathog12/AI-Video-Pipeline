# Engineering Log

Written as work happens, not reconstructed afterwards. Entries are append-only.

---

## Phase 0 — Walking skeleton (complete)

**Goal.** Carry a hardcoded two-scene spec all the way to a playing MP4, exercising every
structural decision that is expensive to change later: the IR, ms-based timings, native word
alignment, the two-tier cache, video-only rendering, continuous-audio assembly, single mux.

**Result.** `output/run1.mp4` — 852×480 h264 + AAC, 825 frames, 27.500000 s.

### Measurements

| Run | Wall clock | Cache | Notes |
|---|---|---|---|
| Cold (no cache) | 90.6 s | 0/6 | 2 TTS calls, 2 Remotion renders |
| Warm (unchanged) | 1.2 s | 6/6 | zero network calls, zero renders |
| One scene edited via spec | 43.3 s | 5/6 | re-rendered scene_01 only |
| Portrait profile (9:16) | 87.1 s | 4/6 | audio reused, both scenes re-rendered |

Render dominates: ~45 s per scene at 852×480, versus ~2 s for TTS. That ratio is what the cost
model in the final submission will be built on, and it is why the render cache matters more than
the audio cache.

### Verification performed

- **A/V sync is structural.** ffprobe reports video duration 27.500000 s and audio duration
  27.500000 s — equal to six decimal places, because the audio track is built to an exact whole
  number of frames rather than encoded per scene and concatenated.
- **Determinism.** Two runs compared by decoding to raw frames (`-f rawvideo -pix_fmt rgb24`) and
  to raw PCM, then hashing: video `ab67600b…`, audio `07849c90…`, identical across both runs. The
  MP4s also happened to be byte-identical, but frame-hash equality is the claim — byte equality
  would not survive an ffmpeg version change.
- **R4 legibility.** Frame 300 inspected visually at 480p: `256` renders crisp, and `16,777,216`
  keeps every digit and comma. This is checked by eye now and by an OCR gate in Phase 3.
- **R5 timing.** Scene 1's `256` cue fires at 8375 ms. Frame 200 (≈6.7 s) correctly shows no value;
  frame 300 (≈10 s) shows it.
- **R6.** Edited `expression.resolved` in the spec to `EDITED-1024`, re-ran with `--spec`: the
  video changed, no LLM or TTS involved.
- **R7.** `--profile portrait` produced 480×852 with no code change; the vmin type scale kept text
  legible and the item card flipped to full width.

---

## Dead end 1 — Word timings silently absent from edge-tts

**What I expected.** edge-tts emits `WordBoundary` events during synthesis. The plan was to use
those as the primary alignment source (exact by construction, no model download, no forced
alignment), with WhisperX only as a fallback for providers that report nothing.

**What actually happened.** A probe over `Communicate(text, voice).stream()` returned 27,648 bytes
of audio and **zero** `WordBoundary` events. No exception, no warning — the stream simply contained
none. Had this gone unnoticed until the aligner ran, it would have surfaced as an empty
`word_triggers` list and a video where nothing was cued to the narration.

**Hypothesis.** Either the events were renamed in edge-tts 7.x, or they are gated behind an option.
A version rename seemed more likely at first, since the event name is dictated by the underlying
SSML/WebVTT convention.

**What I tried next.** Enumerated every chunk `type` the stream actually yields, rather than
filtering for the one I expected. Result: `{'audio': 45, 'SentenceBoundary': 2}`. So boundaries
*were* being emitted, at the wrong granularity. `inspect.signature(edge_tts.Communicate.__init__)`
then showed the cause directly:

```
boundary: Literal['WordBoundary', 'SentenceBoundary'] = 'SentenceBoundary'
```

**Resolution.** Pass `boundary="WordBoundary"` explicitly. 14 word events for a two-sentence probe,
with offsets in 100-nanosecond ticks (÷10,000 for ms, not ÷1,000).

**The unexpected bonus.** The `text` field of each event is the token from the *input text* —
`'255'`, not `'two hundred fifty-five'`. The synthesiser reports boundaries against what it was
given, not against what it pronounced. That removes an entire problem I had budgeted for: the
numeral-normalisation layer (`num2words` expansion plus an index map back to original tokens) is
only needed for the WhisperX fallback, which sees audio alone. On the native path, a reviewer
scrubbing to `255` in the script lands on the right frame with no normalisation at all.

**Kept as a risk.** This is an undocumented, reverse-engineered Microsoft endpoint requiring
network access. `piper.py` exists as an offline provider for exactly this reason.

---

## Dead end 2 — Alignment cache missed on every run (int16 quantisation off by one grid step)

**What I expected.** Second run, everything cached: 6/6 hits, no network.

**What actually happened.** 4/6. Both audio tiers hit and both render tiers hit, but **both
alignment tiers missed every single time** — and the log showed `re-synthesising scene_02 to
recover word timings`, meaning each "cached" run still hit the network. Worse, the align cache key
itself differed between runs: `c525fe295fd2` on the cold run, `37079f2314b3` on the warm one. A
content-addressed key that changes for identical content is a broken key.

**Hypothesis.** The align key is derived from `audio_pcm_sha256`, so the PCM hash was unstable. The
two runs hash *different objects*: the cold run hashes fresh float32 straight from the ffmpeg
decode, while the warm run hashes float32 read back from the cached PCM_16 WAV. If quantisation
were not idempotent, the round-trip would shift samples and change the digest — which is precisely
the failure mode the design's "hash decoded PCM, never container bytes" rule was supposed to
prevent. The rule was right; my implementation of it was not.

**What I tried next.** Wrote the round-trip as a direct test: hash a random float32 buffer, write
it with `write_wav`, read it back, hash again. Confirmed the two digests differed. The cause was in
`pcm_hash`:

```python
q = (pcm * 32767.0).astype(np.int16)   # wrong on two counts
```

Two defects. First, the scale factor: int16 PCM spans −32768…32767, and libsndfile writes with
32768, so my 32767 put every sample on a slightly different grid than the file on disk. Second,
`.astype(np.int16)` **truncates toward zero** rather than rounding, so the mapping was not
idempotent even against itself — a sample read back as exactly `i/32768` would not re-quantise to
`i`.

**Resolution.** A single `to_int16()` helper used by both `pcm_hash` and `write_wav`: clip to
[−1, 1], multiply by 32768.0, `np.round`, clip to the int16 range. `write_wav` now writes those
exact int16 samples, so the bytes on disk are precisely what was hashed. Verified idempotent across
two successive round-trips, then cold/warm re-run: **6/6 hits, 90.6 s → 1.2 s, zero network calls.**

**Why this was worth the time.** The symptom was mild — a warm run that was still fast because
audio and render both hit. The real defect was that a hash the whole cache depends on was not
stable across a serialisation boundary. R3 is graded by running the pipeline twice, so an unstable
content key is close to the worst possible bug in this system, and it presented as nothing more
than two missing cache rows.

---

## Incidental fixes worth recording

- **`soundfile` cannot infer format from `.wav.tmp`.** Atomic cache writes stage to a temp name, and
  libsndfile derives the container from the file extension, so a trailing `.tmp` raised
  `TypeError: No format specified`. Fixed by passing `format="WAV"` explicitly *and* by putting the
  real extension last in staging names (`<key>.<pid>.staging.wav`). Remotion infers its container
  from the extension too, so the ordering matters in both places.
- **`shutil.copyfile` onto itself.** The cache's `put_file` generated its own temp name that
  collided with the caller's staging path, raising `SameFileError`. The cache now owns staging paths
  via `staging_path()`, and commits with an atomic `Path.replace` (a move, not a copy — worth doing
  for multi-megabyte renders).
- **`npx` is `npx.cmd` on Windows.** `subprocess` without `shell=True` will not find a bare `npx`.
  Resolved via `shutil.which` over both names.
- **Remotion props go via a file, not argv.** A JSON payload on a Windows command line runs into
  quoting rules and the ~8191-character limit; `--props=<path>` avoids both.
