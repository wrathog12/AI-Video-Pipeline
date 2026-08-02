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
- `tsc --noEmit` exits 0; `eslint --ext .ts,.tsx src` clean over 15 files. ⚠️ The original form of this
  claim was `eslint src` clean, which was false — see Dead end 11.

### Verification performed on the icon build (Phase 3.5)

Both scripts re-rendered end to end with `providers.assets: icon_pack`:

| | Script A | Script B |
|---|---|---|
| Exit code | 0 | 0 |
| Frames / duration | 4148 / 138.267000 s | 3447 / 114.900000 s |
| On-screen values | 12 (8 computed) | 8 (8 computed) |
| Scenes with an icon | **6 / 7** | **6 / 7** |
| `cue_check` | **12/12 PASS**, worst 13.3 ms | **8/8 PASS**, worst 15.3 ms |

- **All 12 icon cues resolve to a real word trigger.** Checked explicitly rather than assumed,
  because a missed cue is not fail-safe: `useCueProgress` returns 1 with no trigger, so an unmatched
  icon would appear at frame 0 and silently ignore the audio rather than failing visibly.
- **Frames read at the cue times**, not just probed for existence: 🍎 top-right of the title card at
  t=4.0 s; 👁 bottom-right of the RGB panel at t=26.8 s; 🧮 on the range card at t=62.0 s; 📅 top-left
  of script B's ComparisonGrid at t=70.3 s, clear of all three bars.
- **The R7 seam exercised in all four states**, since an interface with one live implementation is not
  a seam: `icon_pack` resolves; `null` returns `[]`; an unknown provider name raises
  `Unknown asset provider: 'bogus' (available: null, icon_pack)`; and an `IconPackProvider` pointed at
  an empty directory reports `available=0` and resolves to `[]` — a missing pack degrades to no icon,
  never to a render error.
- **`render_key` proven sensitive to assets**: no-assets, apple and eye produce three distinct
  digests, and omitting the argument equals the empty case. Without this, flipping the provider would
  serve cached icon-free frames.
- `tsc --noEmit` exits 0 and `eslint --ext .ts,.tsx src` is clean on the 16-file tree (with
  `SceneIcon.tsx` added).

### Known gaps, stated rather than hidden

- ~~**The Gemini path has never made a live call.**~~ **Resolved in Phase 3.5.** Live calls now
  succeed on both scripts; the measured annotator contribution is recorded in that section.
- ~~**Script B yields 5/7 `Fallback` scenes**~~ on the heuristic annotator, because it cannot parse
  word-form arithmetic ("seventy-two divided by seven"). **Measured with Gemini live: 1/7.** That
  delta is the annotator's entire contribution.
- **Cartesia is untested** against a real key, and the voice UUID in `config.yaml` is an unverified
  placeholder. The shipped videos use `edge-tts`.
- ~~**`assets` is always empty.**~~ **Closed in Phase 3.5.** The `icon_pack` provider now matches
  narration keywords against 53 vendored Noto Emoji SVGs; 6/7 scenes carry an icon on both scripts and
  the apple appears in scene 1 of script A. What remains true is the honest limit: **an icon labels
  the topic, it does not encode a value.** The swatch strip and the comparison bars are content; the
  apple is a caption. So this closes "I don't see an apple" without closing "the frame feels sparse" —
  scene *count* is a separate axis that only sub-scene beats would move.
- **The catalog is a hand-made mini-ontology.** 53 icons, ~172 keywords, curated by hand. A script
  about a topic outside that vocabulary gets fewer icons, and the only fix is a human adding aliases.
  This is deliberate — see Dead end 12 for the three alias rules and why automatic import cannot
  replace the judgement — but it is maintenance debt, and "huge package of emojis" oversells it: the
  pack is large, the *usable curated slice* is necessarily small.
- `cue_check.py` deliberately does not claim provider timestamp accuracy — only matching and
  quantisation. See its docstring for the three-way decomposition of the ±150 ms budget.
- **`README.md`, `Dockerfile`, `docker-compose.yml` and `run.ps1` are 0-byte placeholders**, and
  `context.md` §3 marked all four as existing until this was checked with `stat`. Same class of error as
  the 0-byte components in Phase 3.5: a file that exists is not a file that is written, and a ✓ in a
  directory tree is the cheapest place in the project to state a falsehood. `run` and `run.cmd` work, so
  R1 holds; the clean-machine story in §10 is a plan, not a state.
