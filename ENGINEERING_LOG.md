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

## Phases 1–3 — the real pipeline (complete)

**Goal.** Replace the hardcoded skeleton spec with the actual compiler: deterministic segmentation,
LLM annotation, symbolic evaluation, six templates, and shipped verifiers for R3, R4 and R5.

**What was built.** `segmenter.py` (fidelity-gated), `evaluator.py` (AST whitelist), `annotate.py`
(annotation → props mapping + reconciliation), `heuristic_annotator.py` (offline path),
`llm_annotator.py` (Gemini + Claude), `verify_determinism.py`, `cue_check.py`, plus
`TitleCard` / `KeyValuePanel` / `BigNumber` / `ProcessSteps` and a shared `ValueBlock`.

### Measurements

| Run | Wall clock | Cache | Output |
|---|---|---|---|
| Script A cold, 7 scenes | 412.7 s | 1/22 | 2.82 MB, 4148 frames, 138.267000 s |
| Script A warm, unchanged | 6.3 s | 22/22 | frame-identical |
| One `expr` edited via `--spec` | 75.2 s | 20/21 | only `scene_06` re-rendered |
| Script B cold, unseen topic | 364.3 s | — | 2.58 MB, 3447 frames, 114.900000 s |

The 412.7 s → 6.3 s → 75.2 s progression is the cost model in miniature: a cold run is ~59 s of
render per scene, a warm run is free, and a one-value edit costs exactly one scene. Render still
dominates TTS by roughly 30:1, which is why the render cache is the one that matters.

### Verification performed

- **R3.** `verify_determinism.py` over two full runs: 4148 frames identical, audio identical, specs
  identical excluding provenance, exit 0. Byte equality also happened to hold and is reported
  *separately* from the claim. Then run against a genuinely different pair to prove the verifier can
  fail: `NOT DETERMINISTIC, 700 of 4148 frames differ; first at index 2867 (t=95.567s)`, exit 1. A
  verifier that has only ever passed is not evidence.
- **R4.** 18/18 arithmetic cases correct; **20/20 malicious or invalid expressions rejected**
  (`__import__`, `open`, `eval`, attribute access, lambda, comprehensions, `9**9**9**9`, `2**100000`,
  `1/0`, bool/str constants, ternary, bit-shift, `~`, kwargs); 13/13 formatting cases correct. The
  authored-number gate refused `256`, `16,777,216`, `3.14`, `25%`, `-42` and allowed `0-255`, `RGB`,
  `8-bit`, `sRGB / Display P3`. Visually confirmed at 480p that frame 3100 shows `16,777,216` and the
  edited run shows `4,294,967,296`, every digit and comma crisp.
- **R5.** `cue_check.py`: script A 5/5 cues matched (4 exact, 1 prefix), worst quantisation 11.7 ms
  against a 150 ms budget; script B 3/3 matched, worst 8.0 ms. Both PASS. Visually confirmed the
  `16.8` cue at 19,150 ms — empty item box at frame 3100, `16.8` present at frame 3400.
- **R6 + R8.** Edited `scene_06.expression.expr` from `256**3` to `256**4` in the spec and re-ran
  with `--spec`: Python recomputed the display string, exactly one scene re-rendered, 412 s → 75 s.
- **R2.** Script B (compound interest — a different topic, different arithmetic) ran through to a
  playing MP4 with no code edits.
- **Graceful degradation.** With Gemini configured and no key on disk, the run logs two WARNING
  lines and completes via the heuristic annotator. Provenance then honestly reports
  `annotator: heuristic` rather than the configured model.
- **A/V sync.** Video duration == audio duration to six decimals on both scripts.
- `tsc --noEmit` exits 0; `eslint src` clean.

### Known gaps, stated rather than hidden

- **The Gemini path has never made a live call.** The schema builds, `parse_response` is tested
  against synthetic payloads including fenced JSON and bare lists, and the fallback is exercised —
  but no request has left the machine, because the key is not on disk yet.