- **R3 has not been re-verified on the icon build.** `verify_determinism.py` last passed on the
  pre-icon engine. Nothing in the icon path is non-deterministic by construction — keyword extraction
  and lookup are pure functions of the script, glyphs are files on disk, and every animation is a
  function of `useCurrentFrame()` — but "by construction" is exactly the reasoning Dead ends 4 and 11
  punished. It is unverified, not verified.

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

## Phase 4 — Making the frame carry the topic (in progress)

**The complaint that started it.** With real keys in place and both videos watched: *"they don't have
much elements … the narration mentions apple but I don't see any red apple … when explaining RGB, no
colour palette showing anything … it looks pale and bland."*

The honest diagnosis was not a design flaw. `SwatchStrip.tsx`, `CountUp.tsx`,
`ComparisonGrid.tsx` and all three `python_pipeline/assets/*.py` files were **0 bytes, imported by
nobody**. The architecture had anticipated exactly this complaint; the code was never written. Worth
recording because the failure mode is specific: a directory tree that looks complete, a design doc
that describes the missing behaviour in detail, and no import anywhere that would have surfaced the
gap. `tsc` cannot fail on a component that nothing references.

Roughly half the blandness was also a *different* cause: the videos watched had been produced on the
degraded heuristic path, before any key was present. Live Gemini changed the numbers materially.

| | Heuristic (what was watched) | Gemini (live) |
|---|---|---|
| Script A on-screen values | 6 | **12** |
| Script B on-screen values | 6, mostly junk | **8** |
| Script B `Fallback` scenes | **5 of 7** | **1 of 7** |

Gemini parses word-form arithmetic the heuristic cannot: "a thousand dollars at seven percent for
thirty years" → `1000 * (1.07)**30` → `7612`.

**What was built, part 1.** The three empty components, plus the structured-data change that makes a
swatch possible without breaking R4 (below).

**What was built, part 2 — the `icon_pack` provider.** 53 Noto Emoji SVGs (Apache-2.0), vendored to
`remotion_engine/public/icons/` by a one-time `python -m python_pipeline.assets.vendor_icons` and
committed with a SHA-256 manifest. `assets/base.py` extracts keywords in Python, `icon_pack.py` looks
them up, `SceneIcon.tsx` draws at most one per scene in a corner.

Four decisions worth recording, because in each case the obvious version is wrong:

1. **Keywords are extracted in Python, not requested from the LLM.** Asking the annotator for them
   would bump `PROMPT_VERSION`, invalidating every cached annotation on disk — paying the annotation
   cache to add decoration. It would also make icon choice model-dependent, importing R3's problem
   into the visual layer. And it is unnecessary: the narration already contains the nouns.
2. **A diffusion model was considered and rejected** (the user asked directly). It emits pixels, not
   paths; vectorising them is blobby; and output is not reproducible across GPU, driver and kernel
   even at a fixed seed, which breaks R3 outright. A build-time bake would have been acceptable, but a
   curated pack is better *and* auditable — a generated icon has no upstream anyone can check.
3. **Icons are absolutely positioned, never in flow.** If an icon participated in layout, whether the
   provider matched a keyword would change where the text sits — so the same script would compose
   differently under `null` and `icon_pack`, and the "every template looks complete with
   `assets: []`" property would quietly stop holding.
4. **`assets` is a component of `render_key`.** Switching providers changes what is on screen without
   touching a single prop. Omitting it would serve icon-free frames from cache and make the new
   provider look like it does nothing — the same class of bug as Dead end 8.

**Measured on both scripts.** 6/7 scenes carry an icon on each; 12/12 icon cues match a real word
trigger, so none silently defaults to show-immediately. Script A scene 1 shows the apple.

**Still not approved / not started:** background motifs and sub-scene beats. Stated plainly to the
user before building: icons are worth roughly a 15–20% improvement because **an icon labels the topic
while a swatch encodes a value**, and only sub-scene beats would move scene *count*, which was the
other half of the original complaint.

---

## Dead end 7 — A colour swatch cannot be drawn from the string `"(255, 0, 0)"`

**What I expected.** The IR already carries `resolved: "(255, 0, 0)"`. A swatch component reads it,
splits on commas, and paints. Half an hour of work.

**Why that is wrong.** Splitting `"(255, 0, 0)"` back into three numbers is arithmetic in the
renderer, performed on a value the pipeline promises Python computed — precisely the R4 boundary that
the whole `expr`/`resolved` split exists to hold. It would also be *silently* wrong rather than
loudly wrong: a parse that mis-handles a format change paints a plausible colour next to a correct
number, and a wrong colour beside a correct number reads as *the number* being wrong.

**Resolution.** `Value.channels: list[float]`, filled by `evaluator.channels_of()` from the tuple the
evaluator already computed, and never by the annotator. The structure was always there; it was being
discarded at the formatting step. `channels_of` returns `[]` for anything that is not a tuple of
finite numbers — a partially-numeric tuple degrades to "no channels" instead of a half-filled list.

**The topic-agnosticism trap inside the fix.** The obvious implementation says "channels[0] is red".
That is the Script-A trap in a new place: any three numbers would then paint a colour. `classifyChannels`
decides by *arity and range* — 3ch/0–255 → rgb, 4ch/0–100 → cmyk, 1ch → greyscale ramp, **everything
else → proportional bars**. The `bars` branch is the load-bearing one. `[1967, 7612]` from the compound
interest script has two channels; guessing a colour from it would produce a confidently wrong swatch.
`SwatchStrip` returns `null` when nothing has channels, so no template needs to know whether *this*
script is about colour.

---

## Dead end 8 — Editing a template served the previous run's frames

**What I expected.** Write the new components, re-run, see them.

**What actually happened.** Caught before it could waste a debugging session, but only by reading
`render_key` while wiring the components in: the key hashed props, theme and dimensions — and **not the
renderer's own source**. Every scene would have been a cache HIT after a component rewrite.

**Why this failure is worse than it sounds.** It does not look like a stale cache. It looks like *the
edit didn't work* — so it gets debugged in the TSX file, which is correct code, indefinitely.

**Resolution.** `cache.engine_fingerprint()` hashes all of `src/` plus the Remotion config and
lockfile into one digest that `render_key` now requires. Deliberately coarse: any component edit
invalidates every scene. Being wrong toward "re-render too much" costs render minutes; being wrong the
other way costs correctness. Verified stable across calls, moving on edit, and returning to the
original digest on restore.

---

## Dead end 9 — One algebraic expression killed the entire video

**What I expected.** Script B would render with the new components like script A did.

**What actually happened.** `[engine] FAILED: EvaluationError: Unknown name 'P' (available: e, pi,
tau)`, exit 1, no video at all. Gemini had annotated one segment with `expr: "P * (1.07)**N"` and
another with `72 / R` — the narration states a *general formula*, and the model transcribed it
faithfully as algebra.

**What was right and what was wrong.** The evaluator was right to refuse it: free variables are exactly
what the whitelist exists to stop, and the message named the offending symbol precisely. The *blast
radius* was wrong. R2 says an unseen script must still produce a video, and one unevaluable value out
of nine should cost that one value, not the run. A hard failure here also makes the pipeline brittle
in the most annoying way possible — non-deterministically, since it depends on model phrasing.

**Resolution, in two layers.** Both were needed, and neither alone is sufficient:

1. **Degrade, don't abort.** `resolve_props` catches `EvaluationError` per value and drops that value
   from the tree entirely — not to an empty `resolved`, because a blank box on screen reads as a
   rendering bug while an absent one reads as "this scene has less to show". Every drop is returned as
   a warning and printed by the run log, so a run that quietly lost half its numbers is impossible.
   A value-led template left with nothing (`BigNumber` with no number is a title in empty space) is
   downgraded to `Fallback` — the same repair path `reconcile()` already uses.
2. **Stop it at the source.** The prompt said "no names", which was evidently too weak against a
   narration that dictates a formula. It now shows the wrong form and the right form side by side
   (`P * (1.07)**N` vs `1000 * (1.07)**30`), tells the model to put a general formula in the *label*
   as prose, and to choose `Fallback` when a segment has no concrete figures. `PROMPT_VERSION` 4 → 5.