- **Script B yields 5/7 `Fallback` scenes** on the heuristic annotator, because it cannot parse
  word-form arithmetic ("seventy-two divided by seven", "one point zero seven raised to the number
  of years"). This is precisely the gap the LLM annotator exists to close, and it is the single most
  useful thing to measure the moment the key lands.
- **Cartesia is untested** against a real key, and the voice UUID in `config.yaml` is an unverified
  placeholder.
- `cue_check.py` deliberately does not claim provider timestamp accuracy — only matching and
  quantisation. See its docstring for the three-way decomposition of the ±150 ms budget.

---

## Dead end 3 — A number regex that split `16,777,216` into `16,777`

**What I expected.** `_NUMBER` in the heuristic annotator matches thousands-separated integers whole.
The pattern ended in a `(?![\w.])` guard, whose purpose was to stop `2.5.1` from matching as `2.5`.

**What actually happened.** The rendered frame showed `16,777` where the narration says
`16,777,216`. Not a rounding error — a *truncation*, which is exactly the "mangled digits" failure
R4 names explicitly, and it appeared only for numbers at the end of a sentence.

**Hypothesis.** The trailing full stop. `"…equals 16,777,216."` ends in `.`, which `[\w.]` matches,
so the lookahead failed — and a failing lookahead does not fail the match, it makes the regex engine
**backtrack**. The greedy `(?:,\d{3})+` gave back its last group, the lookahead then saw `,` instead
of `.`, and the match succeeded on a shorter, wrong string. Mid-sentence numbers were followed by a
space and never triggered the backtrack, which is why the bug looked positional.

**What I tried next.** Confirmed by testing the same number with and without a trailing period:
`16,777,216` matched whole, `16,777,216.` matched as `16,777`. That isolated the guard as the cause
rather than the alternation.

**Resolution.** Make the guard reject only a *digit-bearing* continuation:

```python
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\d.]\d|\w)")
```

`(?![\d.]\d|\w)` still rejects `2.5` inside `2.5.1` (a `.` followed by a digit), but a sentence-final
period followed by whitespace or end-of-string is fine. Verified across 8 cases.

**Why this was worth recording.** A negative lookahead reads like an assertion but behaves like a
constraint the engine will *negotiate around*. The failure mode is not "no match" — it is a
plausible-looking shorter match, which in a number pipeline means silently wrong digits on screen.

---

## Dead end 4 — The lint-enforced determinism ban was never running

**What I expected.** `.eslintrc.cjs` bans `Math.random()`, `Date.now()`, `new Date()` and
`performance.now()`. `WordCue.tsx`'s docstring says the ban is "enforced rather than merely
documented". I believed that because I had written the rule.

**What actually happened.** Running `npx eslint src` to confirm before writing it into the log:

```
Failed to load parser '@typescript-eslint/parser' declared in '.eslintrc.cjs'
```

The parser was configured but never installed. ESLint had therefore never successfully linted a
single file. Every claim about the ban being enforced was false, and it was false in the most
embarrassing possible way — documented, believed, and unexecuted.

**Hypothesis.** Nothing subtle; a missing dev dependency. The interesting question was whether the
*rule itself* was correct, since it had never once been evaluated.

**What I tried next.** Installed `@typescript-eslint/parser@8` and
`@typescript-eslint/eslint-plugin@8`, then wrote a deliberate probe file containing `Math.random()`
and `Date.now()` rather than just re-running on the clean tree — a clean pass proves the linter ran,
not that the rule works.

**Resolution.** 3 errors fired correctly (`no-restricted-syntax` ×2, `no-restricted-globals` ×1).
Probe deleted; `eslint src` now clean and meaningfully so.

**The general lesson, which cost me twice in this project.** A guard you have not watched fail is
not a guard. Dead end 2 was a cache key that changed for identical content; this is a lint rule that
never parsed. Both were *written* correctly and both were inert. `verify_determinism.py` is now
deliberately tested against a known-different pair for the same reason.

---

## Dead end 5 — A cue word that matched no trigger, and why that is worse than a crash

**What I expected.** `cue_word` in the IR names a narrated word; `findTrigger` in `WordCue.tsx`
normalises both sides and matches. The annotator names `8`, the narration contains `8`, done.

**What actually happened.** Scene 3's cue `8` matched nothing. edge-tts emitted the token as
`8-bit`, which normalises to `8bit`, and `8 !== 8bit`.

**Why it mattered more than it looked.** `useCueProgress` returns `1` when there is no trigger — a
deliberate choice so a scene with no alignment data renders visible rather than blank. But that
makes a *miss* indistinguishable from *no alignment*: the element appears at frame 0, fully ignoring
the audio, and nothing warns. The video still looks fine. It is simply no longer synced, which is
the entire R5 claim, failing silently.

**Hypothesis.** The cue names a word from the script; the TTS provider decides tokenisation. The two
disagree structurally, not occasionally — compounds (`8-bit`), and providers that emit a run of
words as one event.

**What I tried next.** Rather than loosening the match until the symptom went away, I made the
failure *countable*: wrote `cue_check.py`, which walks the spec, mirrors `findTrigger`, and reports
per-cue how each one matched (exact / prefix / sub-token / none) plus the quantisation error. A miss
is now a non-zero exit, not an invisible degradation.

**Resolution.** Three narrowing passes in `findTrigger` — exact, then cue-as-prefix-of-token
(`8` in `8bit`), then cue-as-word-inside-a-multi-word-event. Script A: 5/5 matched. Script B:
**3/3 matched, all three via the prefix fallback** — so on the unseen script the exact pass caught
nothing at all. The fallbacks are load-bearing, not defensive.

**Accepted cost.** The matching logic is now duplicated in Python and TypeScript, because the
renderer cannot import Python. They must be changed together, and `cue_check.py` is what catches it
if they drift — noted in both files' docstrings.

---

## Dead end 6 — The same number rendered twice on screen

**What I expected.** The headline value and the supporting items come from different parts of the
sentence, so excluding the headline's own expression string from the item scan is enough.

**What actually happened.** 13 values across 7 scenes, with visible duplicates — the hero figure and
a supporting item showing the same number.

**Hypothesis and the two things wrong with it.** The exclusion compared against the headline's
*expression*. For "256 times 256 times 256" the headline expr is `256**3`, which shares no digits
with the narration, so it excluded nothing. Fixing that surfaced a second, independent leak: the
narration also states the *answer* aloud, outside the headline's span — "…equals 16,777,216" — so
even a correct span exclusion leaves the computed result to be re-detected as a fresh number.

**Resolution.** Two exclusions, because there are two mechanisms: `exclude_phrase` (the text span the
headline was derived from) and `exclude_value` (the headline's computed *result*). 13 values with
duplicates → 6 clean ones.

**Related fix in the same area.** Supporting items had their format chosen by digit count — `int` for
short numbers, `thousands` for long ones — so `16.8` rendered as `17`. The arithmetic was right and
the display was wrong, which by R4's standard is still a mangled digit. `_supporting_items` now calls
`_format_for(expr)` like every other path.

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
- **The abbreviation guard could never fire for initials.** `segmenter.py` lowercased the token
  before testing it against `[A-Z]`, so the initials branch was unreachable and
  `"J. R. Tolkien wrote books. They were long."` split into 4 pieces instead of 2. The abbreviation
  test needs the lowered token and the initials test needs the original; they are not the same
  string. Battery went 7/8 → 9/9.
- **`get_annotator('claude-opus-5')` was refused while `config.yaml` still specified it.** That
  combination would have made the R7 multi-vendor claim false at startup — the config named a
  provider the dispatcher rejected. Added `ClaudeAnnotator` and switched the default to
  `gemini-2.5-flash`. Verified all five dispatch paths: `heuristic`/`none`/`''` → heuristic,
  `gemini-*` → Gemini, `claude-*` → Claude, unknown → refused with an actionable message.
- **The annotation contract is flat, and that was a deliberate reversal.** The first design had the
  model emit each template's prop shape directly. Seven nested shapes is seven ways to be subtly
  wrong, prop layout is a renderer concern the model should not know, and adding a template would
  invalidate every cached annotation. The model now returns one uniform `Annotation`; `build_props`
  in `annotate.py` maps it per template. `ComparisonGrid` is excluded from
  `IMPLEMENTED_TEMPLATES` for exactly this reason — the flat contract has no notion of columns, and
  faking one would be worse than a `Fallback`.