Layer 2 fixed script B outright — 8/8 values numeric, zero drops, and `ComparisonGrid` selected for the
decade-vs-decade contrast. Layer 1 is what keeps the *next* unseen script from failing the same way,
and it is the one that matters for R2: verified by hand against `P * (1.07)**N`, `72 / R` and `foo+1`,
producing 3 drops, 1 downgrade, and a rendered video.

**The lesson.** A correct guard in the wrong scope is still a defect. "Refuse unsafe input" and "fail
the whole run on unsafe input" are separate decisions, and I had only made the first one.

---

## Dead end 10 — A cue word that could not possibly match, found only by running the checker

**What I expected.** With the swatches rendering, script A was done. Run `cue_check.py` as a
formality.

**What actually happened.** `FAIL — 2 cue word(s) matched no trigger; these elements ignore the audio
and appear at frame 0: scene_05.items[0]='(255, 0, 0)', scene_05.items[1]='(0, 0, 0)'`. 8/10 matched.

**Hypothesis and why it was structural, not a bad guess by the model.** `word_triggers` are single
tokens, because that is what a TTS provider emits — `['Pure','red','is','255','0','0',…]`. A cue word
spanning several tokens can therefore *never* match, at any similarity threshold. The prompt asks for
"the single word where the value should appear", and for a value whose `resolved` is `(255, 0, 0)` the
model copied the whole tuple. That is a faithful reading of the instruction and it is unmatchable. The
new tuple/swatch feature is what created the cue words that trip it.

**Why this had to be fixed in Python, not in the matcher.** The tempting fix is a fourth pass in
`findTrigger` that tries each sub-token of the cue. But only the narration can say whether a candidate
token was actually spoken, and the narration is not in the renderer. Repairing in `build_spec` — the
first point where a props tree and its narration are both in hand — also means the repair is *visible
in the IR* and logged, rather than being a silent leniency at render time.

**Resolution, and the subtlety in it.** `repair_cue` reduces a multi-token cue to its **least ambiguous**
token rather than its first. `(255, 0, 0)` contains "255" once and "0" five times; anchoring to "0"
fires on whichever zero came first in the sentence, which is usually not the value's own. Rarest token
wins → "255", which is the word a viewer actually hears.

**And the regression that fix caused, caught one command later.** My first tokeniser split on all
punctuation, which rewrote `8-bit` → `8` and would have rewritten `16,777,216` → `16`. Both of those
cues were *already matching*: a hyphenated compound and a grouped number are each pronounced as one
event and arrive as one trigger. So the repair pass was about to damage two working cues in order to
fix two broken ones. Internal hyphens, commas and dots are now part of a token; only punctuation
*around* a token splits. Verified: `8-bit` and `16,777,216` pass through untouched, the three tuples
repair, and a cue absent from the narration is dropped to `None` rather than left pointing at nothing.

**The lesson.** A repair pass is a rewrite of working data as much as of broken data, and its blast
radius needs the same scrutiny as its correctness. I would not have caught this by reading the diff —
only by running the checker that reports on *all* cues, not just the failing ones.

---

## Dead end 11 — `eslint src` linted zero files, and I had already claimed it clean

**What actually happened.** `npx eslint src` → *"No files matching the pattern 'src' were found"*, exit
2. ESLint 8 resolves only `.js` from a bare directory argument; `--ext .ts,.tsx` is required. A clean
exit from `eslint src` means zero files were linted, not zero problems.

**Why it is recorded here.** The claim "`eslint src` clean" was already written into this log and into
commit `fa28bb8`. It was false. This is the same class of mistake as Dead end 4 — trusting a guard I had
not watched fail — on the same guard, one phase later.

**Resolution.** `--ext .ts,.tsx` (which `npm run lint` in `package.json` already had). Confirmed via
`--format json` that **15 files** are now actually linted, then probed with a file containing
`Math.random()`, `Date.now()` and `new Date()` → **5 errors fired** (`no-restricted-syntax` ×3,
`no-restricted-globals` ×2). The determinism ban is now a guard I have watched fail.

---

## Dead end 12 — The icon pack matched 12 of 14 scenes and was still wrong

**What I expected.** Take a scene's keywords in order of appearance, return the first one with an
icon. The first concrete noun in a segment is usually its subject, so first-position ordering should
land on the thing the scene is about.

**What actually happened.** I printed the per-segment match table before rendering anything. 12/14
scenes matched — a better hit rate than I had estimated to the user — and it was unusable:

```
seg1: desktop   <- 'Computers'      seg1: chart_increasing <- 'Compound'
seg2: eye       <- 'vision'         seg2: gear             <- 'mechanism'
seg3: ruler     <- 'measure'        seg3: calendar         <- 'year'
seg4: desktop   <- 'computer'       seg4: calendar         <- 'years'
seg5: (none)                        seg5: calendar         <- 'decade'
seg6: desktop   <- 'monitor'        seg6: calendar         <- 'years'
seg7: camera    <- 'photograph'     seg7: (none)
```

Two distinct defects that a hit-rate number cannot show.

**Defect 1: the apple never appeared.** Script A's opening segment is *"When you look at an apple, you
see red. A computer, however, only understands numbers."* It says "Computers" before it says "apple",
so first-position ordering picked a monitor. The single thing the user actually asked for — *"the
narration mentions apple but I don't see any red apple"* — was the one thing a 12/14 match rate was
still failing to deliver, and the metric looked fine.

**Defect 2: repetition read as a template artifact.** 🖥 three times in seven scenes, 📅 four times.
The same glyph recurring every other scene stops reading as illustration and starts reading as a bug
in the renderer, which is worse than a plain frame — the same argument as Dead end 7's refusal to
guess a colour, one level up.

**Resolution — two mechanisms, both already used elsewhere in this codebase.**

1. `base.rank_by_rarity` orders a scene's keywords by frequency across the **whole script**, rarest
   first. A word the script says once is what *this* scene is about; a word it repeats throughout is
   background vocabulary. Script A says "computer" four times and "apple" once. This is
   rarest-token-wins from `annotate.repair_cue` (Dead end 10) applied to a different problem — and it
   picks the apple.
2. The provider spends each glyph at most once per video and keeps scanning a scene's remaining
   keywords when its first choice is taken. A second-choice icon that is new beats a first-choice one
   the viewer has already seen.

Result: 12/14 → **12/14**. The hit rate did not move at all; what moved is that every icon is now the
right one and no glyph repeats.

```
seg1: apple     <- 'apple'          seg1: sparkles         <- 'Magic'
seg2: eye       <- 'vision'         seg2: gear             <- 'mechanism'
seg3: ruler     <- 'measure'        seg3: abacus           <- 'arithmetic'
seg4: abacus    <- 'counting'       seg4: calendar         <- 'decade'
seg5: (none)                        seg5: coin             <- 'cent'
seg6: desktop   <- 'monitor'        seg6: chart_increasing <- 'compound'
seg7: camera    <- 'photograph'     seg7: (none)
```

**Why this is the entry worth keeping.** The aggregate metric was identical before and after, so no
amount of staring at 12/14 would have surfaced either defect. Printing the actual per-scene choices
cost one command and caught both — before ~12 minutes of rendering, and before showing the user a
video whose headline feature still didn't do the thing they asked for.

A related trap in the same table, caught by reading the alias lists rather than the output: `power`
mapped to 🔋. *"Two to the eighth power"* is arguably the most likely phrase in an explainer script,
so the single commonest sentence in the corpus would have drawn a battery beside an exponent.
Excluded, along with `right` (usually "right?" or "the right-hand side", not "correct"), `note`,
`drop`, `fall` and `space`. **A word with a dominant non-literal sense cannot earn an icon, however
sensible the mapping looks in a list** — and the vendoring step now warns when two icons claim the
same keyword, which is how the 🌱-vs-📈 collision on "grow" was found.

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
  in `annotate.py` maps it per template. `ComparisonGrid` was originally excluded from
  `IMPLEMENTED_TEMPLATES` for exactly this reason — the flat contract has no notion of columns, and
  faking one would be worse than a `Fallback`. It is now implemented, but as **proportional bars over
  the flat `items[]`**, not as the table the design specified: a comparison is fundamentally about
  relative magnitude, which a flat list of computed numbers already carries, so bars need no new
  annotation shape and read the medium better than a grid of text. Bar widths are geometry only —
  every label on screen is still `value.resolved` verbatim, so a rounding difference in a width can
  never become a wrong number on screen.
