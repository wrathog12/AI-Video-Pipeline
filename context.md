# Automated AI Video Engine — System Architecture & Implementation Specification

**Revision 2.** Supersedes the first draft (kept at `context.original.md`). Every change from v1 is
listed with its rationale in §12 — that section is raw material for the engineering log, which the
assignment weights heavily.

**Instructions for Claude Code:** you are building a fully automated, programmatic video generation
engine. Implement the code, configuration, templates and CLI specified here. No human touches the
artifact between script ingestion and `.mp4` output.

---

## 1. Core Architectural Strategy — The Compiler Engine Pattern

```
script.txt
   │
   ▼  deterministic Python: normalise → sentence split → group into segments
segments[]                                   (NO LLM — see §6 Stage 1)
   │
   ▼  LLM annotates each fixed segment: template_name + props (+ symbolic exprs)
scene_spec.json  ← the inspectable IR (R6). Validated by Pydantic.
   │
   ▼  Python evaluates every symbolic expression → literal display strings (R4)
scene_spec.json (resolved)
   │
   ▼  TTS per scene → PCM WAV + native word timings (R5)
   ▼  Aligner (TTS-native | WhisperX fallback) → word_triggers in MILLISECONDS
scene_spec.json (timed)
   │
   ▼  Remotion renders each scene VIDEO-ONLY (cache-keyed, R8)
   ▼  Python builds ONE continuous frame-aligned PCM track
   ▼  single ffmpeg mux
output.mp4
```

Three invariants hold the whole design together. Violating any one of them is what makes an unseen
script fail visibly at review.

1. **The LLM never computes and never rewrites.** It only *labels* text it is given. Segmentation
   is Python's job; arithmetic is Python's job. The LLM chooses a template and fills props.
2. **The LLM never emits code.** Only JSON conforming to a Pydantic schema.
3. **Timings live in milliseconds in the IR, frames only inside the renderer.** fps is
   configuration (R7); an IR that stores frame numbers has config baked into it.

---

## 2. Requirement Compliance Matrix

| Req | Requirement | Implementation |
|---|---|---|
| **R1** | One command | `run` (bash) + `run.cmd` / `run.ps1` (Windows) → `python_pipeline/main.py`. No GUI, no timeline. |
| **R2** | Unseen script | Deterministic segmentation (topic-agnostic); 6 generic templates with abstract prop shapes; a `Fallback` template so an unmapped scene degrades instead of crashing; zero string constants naming apples, pixels or 256. |
| **R3** | Determinism | **The cache is the guarantee**, not sampling parameters. Spec cached on `SHA256(script + prompt + model_id + schema_version)`. Audio cached on normalised text + voice. Aligner output cached. Fonts bundled locally, Chromium version pinned, `Math.random()`/`Date.now()` banned in TSX. Verified *frame-identical*, not byte-identical, by `verify_determinism.py`. |
| **R4** | Computed values | LLM emits **symbolic expressions** (`{"expr": "2**8", "format": "int"}`); Python evaluates and formats. OCR gate asserts each resolved string is actually legible on a rendered frame. |
| **R5** | Word sync ±150 ms | Primary: TTS-native word boundaries (edge-tts `WordBoundary`, ElevenLabs char timestamps). Fallback: WhisperX forced alignment behind the same `Aligner` interface. Numerals/symbols normalised before alignment and mapped back to original tokens. |
| **R6** | Inspectable IR | `scene_spec.json` with `schema_version`, per-scene `start_ms`/`duration_ms`, `assets`, and a `provenance` block. `--spec` re-renders from an edited spec without re-invoking the LLM. |
| **R7** | Swappable components | Four interfaces: `TTSProvider`, `Aligner`, `AssetProvider`, `Renderer`. Selected in `config.yaml`. `AssetProvider` has **two** live implementations (`null`, `icon_pack`) because an interface with one is not a seam; `TTSProvider` has `edge-tts` live and `cartesia` written but untested; `Aligner` and `Renderer` currently have one each. Resolution, aspect ratio (16:9 **and** 9:16), palette, typography are all config. |
| **R8** | Incremental re-render | Two-tier content-addressed cache (audio, render) keyed on decoded PCM + theme + dimensions + fps. `--explain-cache` prints per-scene hit/miss and the reason for each miss. |

---

## 3. Project Directory Structure

Everything lives at the repository root — there is no wrapper directory, so a clone *is* the
project. Files marked ✓ exist; unmarked ones are planned.

```
.
├── run                        ✓ # bash entry point (R1)
├── run.cmd                    ✓ # Windows shim — same CLI surface
├── run.ps1                      # 0 bytes, NOT written (run.cmd covers Windows)
├── config.yaml                ✓ # master configuration (R7)
├── requirements.txt           ✓ # pinned; optional extras commented out
├── .env.example               ✓ # key names only — .env itself is gitignored
├── Dockerfile                   # 0 bytes, NOT written — see §10
├── docker-compose.yml           # 0 bytes, NOT written — see §10
├── README.md                    # clone → video on a clean machine — 0 bytes, NOT written
├── context.md                 ✓ # this document
├── ENGINEERING_LOG.md         ✓ # append-only; dead ends as they happen
│
├── python_pipeline/
│   ├── main.py                ✓ # orchestrator + CLI (8 stages)
│   ├── segmenter.py           ✓ # DETERMINISTIC scene splitting (no LLM)
│   ├── schema.py              ✓ # Pydantic IR + the flat annotation contract
│   ├── annotate.py            ✓ # Annotation → per-template props; reconciliation
│   ├── llm_annotator.py       ✓ # Gemini (default) + Claude, dispatched by prefix
│   ├── heuristic_annotator.py ✓ # rule-based fallback if the LLM is unreachable
│   ├── evaluator.py           ✓ # AST-whitelist evaluation + formatting (R4)
│   ├── env.py                 ✓ # .env loading; returns key NAMES, never values
│   ├── cache.py               ✓ # content-addressed cache (spec/audio/align/render)
│   ├── audio_track.py         ✓ # frame-aligned continuous PCM assembly
│   ├── renderer.py            ✓ # Remotion invocation (Renderer interface)
│   ├── mux.py                 ✓ # single final ffmpeg mux
│   ├── verify_determinism.py  ✓ # frame + sample hash comparison of two runs (R3)
│   ├── cue_check.py           ✓ # every cue_word resolves to a trigger (R5)
│   ├── vendor_fonts.py        ✓ # one-time OFL font fetch → public/fonts + manifest
│   ├── qa_ocr.py                # R4 legibility gate — stub, off by default
│   ├── server/                  # the dashboard (`./run --serve`) — see §13
│   │   ├── app.py             ✓ # FastAPI routes; comment-preserving config writes
│   │   ├── runs.py            ✓ # subprocess supervision, stdout → stage timeline
│   │   ├── settings.py        ✓ # the editable whitelist + availability probes
│   │   └── static/            ✓ # index.html, settings.html, *.js, style.css
│   │                            #   no build step, no bundler, no node_modules
│   ├── tts/
│   │   ├── base.py            ✓ # TTSProvider interface
│   │   ├── edge.py            ✓ # edge-tts (+ native WordBoundary timings)
│   │   ├── cartesia.py        ✓ # Sonic over SSE, add_timestamps (untested: no key)
│   │   ├── piper.py             # local/offline provider
│   │   └── elevenlabs.py        # char-level timestamps
│   ├── align/
│   │   ├── base.py            ✓ # Aligner interface
│   │   ├── native.py          ✓ # timings straight from the TTS provider
│   │   └── whisperx.py          # forced-alignment fallback
│   └── assets/
│       ├── base.py            ✓ # AssetProvider interface + keyword extraction
│       ├── null.py            ✓ # returns nothing; templates degrade gracefully
│       ├── icon_pack.py       ✓ # local SVG lookup by keyword (default)
│       └── vendor_icons.py    ✓ # one-time Noto Emoji fetch + SHA-256 manifest
│
├── remotion_engine/
│   ├── package.json           ✓
│   ├── remotion.config.ts     ✓
│   ├── .eslintrc.cjs          ✓ # the wall-clock ban — verified to actually fire
│   ├── public/fonts/          ✓ # 3 vendored OFL variable fonts + manifest.json
│   ├── public/icons/          ✓ # vendored SVGs + manifest.json; staticFile() root
│   └── src/
│       ├── Root.tsx           ✓
│       ├── SceneDispatcher.tsx ✓ # template_name → component, Fallback on miss
│       ├── fonts.ts           ✓ # FontFace + delayRender; theme.font_family is real
│       ├── theme.ts           ✓ # theme + orientation from injected props
│       ├── types.ts           ✓ # mirrors schema.py, incl. cue_word
│       ├── templates/
│       │   ├── TitleCard.tsx      ✓
│       │   ├── KeyValuePanel.tsx  ✓
│       │   ├── ExpressionCard.tsx ✓
│       │   ├── BigNumber.tsx      ✓ # fitFactor shrinks the hero so digits can't clip
│       │   ├── ProcessSteps.tsx   ✓
│       │   ├── Fallback.tsx       ✓ # narration + safe generic layout
│       │   └── ComparisonGrid.tsx ✓ # proportional bars over flat items — see below
│       └── components/
│           ├── WordCue.tsx     ✓ # ms → frame conversion happens HERE
│           ├── ValueBlock.tsx  ✓ # shared value rendering; cue anchoring lives here
│           ├── SwatchStrip.tsx ✓ # paints Value.channels; null when nothing is drawable
│           ├── SceneIcon.tsx   ✓ # one out-of-flow glyph per scene; null on load failure
│           └── CountUp.tsx     ✓ # animates plain numbers only; rests on `resolved`
│
├── scripts/
│   ├── script_a.txt           ✓ # the assignment's script
│   └── script_b.txt           ✓ # unseen-topic script for the R2 rehearsal
├── output/
└── .cache/
    ├── spec/                    # annotation results
    ├── audio/                   # PCM WAV per (text, provider, voice, rate)
    ├── align/                   # aligner output per audio hash
    └── scenes/                  # video-only scene MP4s
```

`ComparisonGrid.tsx` is implemented, but **not as the table this document originally specified**. The
flat annotation contract (§5) has no notion of columns and rows, so an annotator cannot fill a grid
honestly, and a grid stuffed with mislabelled cells is worse than a `Fallback`. It instead renders the
flat `items[]` as **proportional horizontal bars**: a comparison is fundamentally a claim about
relative magnitude, which a flat list of computed numbers already carries, so this needs no new
annotation shape at all.

Bar widths are geometry and nothing else. No new figure is derived and none is displayed — every label
on screen is `value.resolved` verbatim, so a rounding difference in a bar width can never become a
wrong number on screen. The peak is computed over comparable values only, and the accent colour goes
to the peak *index derived from the data*, never to a hardcoded position. A non-numeric value gets a
label and no bar rather than a guessed one.

---

## 4. Configuration Schema (`config.yaml`)

Note the explicit `width`/`height`. `resolution: "480p"` plus `aspect_ratio` was ambiguous in v1 —
480p in 9:16 is a different pixel count than 480p in 16:9. Design at a fixed canvas and use
Remotion's `--scale` to emit 480p.

```yaml
schema_version: 2

project:
  fps: 30
  # Canvas is authoritative. Aspect is derived from it, not the reverse.
  width: 1920
  height: 1080
  # Output scale factor applied at render time (0.25 * 1920x1080 => 480x270;
  # 0.444 => 854x480). Set to 1.0 for full-res delivery.
  output_scale: 0.4445
  # "landscape" | "portrait" — derived from width/height, exposed to templates
  # as a token so layouts can flip flex direction rather than guess.
  orientation: auto

# Portrait preset — select with `./run --profile portrait`
profiles:
  portrait:
    project:
      width: 1080
      height: 1920

theme:
  font_family: "Inter"          # resolved against remotion_engine/fonts/
  type_scale_base_vmin: 3.2     # all type sizes are multiples of vmin, not px
  min_font_px: 22               # hard floor; enforced at 480p for R4 legibility
  primary_color: "#E63946"
  secondary_color: "#4EA8DE"
  background_color: "#0D1117"
  text_color: "#F8F9FA"

segmentation:
  target_seconds: 12.0          # aim per scene
  min_seconds: 4.0
  max_seconds: 20.0
  words_per_minute: 165         # duration estimate used to group sentences

providers:
  llm: "gemini-2.5-flash"       # annotation only; dispatched by vendor prefix
  tts: "edge-tts"               # edge-tts | piper | elevenlabs
  aligner: "native"             # native | whisperx
  assets: "icon_pack"           # null | icon_pack — `null` is a real toggle, not a degraded mode
  renderer: "remotion"

tts:
  voice: "en-US-AriaNeural"
  rate: "+0%"

determinism:
  # Cache is the determinism mechanism (see §7). This forces a hard failure
  # instead of a silent re-roll if a cache entry is missing during a verify run.
  require_cache: false
  ban_wallclock: true

qa:
  ocr_gate: true                # assert computed strings appear on a real frame
  ocr_min_confidence: 60
```

---

## 5. Scene Specification Schema (`scene_spec.json`)

The IR after all stages have run. Every field a human might want to edit is present, and editing it
and re-running `--spec` changes the video (R6).

```json
{
  "schema_version": 2,
  "project_title": "How Computers See Color",
  "provenance": {
    "script_sha256": "8c1f…",
    "annotator": "gemini",
    "llm_model": "gemini-2.5-flash",
    "prompt_sha256": "b0a3…",
    "tts_provider": "edge-tts",
    "tts_voice": "en-US-AriaNeural",
    "aligner": "native",
    "fps": 30,
    "width": 1920,
    "height": 1080,
    "theme_sha256": "5d77…",
    "generated_at_utc": "2026-08-02T09:14:07Z"
  },
  "transitions": [
    { "after_scene": "scene_03", "type": "fade", "duration_ms": 400 }
  ],
  "scenes": [
    {
      "scene_id": "scene_04",
      "template_name": "ExpressionCard",
      "narration_text": "Pure red is (255, 0, 0). If you turn all the lights off to (0, 0, 0), you get pitch black.",
      "start_ms": 41200,
      "duration_ms": 9800,
      "props": {
        "title": "Colour as coordinates",
        "expression": {
          "label": "Pure red",
          "expr": "(255, 0, 0)",
          "format": "tuple",
          "unit": null,
          "cue_word": "255",
          "resolved": "(255, 0, 0)",
          "channels": [255.0, 0.0, 0.0]
        },
        "items": [
          {
            "label": "All channels off",
            "expr": "(0, 0, 0)",
            "format": "tuple",
            "unit": null,
            "cue_word": "black",
            "resolved": "(0, 0, 0)",
            "channels": [0.0, 0.0, 0.0]
          }
        ]
      },
      "assets": [],
      "word_triggers": [
        { "word": "red",   "start_ms": 940,  "end_ms": 1180 },
        { "word": "255",   "start_ms": 1740, "end_ms": 2410 },
        { "word": "black", "start_ms": 8020, "end_ms": 8460 }
      ],
      "derived_from": {
        "audio_pcm_sha256": "1a9e…",
        "narration_sha256": "77bc…"
      }
    },
    {
      "scene_id": "scene_05",
      "template_name": "BigNumber",
      "narration_text": "256 times 256 times 256 equals 16,777,216.",
      "start_ms": 51000,
      "duration_ms": 8200,
      "props": {
        "title": "Total addressable colours",
        "expression": {
          "label": "256 × 256 × 256",
          "expr": "256**3",
          "format": "thousands",
          "unit": null,
          "cue_word": "16,777,216",
          "resolved": "16,777,216",
          "channels": []
        },
        "items": []
      },
      "assets": [],
      "word_triggers": [],
      "derived_from": { "audio_pcm_sha256": "…", "narration_sha256": "…" }
    }
  ]
}
```

**Field notes**

- `expr` is what the LLM produced. `resolved` is what Python computed. A reviewer can see both, and
  can edit `expr` and re-run the render stage to watch the number change — a clean R4 + R6 demo.
- `cue_word` names **which narrated word this value is anchored to**, and the annotator supplies it.
  The alternative — having the template fuzzy-match `resolved` against the word triggers — fails
  exactly where it matters: narration says "sixteen point eight million" while the display says
  `16.8`, so the string that is on screen is not the string that was spoken. Naming the spoken token
  explicitly is the only version that survives. A cue that matches nothing is *not* fail-safe:
  `useCueProgress` treats a missing trigger as "show immediately", so the element silently ignores
  the audio. `cue_check.py` exists to make that countable rather than invisible.
- **A cue word must be a single token, and `build_spec` enforces that** via `repair_cue`.
  `word_triggers` are single tokens because that is what a TTS provider emits, so a multi-token cue
  can never match at any threshold — and a model asked to name the word for a value whose `resolved`
  is `(255, 0, 0)` will copy the whole tuple, faithfully and unmatchably. The repair reduces such a
  cue to its **least ambiguous** token (rarest in the narration, earliest on a tie), which lands on
  `255` rather than on the first of five zeroes. A cue absent from the narration becomes `None`.
  This lives in Python and not in `findTrigger` because only the narration can say whether a candidate
  token was spoken, and the narration is not available to the renderer.
- `word_triggers` are **milliseconds relative to the scene's own audio**, and are invalidated when
  `derived_from.narration_sha256` no longer matches `narration_text`. Editing narration in the spec
  therefore re-aligns only that one scene.
- `transitions` sit *between* scenes, not on them. A `transition_type` field on a scene has no
  well-defined meaning for the last scene and no second clip to blend with.
- `assets` is present even when empty so the `AssetProvider` interface is visible in the IR (R7), and it
  is empty in *both* examples above for a real reason rather than because the feature is unbuilt: the
  `icon_pack` provider only matches a narration word that appears in a hand-curated catalog, and neither
  "Pure red is (255, 0, 0)" nor "256 times 256 times 256" contains one. A populated entry looks like
  this, from `scene_01` of Script A:

  ```json
  "assets": [
    { "kind": "svg", "id": "apple", "path": "icons/apple.svg", "cue_word": "apple" }
  ]
  ```

  `path` is **renderer-relative**, resolved with Remotion's `staticFile()` against
  `remotion_engine/public/`. Never absolute: an absolute path would bake this machine's directory layout
  into an artifact whose whole point is being re-renderable elsewhere.

  `cue_word` is the narration word that *selected* the asset, spelled as the narration spells it, so the
  icon can be revealed as it is spoken (R5) using the same `word_triggers` machinery as a value. This is
  why keyword extraction hands back surface forms rather than normalised keys — the aligner reports the
  token the voice actually emitted, and normalisation happens on the lookup side instead. An icon cued on
  a word no trigger contains would not fail visibly; it would appear at frame 0 and silently ignore the
  audio.

  Keywords are extracted in **Python, not by the LLM**. Asking the annotator for them would change the
  prompt (bumping `PROMPT_VERSION` and invalidating every cached annotation on disk), make icon choice
  model-dependent (importing R3's problem into the visual layer), and buy nothing — the narration already
  contains the nouns, and matching them against a fixed index is deterministic and free.

  Matching is exact-after-normalisation, with at most a trivial `-s` strip. No stemming, no edit distance,
  no embeddings: **a wrong icon is worse than no icon**, because an apple beside a segment about apples
  reads as illustration while an apple beside a segment about compound interest reads as a bug and
  undermines the numbers next to it. Each of those techniques buys coverage by trading away the property
  that a match means something. Words with a dominant non-literal sense are excluded from the catalog
  entirely for the same reason — `power` earns no battery, because an explainer that says it usually
  means "two to the eighth power".

  At most **one** asset per scene, and each glyph is spent at most once per video. Both caps are provider
  arguments rather than template logic. An icon is a *label on the content*, not the content — a swatch
  strip encodes a value, an apple glyph annotates a topic — and a glyph recurring every other scene stops
  reading as illustration and starts reading as a template artifact.
- `channels` is filled by the **evaluator**, never by the annotator, and holds the numeric components
  of a tuple result. It exists so that a component which *draws* a value — a colour swatch, a bar —
  reads structured numbers rather than parsing them back out of `resolved`. Turning `"(255, 0, 0)"`
  into three integers inside a template would be arithmetic in the renderer, which is exactly the R4
  boundary the `expr`/`resolved` split exists to hold. `channels_of()` returns `[]` for anything that
  is not a tuple of finite numbers, so a partially-numeric tuple degrades to "not drawable" instead of
  to a half-filled list.
- An **unresolvable value is dropped from the tree**, not left with an empty `resolved`. A blank box on
  screen reads as a rendering bug; an absent one reads as "this scene has less to show". Each drop is
  logged by name, and a value-led template left with nothing is downgraded to `Fallback`. This is what
  keeps one algebraic expression from an LLM (`P * (1.07)**N`) from failing the whole run — see Dead
  end 9 in the engineering log.
- `provenance.engine_sha256` fingerprints the renderer's own sources. Without it the render cache keys
  on props alone, so editing a component serves the previous run's frames and the edit looks broken
  rather than cached.

### Generic prop shapes

Templates must never receive topic-specific props. `channel_values: {r, g, b}` is a Script-A trap.
The universal shapes:

| Template | Props |
|---|---|
| `TitleCard` | `{ title, subtitle? }` |
| `KeyValuePanel` | `{ title, items: [{label, expr?, resolved, unit?}] }` |
| `ExpressionCard` | `{ title, expression: {label, expr, format, resolved}, steps?: [...], items? }` |
| `BigNumber` | `{ title, expression, caption? }` |
| `ComparisonGrid` | `{ title, items: [...], caption? }` — same flat shape as `KeyValuePanel`; the template scales them into bars |
| `ProcessSteps` | `{ title, steps: [{label, detail?}] }` |
| `Fallback` | `{ title?, items? }` — renders narration-derived text on the theme background |

Note that `ComparisonGrid` shares `KeyValuePanel`'s prop shape exactly. That is the point: adding a way
to *display* a comparison required no new way to *describe* one.

`SwatchStrip` reads `Value.channels` and classifies by **arity and range**, not by topic — 3 channels
in 0–255 → RGB, 4 in 0–100 → CMYK, 1 → greyscale ramp, and anything else → proportional bars. The last
branch is the load-bearing one: `[1967, 7612]` from the compound-interest script has two channels, and
guessing a colour from two arbitrary numbers would paint a confidently wrong swatch beside a correct
number — which reads as the *number* being wrong. `SwatchStrip` returns `null` when no value has
channels, so no template needs to know whether the current script is about colour.

`CountUp` animates a figure to its computed value under two rules. The final frame renders
`value.resolved` **verbatim**, never a re-derived string, so the resting state is always Python's truth.
And a value that is not a plain number — a tuple, a range, text — is **never animated**, because a
half-interpolated tuple is precisely the mangled-digit failure R4 names. Its comma grouping is
hand-rolled rather than `toLocaleString`, since locale resolution depends on the host environment and
would make frame output machine-dependent (R3).

---

## 6. Execution Pipeline Implementation Guide

### Stage 1 — Deterministic segmentation (`segmenter.py`) — **no LLM**

1. Normalise whitespace; preserve the source text byte-for-byte otherwise.
2. Split into sentences (regex on `.?!` with abbreviation guards; do not use an ML sentence model —
   it is another non-deterministic dependency).
3. Greedily group sentences into segments, estimating duration as
   `words / (words_per_minute / 60)`, respecting `target/min/max_seconds`.
4. **Hard fidelity gate.** Assert that concatenating every `narration_text`, modulo whitespace,
   reproduces the input script exactly. On mismatch, raise and abort with a diff. This is the check
   that prevents a paraphrasing LLM from making the TTS speak words the author never wrote.

Why not let the LLM segment: scene count would become model-dependent, breaking R3 reproducibility
and busting every cache key; and any dropped clause or smart-quote substitution silently changes
the narration.

### Stage 2 — LLM annotation (`llm_annotator.py`)

Input: the fixed segment list. Output one `Annotation` per segment, nothing else.

**The contract is flat, not per-template.** The model returns a single uniform shape
(`template_name`, `title`, `subtitle?`, `caption?`, `headline?`, `items[]`, `steps[]`, `reasoning`)
and `annotate.build_props` maps it to each template's prop layout in Python. Asking the model to emit
seven different nested shapes gives seven ways to be subtly wrong, makes prop layout — a renderer
concern — part of the model's job, and means adding a template invalidates every cached annotation.

- Default: **Gemini** (`gemini-2.5-flash`) via `google-genai`, with a hand-written
  `RESPONSE_SCHEMA`. Gemini's `responseSchema` is a restricted OpenAPI subset: use `nullable: true`
  rather than `anyOf` with null, and `propertyOrdering` is the only way to control field order —
  which matters, because `reasoning` must be ordered *before* `template_name` so the model commits to
  a justification before a choice.
- Dispatch is by **vendor prefix** (`llm_annotator.get_annotator`), so any `gemini-*` or `claude-*`
  model id works with no code change, and `heuristic` runs the offline annotator with no key at all.
  Two live vendors is what makes the R7 "swappable" claim demonstrable rather than asserted.
- **`temperature` and `seed` are vendor-specific.** Gemini accepts both, so they are sent — a real
  cold-cache determinism lever. `claude-opus-5` rejects `temperature` with a 400, so the Claude path
  omits it. Either way the cache (§7) remains the actual R3 guarantee; sampling parameters are a
  nice-to-have on top of it.
- `reconcile()` repairs what the model gets structurally wrong — duplicate segment indices,
  out-of-range indices, unannotated segments, unimplemented templates, empty content — and returns
  every repair as a warning that the run logs. The pipeline does not abort on a bad annotation; it
  degrades and says so.
- `narration_text` is copied from the **segment**, never from the annotation. That is the structural
  half of the no-rewrite invariant: even a model that paraphrases cannot change what the TTS speaks.
- Prompt rules, verbatim in the system prompt:
  - You are given segments. Do not merge, split, reword, or re-order them.
  - Never compute a numeric result. Emit `{"expr": "...", "format": "..."}` and let the caller
    evaluate it. `expr` must be a pure arithmetic/tuple expression in Python syntax.
  - Choose exactly one `template_name` from the enumerated list. If nothing fits, choose
    `Fallback`.
  - Emit no code, no markdown, no commentary.
  - **Every expression must be fully numeric — no variable names, ever.** The prompt shows the wrong
    and right forms side by side (`P * (1.07)**N` vs `1000 * (1.07)**30`), because "no names" alone was
    demonstrably too weak: narration that states a general formula gets transcribed as algebra, which
    the evaluator then refuses. A general formula belongs in the *label* as prose; a segment with no
    concrete figures should be `Fallback`.
  - A tuple whose three components are 0–255 is **painted as an actual colour swatch**, so a segment
    describing a colour by its components should emit `expr: "(255, 0, 0)", format: "tuple"`. This is
    the one place the prompt mentions a visual consequence, and it is phrased as a property of the
    data rather than as a layout instruction — the model still never specifies colours or positions.
- `PROMPT_VERSION` feeds the annotation cache key, so **any** prompt or schema edit must bump it.
  Otherwise a stale key serves annotations produced by the old prompt and the change appears to have
  had no effect. Currently 5.
- Cache the whole annotation on `SHA256(script + prompt + model_id + schema_version)`.
- If the API is unreachable (no key, no network — a real live-review risk), fall back to
  `heuristic_annotator.py`: regex-detect numerals, powers, ranges and tuples; pick
  `ExpressionCard`/`BigNumber` when a numeric relation is present, `KeyValuePanel` when a
  colon-delimited definition is present, else `Fallback`. The demo always produces *a* video.

### Stage 3 — Symbolic evaluation (`evaluator.py`)

This stage, not the LLM, is what satisfies "computed, not authored".

- Parse `expr` with `ast.parse(mode="eval")` and walk the tree, permitting only
  `Expression, BinOp, UnaryOp, Constant, Tuple, List, Add, Sub, Mult, Div, FloorDiv, Pow, Mod,
  USub, UAdd`. Reject anything else — no names, no calls, no attributes, no subscripts. (`sympy` is
  an acceptable alternative but is a heavy dependency for what a 40-line whitelist walker does.)
- **Annotator output is untrusted input by construction**, so the limits are real bounds and not
  sanity checks: `MAX_EXPR_CHARS`, `MAX_NODES`, `MAX_RESULT_BITS`, `MAX_FACTORIAL`. Crucially
  `_check_pow` decides via **bit-length arithmetic**, so `2**10**9` is rejected without ever being
  computed — a magnitude cap that has to evaluate the expression first is not a cap.
- **A `Value` with a bare number in `resolved` and no `expr` is rejected.** That refusal *is* the R4
  gate: it makes "authored rather than computed" a hard failure instead of a convention. Non-numeric
  strings (`0-255`, `RGB`, `8-bit`) pass, because they are labels, not results.
- `--explain-values` prints every resolution as a table — expression, format, computed result — which
  is how R4 gets demonstrated in the review without opening the JSON.
- Formatters: `int`, `thousands` (`f"{v:,}"`), `float:N`, `tuple`, `range` (`"0–255"` with an en
  dash), `percent`, `raw`.
- Write the result into `props…resolved` and freeze it. Templates render `resolved` and never
  compute. A tuple result additionally keeps its components in `channels`, because a component that
  paints a swatch or sizes a bar must not parse numbers back out of the display string.
- **An expression the walker refuses costs that one value, not the run.** Refusing unsafe input and
  failing the whole render on unsafe input are separate decisions, and only the first is required. An
  LLM handed narration that states a general formula will occasionally transcribe it as algebra
  (`P * (1.07)**N`); the value is dropped from the tree, the drop is logged by name, and a value-led
  template left with nothing is downgraded to `Fallback`. R2 requires an unseen script to produce a
  video, and this is the failure mode most likely to be phrasing-dependent — i.e. the one that would
  break unpredictably. The prompt separately forbids symbolic expressions, which is what prevents the
  drop; this is what survives it.

### Stage 3.5 — Visual assets (`assets/`) — **no LLM**

Numbered 3.5 rather than renumbering everything after it, because it is genuinely a half-stage: it
decorates an already-complete IR and every scene renders without it.

- For each scene, `base.keywords_of(narration_text)` lifts candidate nouns (stopworded, deduplicated on
  the normalised form, capped), then `base.rank_by_rarity(keywords, script_text)` re-orders them so the
  word that is rarest **in the whole script** comes first, and the provider resolves that list.
- **Rarity ordering, not first-appearance.** Same argument as rarest-token-wins in `repair_cue`, applied
  to a different problem: a word the script says once is what *this* scene is about, while a word it
  repeats throughout is background vocabulary. Script A says "computer" four times and "apple" once, so
  first-position ordering put a monitor on the opening scene and never drew the apple — which was the
  original complaint the whole stage exists to answer. It also thins repetition out on its own, since a
  word that ranks last in every scene mentioning it stops being three scenes' answer.
- `resolve` is therefore **stateful and order-dependent** — the same scene resolves differently depending
  on what came before it. Safe here because scene order is a pure function of the script, so a run is
  reproducible, which is what R3 actually asks. It does mean the provider is per-run rather than a shared
  singleton; `get_asset_provider` builds a fresh one each time.
- **Runs on the script path only, never under `--spec`.** A spec handed in with `--spec` is rendered
  exactly as written — R6's promise is that the artifact is the contract, so a hand-edited `assets: []`
  must stay empty rather than being helpfully refilled.
- The glyphs are **committed to the repository**, not fetched at render time, for the same reason as the
  bundled fonts: a frame must be a function of the repo, not of GitHub's availability.
  `vendor_icons.py` is a one-time fetch that writes `remotion_engine/public/icons/` plus a
  `manifest.json` of per-file SHA-256s, so the committed bytes are auditable. It writes straight into the
  renderer's static directory rather than copying between two trees — two directories would be one more
  place for provider and renderer to disagree, and they would disagree *silently*, as a scene whose
  `AssetRef` resolves but whose image 404s.
- A catalog keyword with no file on disk resolves to nothing, so an un-vendored checkout renders exactly
  as it does under `null`. A missing pack must not be a render error, by the same R2 reasoning that says
  an unseen script still produces a video.
- `assets` is a component of `render_key` (§5 of Stage 5), because switching `providers.assets` changes
  what is on screen without touching a single prop. Omit it and the cache serves icon-free frames, making
  the new provider look inert.

### Stage 4 — TTS and alignment

**TTS** (`tts/`). One WAV per scene, decoded to canonical PCM (16-bit, 24 kHz, mono) before
hashing. Cache key: `SHA256(normalized_text + provider + voice + rate)` — *not* the WAV file bytes,
which carry container headers that differ between runs and would bust every entry.

**Alignment** (`align/`). Two implementations behind one interface, which doubles as concrete R7
evidence:

- `native.py` — **the primary path.** `edge-tts` emits `WordBoundary` events with offsets;
  ElevenLabs returns character-level timestamps. These come from the synthesiser itself, so they
  are exact by construction and cost nothing extra. Verify this works on day one.
- `whisperx.py` — fallback. Because the transcript is known, load **only** the wav2vec2 alignment
  model, not Whisper ASR: skips a ~3 GB download and most of the runtime. Force CPU and
  `torch.use_deterministic_algorithms(True)`; cache the alignment JSON keyed on the PCM hash.

**Numeral and symbol normalisation.** The narration says "two hundred fifty-five" but the script
says `255`; it says "to the eighth power" but the script says `2⁸`; it says "times" for `×`. Before
alignment, expand numerals with `num2words` and expand a symbol table (`× → times`,
`⁸ → to the eighth power`, `– → to`), keeping an index map from expanded token back to the original
token. Emit `word_triggers` against the *original* tokens so a reviewer scrubbing to `255` finds
the right frame. Without this, WhisperX mis-aligns exactly the tokens the assignment says it will
check.

Store triggers in **milliseconds**. `WordCue.tsx` converts to frames with `useVideoConfig().fps`.

### Stage 5 — Caching (`cache.py`)

Two tiers, both content-addressed:

| Cache | Key | Purpose |
|---|---|---|
| audio | `(normalized_text, provider, voice, rate)` | Editing scene 3's visuals must not re-synthesise its narration. |
| render | `(template_name, resolved_props, audio_pcm_sha256, theme_sha256, fps, width, height, output_scale)` | Editing scene 3 re-renders scene 3 only. |

`theme_sha256`, `fps` and the dimensions **must** be in the render key. Omitting them serves a stale
scene when the palette or aspect ratio changes — and "change the palette in config and re-run" is a
natural thing for a reviewer to try.

`--explain-cache` prints a table: scene id, hit/miss, and for a miss, which key component changed.
That is the R8 demonstration.

### Stage 6 — Render (`renderer.py`) — video only

Remotion renders **no audio**. Each scene MP4 is silent video at the exact frame count implied by
its `duration_ms`.

Determinism obligations inside `remotion_engine/`:

- Fonts bundled in **`remotion_engine/public/fonts/`** and registered by `src/fonts.ts` (`FontFace`
  + `document.fonts.add`, wrapped in `delayRender`). A Google Fonts fetch — including
  `@remotion/google-fonts` — is both a network dependency at render time and a nondeterminism
  source, so the three OFL families are fetched **once** by `python -m python_pipeline.vendor_fonts`
  and committed with a SHA-256 per file. Three details here were each learned the hard way:
  - **`public/`, not the sibling `fonts/` this document originally specified.** With no
    `@remotion/fonts` package installed, the only way to reach a local file from inside the bundle is
    `staticFile()`, which resolves against `public/`. A font written anywhere else is a font the
    renderer cannot open — and it fails *silently*, as a fallback family that still looks like text.
  - **`delayRender` is load-bearing.** Remotion screenshots a frame as soon as React paints, so a
    font that resolves milliseconds later would land in some frames and not others: an R3 failure
    that presents as a rendering glitch rather than as a missing font.
  - **`ensureFontsLoaded()` is called in the component body, not in an effect.** An effect runs after
    React has committed, by which point frame 0 may already be captured with fallback typography.
  For three phases `theme.font_family` was a config key that nothing read, and the failure was
  invisible because a missing family degrades to readable fallback text instead of erroring. See
  `ENGINEERING_LOG.md`.
- `Math.random()`, `Date.now()`, `new Date()` and `performance.now()` are banned — add an ESLint
  rule so the ban is enforced, not aspirational. Every animation is a pure function of
  `useCurrentFrame()`.
- Pin the Chrome Headless Shell version in `remotion.config.ts`; record it in `provenance`.
- `SceneDispatcher.tsx` looks up `template_name` in a registry and mounts `Fallback` on a miss.
  Never throw — a crash on an unmapped template is exactly the R2 failure mode.
- Layout: `theme.ts` exposes `orientation` and a vmin-based type scale. Templates set flex
  direction from `orientation` rather than assuming a wide canvas. Clamp every computed font size
  to `min_font_px`. CI smoke-renders one frame at both 1920×1080 and 1080×1920.
- **A scene icon is absolutely positioned decoration and nothing else** (`components/SceneIcon.tsx`).
  If it sat in flow, whether a keyword happened to match would change where the text sits, so the same
  script would compose differently under `null` than under `icon_pack` — destroying the property that
  every template looks complete with `assets: []`. `rootStyle` carries `position: 'relative'` so the
  glyph is placed against its own scene frame rather than Remotion's `AbsoluteFill`. It is small, in a
  fixed corner per template, desaturated and under full opacity, and revealed on its `cue_word` — never
  a large centred glyph, because Noto colour emoji are glossy and cartoon-rounded and would fight the
  typography at any size that reads as content.
- **A failed image load renders nothing rather than failing the video.** Remotion's `<Img>` treats a load
  error as a render error, so one missing SVG would mean no output at all — the exact R2 failure. An
  `onError` handler drops the icon instead. The Python provider already checks the file exists, but a
  spec can be rendered with `--spec` on a machine whose pack was never vendored.

### Stage 7 — Audio track assembly (`audio_track.py`)

Build **one continuous PCM track** for the entire video:

1. For each scene, take its decoded PCM.
2. Pad with silence to exactly `round(duration_ms / 1000 * fps)` frames' worth of samples, so audio
   and video boundaries are frame-aligned by construction.
3. Concatenate in the sample domain and write a single WAV.

This replaces v1's per-scene-audio + `concat -c copy`. Concatenating AAC streams inserts ~20 ms of
encoder priming at every boundary; across ~15 scenes that accumulates past the ±150 ms R5 budget and
produces audible clicks. Encoding audio once, at the end, makes global sync structural rather than
something to hope for.

### Stage 8 — Transitions and final mux (`mux.py`)

**Decide one of these and log the trade-off** — v1 specified `transition_type: "slide"` alongside
`-c copy`, which is not implementable: stream copy has nothing to blend into, and `xfade` requires
re-encoding *and* shortens total duration, invalidating every downstream frame number.

- **Option A (default, recommended).** Intra-scene fade in/out rendered inside Remotion. Costs
  nothing, keeps `-c copy` for video concat, keeps durations exact.
- **Option B.** A separate cached stitch stage that applies `xfade` over overlapping regions and
  compensates each scene's `duration_ms` for the overlap. More expensive, re-encodes, but supports
  real cross-fades.

Final assembly, once:

```
ffmpeg -f concat -safe 0 -i concat_list.txt -i track.wav \
       -map 0:v -map 1:a -c:v copy -c:a aac -b:a 128k -shortest out.mp4
```

Write `concat_list.txt` with forward-slash paths relative to the list file — `-safe 0` plus Windows
absolute paths (`C:\…`) is a known breakage.

### Stage 9 — QA gate (`qa_ocr.py`)

For each scene with a `resolved` value, extract one frame at a trigger point and OCR it
(`pytesseract`). Assert the resolved string appears verbatim. This catches the realistic R4 failure
— text overflowing, clipping, or becoming illegible at 480p — which no schema validation can see.
Fail the run loudly; a silently unreadable number is worse than a crash.

### Stage 10 — Determinism verification (`verify_determinism.py`)

Do **not** claim byte-identical MP4s. Muxer metadata and encoder builds make that fragile and it is
not what "the same video" means. Instead: decode both runs to raw frames, hash each frame, and
compare the sequences plus the decoded audio PCM hash. Report the first differing frame index. Ship
this so the answer to "your two md5s differ" is a command, not an argument.

---

## 7. Determinism — the honest account

The v1 doc claimed determinism via "LLM temperature: 0.0". That claim is wrong twice over:

1. Hosted LLM inference is not bit-reproducible even at temperature 0 — request batching, MoE
   routing and floating-point non-associativity all perturb logits.
2. `temperature` is not even an accepted parameter on every model. Gemini takes it (and `seed`);
   `claude-opus-5` returns a 400. So the "just set temperature to 0" answer is not portable across
   the vendors R7 requires us to support.

R3 explicitly permits caching as the constraint mechanism, so state it plainly:

| Component | Non-determinism source | Constraint |
|---|---|---|
| LLM annotation | Inference non-reproducibility | Content-addressed cache on `(normalised script, prompt fingerprint, model_id, schema_version)`. The second run reads the same spec and makes no request. `temperature=0` + `seed` are sent where the vendor accepts them, as a cold-cache best-effort — not as the guarantee. |
| TTS | Cloud synthesis drift | Cache decoded PCM on `(text, provider, voice, rate)`. Piper gives a fully offline option. |
| Forced alignment | GPU kernel/threading nondeterminism | CPU-only, `torch.use_deterministic_algorithms(True)`, cached output. |
| Chromium render | Font fallback, wall-clock animation, GPU raster | Bundled fonts, pinned Chrome Headless Shell, banned wall-clock APIs, software rasterisation. |
| ffmpeg | Encoder version | Pinned in Docker; comparison is frame-identical, not byte-identical. |

Say this in the log rather than overclaiming. A reviewer who knows LLMs will test the overclaim.

---

## 8. CLI Surface (`run`)

v1's wrapper could not re-render from an edited spec, which makes the R6 demo impossible. Required
flags:

```
./run --script scripts/script_b.txt --out output/script_b.mp4
./run --spec output/script_a.spec.json --out output/script_a.mp4   # skip the LLM (R6)
./run --script … --annotator heuristic                              # force the offline path
./run --script … --explain-values                                   # R4: expr → computed
./run --script … --dry-run                                          # spec only, no TTS/render
./run --script … --config alt.yaml --profile portrait               # R7
./run --script … --explain-cache                                    # R8
./run --script … --no-cache | --cache-dir .cache2
./run --verify-determinism output/a.mp4 output/b.mp4                # R3
./run --serve                                                       # dashboard (§13)
./run --help
```

`--from-stage` was specified in v1 and is **not** implemented. It was redundant: `--spec` already
resumes from after annotation, and the content-addressed cache makes every other stage skip itself
when its inputs are unchanged. A flag that manually re-does what the cache does automatically is a
second, less trustworthy source of truth about what is stale. `--dry-run` took its place, because
"produce the spec and stop" is the thing actually worth doing by hand (it is a ~3 s LLM-only loop
instead of a 400 s render).

```bash
#!/usr/bin/env bash
set -euo pipefail            # v1 had `set -e` only; a failing pipe stage passed silently

usage() { sed -n '/^Usage:/,/^$/p' "$0"; exit "${1:-0}"; }

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    *) ARGS+=("$1"); shift ;;      # forward everything else; do NOT reject unknown args,
  esac                             # which in v1 made --help itself an error
done

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || PY=python     # `python3` is frequently absent on Windows

exec "$PY" -m python_pipeline.main "${ARGS[@]}"
```

`main.py` creates the output directory before writing (v1 assumed it existed).

---

## 9. Interfaces (R7)

```python
class TTSProvider(Protocol):
    def synthesize(self, text: str, *, voice: str, rate: str) -> TTSResult: ...
    # TTSResult: pcm: bytes, sample_rate: int, word_boundaries: list[WordBoundary] | None

class Aligner(Protocol):
    def align(self, pcm: bytes, sample_rate: int, text: str,
              hint: list[WordBoundary] | None) -> list[WordTrigger]: ...

class AssetProvider(Protocol):
    def resolve(self, keywords: list[str]) -> list[AssetRef]: ...

class Renderer(Protocol):
    def render_scene(self, scene: Scene, theme: Theme, project: ProjectConfig,
                     out_path: Path) -> None: ...
```

`AssetProvider` exists because R7 names "visual asset generation" explicitly, and v1 had no such
seam. Both implementations ship and both are live: `null` (returns nothing; templates are designed to
look complete without assets) and `icon_pack` (vendored SVG lookup by keyword, the configured default).
An interface with one implementation is not a seam, which is why `null` is kept as a genuine toggle
rather than deleted once `icon_pack` worked.

Vendored SVG beats a diffusion model here, and the reasoning is not only cost: it is deterministic,
~free, instant, and R4 *forbids* image models for on-screen values anyway. A generated image is also
unauditable in a way a committed file is not — you cannot diff it against an expectation, and you cannot
tell a good sample from a lucky one.

The honest limitation is that `vendor_icons.CATALOG` is a **hand-made mini-ontology**: coverage is good
for concrete nouns and falls off exactly where the frames are blandest, on abstractions like "interest",
"compression", "resolution" and "bandwidth". The emoji *pack* is huge; the usable curated slice is
necessarily small, because every alias is a claim that a glyph means that word. Measured coverage is
6/7 scenes on each of the two scripts, and that number should be read as a property of these two
scripts, not a rate to expect.

---

## 10. Reproducibility on a clean machine

The README promises clone-to-video on a clean machine, and the dev environment is Windows. Torch +
Chromium + ffmpeg + tesseract on bare Windows is a multi-hour yak shave for a reviewer.

- Ship a `Dockerfile` pinning Python, Node, Chrome Headless Shell, ffmpeg, tesseract and the fonts.
  This is simultaneously the reproducibility story *and* part of the determinism story.
- `docker compose run engine ./run --script … --out …` as the documented happy path; native install
  documented as the alternative.
- `run.cmd` / `run.ps1` so `./run` has a Windows equivalent.
- Vendored icons are committed rather than fetched, so a clone renders the same frames as this machine
  with no network at all (§6 Stage 3.5). `manifest.json` makes the bytes auditable.

**Status: not built.** `Dockerfile`, `docker-compose.yml`, `README.md` and `run.ps1` are all 0-byte
placeholders. `run` and `run.cmd` work; everything above is the plan, not the state. This is called out
here rather than left implied because "the README promises" is currently a promise nothing keeps.

**Licensing (an explicit evaluation axis in the assignment).** Remotion is *not* unconditionally
free — a paid company licence applies above a small-headcount threshold. State the threshold, state
that this project falls under the free tier, and name the alternative that was rejected
(Playwright + a hand-rolled frame loop: more control, far more work, worse text layout). Have the
answer ready before it is asked.

---

## 11. Build Order

Ship a walking skeleton first. Breadth after depth. Status is current as of the Phase 1–3 log entry.

**Phase 0 — end-to-end skeleton. ✅ COMPLETE.** Hardcoded 2-scene spec → one generic template →
edge-tts → native word timings → video-only render → PCM track → mux. Result: 825 frames,
27.500000 s, video duration == audio duration. Two dead ends logged (absent `WordBoundary` events;
an unstable PCM hash that broke the align cache).

**Phase 1 — determinism and cache. ✅ COMPLETE.** Four cache tiers (spec/audio/align/render),
`--explain-cache`, `verify_determinism.py`. 4148 frames identical across two runs; the verifier also
tested against a known-different pair so a pass means something.

**Phase 2 — the R4 spine. ✅ COMPLETE.** `segmenter.py` + fidelity gate (9/9 sentence battery),
`evaluator.py` (18/18 arithmetic, 20/20 hostile expressions rejected, 13/13 formats),
`llm_annotator.py` with Gemini structured outputs. Two annotators ship: Gemini and the heuristic.
✅ **The Gemini path is now live and exercised** — see the measurement note below.

**Phase 3 — breadth for R2/R7. ◐ PARTIAL.** Done: 7 templates + `Fallback` (`ComparisonGrid` now
registered, as bars rather than a table — §3), portrait profile, Cartesia provider, script B on an
unrelated topic, `cue_check.py`, `icon_pack`. Outstanding: `piper`, `whisperx`, and the OCR gate
(`qa_ocr.py` is a stub and off by default).

**Phase 3.5 — visual density. ◐ IN PROGRESS.** The first watched videos were correct and bland: no
colour ever appeared even in a segment about colour, and nothing moved after the 0.5 s entry fade.
Cause was not architectural — `SwatchStrip.tsx`, `CountUp.tsx`, `ComparisonGrid.tsx` and all three
`assets/*.py` files were **0 bytes and imported by nobody**. Done: those three components, `Value.channels`
as the R4-safe way to feed them, `engine_sha256` in the render key so template edits actually take
effect, and the `icon_pack` `AssetProvider` — which is what finally puts a red apple on screen for
Script A. Measured: **6 of 7 scenes carry an icon on each script**, `cue_check` still 12/12 and 8/8,
and every icon cue resolves to a real word trigger. Not started, and deliberately so: background
motifs and sub-scene beats.

**Icons are a supporting layer, not the answer to "bland."** Worth stating plainly so the next reader
does not over-read the 6/7: an emoji *labels* the topic where a swatch strip *is* the content, because
the strip encodes a value. Scene count and the amount of movement per scene are untouched by this
phase — sub-scene beats are what would change those, and they are not built.

**Phase 3.6 — typography and the dashboard. ✅ COMPLETE.** Two pieces, in that order because the
second depends on the first being true.

*Fonts.* `theme.font_family` was a config key nothing read. Three OFL variable families are now
vendored to `remotion_engine/public/fonts/` and registered through `FontFace` inside `delayRender`
(§6 Stage 6). Proven rather than assumed: three stills of Script A `scene_01` at frame 110 with
`font_family` set to Inter / JetBrains Mono / Source Serif 4 produced **three distinct SHA-256s**
(`da5a2909…`, `0ca0ca2e…`, `6004d829…`), and reading the PNGs confirms a neo-grotesque vs. real
serifs. The control changes the frame, not just a string.

*Dashboard.* `./run --serve` — a timeline page and a settings page (§13). Verified end to end
against a live server: a `--dry-run` (rc=0, 7 scenes, spec+config+log downloadable), a `--spec`
re-render (rc=0, 400 s, video 2.68 MB, 9/9 stages resolved), a full script run with per-run
typography and palette overrides, 409 on a concurrent start, all four downloads serving with
run-scoped filenames, and six invalid-setting cases rejected 422 with **nothing written**. Four
parser and writer bugs were found by running it rather than by reading it; all four are logged.

**Phase 4 — hardening. ☐ NOT STARTED.** Run on 3–4 self-written scripts on unrelated topics (one
with no numbers at all, one with dates and percentages, one twice Script A's length). Fix what
breaks. Then rehearse the live-review sequence: fresh clone → Script B → run twice → edit a spec
value → re-render one scene.

**The measurement that was worth taking first, now taken.** Script B produced **5 of 7 `Fallback`
scenes** on the heuristic annotator, which cannot parse word-form arithmetic ("seventy-two divided by
seven"). With Gemini live it produces **1 of 7**, and on-screen values went 6 → 8 for script B and
6 → 12 for script A. That delta is the LLM annotator's entire contribution, measured rather than
asserted — and it means roughly half of "bland" was the degraded fallback path, not the templates.

**Log dead ends as they happen, in a running file.** The assignment demands at least two with
expectation / actual / hypothesis / next / resolution, and rewards specificity. Reconstructing them
from memory three days later produces exactly the vague write-up that scores nothing. The AAC
concat drift and the numeral-alignment failure are both likely to be genuine entries — record the
measurements when you hit them.

---

## 12. Revisions from v1, with rationale

Engineering-log source material. Each item is a defect in the first draft, not a preference.

**Blocking**

1. **LLM did the arithmetic.** Violated R4's "derived by your system … not typed into a template"
   and would get Script B's numbers wrong. → Symbolic `expr` + Python evaluation (§6 Stage 3).
   Highest-leverage change in the document.
2. **LLM did the segmentation.** Text drift makes TTS speak words the script never contained;
   model-dependent scene counts break R3 and bust every cache key. → Deterministic segmentation
   plus a hard fidelity assertion.
3. **WhisperX as primary aligner.** It mis-aligns precisely the graded tokens (`255`, `2⁸`, `×`,
   `16,777,216`) because the audio says words and the script shows digits. → TTS-native timings
   primary, WhisperX behind the `Aligner` interface, plus numeral/symbol normalisation with a map
   back to original tokens.
4. **`transition_type` was incompatible with `concat -c copy`.** §5 promised slides and cross-fades;
   Stage 5 stream-copied. Nothing to blend into, and `xfade` re-encodes and shortens duration. →
   Transitions modelled between scenes; pick option A or B and log the trade-off.
5. **Per-scene AAC concat.** ~20 ms encoder priming per boundary accumulates past ±150 ms and
   clicks. → Video-only scenes + one continuous frame-aligned PCM track + one final mux.

**Serious**

6. **"temperature: 0.0" as the determinism mechanism.** Not reproducible, and not even a valid
   parameter on `claude-opus-5`. → §7 states the cache as the real guarantee, per-component.
7. **Byte-identical was the implied definition of "same video."** Fragile and not what R3 asks. →
   Frame-identical, with a shipped verifier.
8. **Cache keyed on `audio_wav_bytes`** (container headers bust every entry) and **missing
   theme/fps/dimensions** (serves stale frames after a palette or aspect change). → Two-tier cache
   over decoded PCM with the full key.
9. **No way to re-render from an edited spec**, making the R6 demo impossible. → `--spec`,
   `--from-stage`.
10. **Frame numbers in the IR** leaked fps (config, per R7) into the artifact. → Milliseconds, plus
    `derived_from` hashes so a human narration edit invalidates only that scene's triggers.
11. **Templates were Script-A-shaped** — the R2 trap. `SubPixelVisualizer` was hardcoded RGB;
    `FormulaCard` took `{r, g, b}` and `hex_color`. Also: 3 templates over ~15 scenes is visually
    monotonous. → Generic prop shapes, 6 templates, a `Fallback`, and a length-agnostic
    `SwatchStrip`.
12. **No visual-asset-generation interface**, which R7 names explicitly. → `AssetProvider`.
13. **9:16 hand-waved as "CSS flexbox"**, and `resolution: "480p"` + `aspect_ratio` was ambiguous.
    → Explicit canvas + `output_scale`, an `orientation` token, vmin type scale, `min_font_px`,
    dual-aspect smoke render.
14. **Chromium determinism assumed rather than engineered.** → Bundled fonts, pinned browser,
    lint-enforced wall-clock ban.

**Worth knowing**

15. Remotion licensing is not unconditionally free; licensing is a named evaluation axis.
16. Whisper ASR is unnecessary with a known transcript — align-model only saves ~3 GB and most of
    the alignment runtime. Good cost-model content.
17. Live-demo single points of failure: LLM key/network, and edge-tts's undocumented endpoint. →
    Heuristic annotator fallback and Piper as a fully offline TTS provider; the offline path is both
    resilience and determinism.
18. Windows + "clean machine" ⇒ Docker, plus `run.cmd`/`run.ps1`.
19. No verification that computed text was actually *legible* — the realistic R4 failure at 480p.
    → OCR gate.
20. IR gaps vs R6: no `start_ms`/`duration_ms`, no `assets`, no `schema_version`, no provenance.
21. Shell defects: `set -e` without `pipefail`; unknown-arg rejection broke `--help`; no
    `--config`/`--cache-dir`; output directory never created; `-safe 0` with Windows absolute paths.

---

## 13. The dashboard (`./run --serve`)

A minimal two-page interface: a **timeline** of the workflow (the R6 artifact rendered as a picture)
and a **settings** page for the surface R7 requires to be configurable. When a run finishes, the video,
the scene spec, the config it used and the full engine log are all one click away.

```
./run --serve                       # http://127.0.0.1:8000
python -m python_pipeline.server    # identical; the module is directly runnable
```

### FastAPI + vanilla HTML/JS, no build step

One new Python dependency pair (`fastapi`, `uvicorn`), two static pages, no bundler, no second
`node_modules`, no `dist/` that must be rebuilt before the tool works. A React dashboard would add a
whole second dependency tree and a new way for a fresh clone to be broken — in service of two pages
and a progress stream. The cost is manual DOM building, which `static/app.js` keeps honest with one
`el()` factory. Both pages run offline.

### The dashboard is a client of the CLI, not a second engine

Every run shells out to `python -m python_pipeline.main` with ordinary flags. There is exactly one code
path (R1), so the dashboard cannot drift from the command line — a class of bug that is otherwise
guaranteed, because the UI path gets exercised far less than the command it duplicates. Three specific
reasons it is a subprocess and not an in-process call:

- A crash in a render would take the web server down with it — the page meant to *report* the failure
  would die alongside it.
- Intercepting `print()` means rebinding process-global `sys.stdout`, which breaks the moment two runs
  overlap.
- Every flag would need a second, parallel invocation path.

### Progress is parsed from stdout, not reported by the pipeline

The engine already prints `[engine] rendering scene_03 [BigNumber] 240 frames`. A structured progress
channel would mean editing every stage to report itself, purely to serve the UI. Parsing is uglier but
keeps the pipeline unaware a dashboard exists, and an unrecognised line still shows verbatim. The
parser is best-effort by design: the worst outcome is a stage bar that does not advance while the raw
log still scrolls.

Two consequences worth stating, because both were bugs first:

- **Stages are strictly ordered, so reaching stage N marks every earlier stage done.** Without this,
  stages whose only log line is their completion (`evaluate`, `track`) would never leave `pending`.
- **That back-fill is wrong when a stage was genuinely skipped**, so `--dry-run` (no audio/track/
  render/mux) and `--spec` (no segment/annotate/assets) pre-mark those stages `skipped` *with a
  reason*, and the reason replaces the stage's usual blurb. "keyword → vendored icon" is a lie on a run
  that matched no keywords, and a dash with no explanation reads as a failure.

A `--spec` re-render also never prints a scene list, so the timeline reads the input spec directly and
is fully populated before the first frame renders — on a 400-second render, an empty timeline for the
whole run is most of the experience.

### Per-run config by default; `config.yaml` only on an explicit Save

Edits build an override that is written to `.cache/runs/<id>/config.yaml` and passed with `--config`.
Two consequences worth having: the committed `config.yaml` stays the reproducible default, so trying
six palettes produces no git diff mid-review; and "which config produced this video" is answered by the
file sitting next to the video. **Save to config.yaml** is a separate button that rewrites the real file.

That save **patches the text of the scalar lines it changes** rather than `yaml.safe_dump`-ing the
document, because the comments in `config.yaml` carry the reasoning behind every provider choice and a
dump would strip all of them. The result is validated through `Config.model_validate` *before* writing —
a `config.yaml` that no longer parses would break the CLI too, and the dashboard would be the thing
that broke it. Measured: changing five settings produces a five-line diff with all 38 comments intact.

### A control must not offer an option that would crash, or one that changes nothing

Both are worse than omitting the control. The second is exactly what `theme.font_family` was for three
phases. So availability is **probed** — by import, by key presence, by files on disk — never declared:

- `piper` and `whisperx` are listed **disabled, with the reason** (`module is a stub`). A provider seam
  is architecture worth showing even when one end is unbuilt.
- A font that is not vendored is **omitted entirely**. A greyed-out typeface teaches nobody anything.
- Probes re-run per request, so vendoring icons or dropping a key into `.env` changes the page on
  reload without a restart.

### Security posture, stated rather than implied

`POST /api/run` starts a subprocess and `POST /api/config` rewrites `config.yaml`, so **this server is
remote code execution by design**. That is fine on loopback on a developer's machine and indefensible
anywhere else. Therefore:

- The host default is `127.0.0.1`, and any other `--host` prints a warning.
- There is no login, deliberately: a login would imply this is safe to expose, which it is not.
- `GET /api/env` returns key **names** only — never a value, a prefix or a length. `load_env()` returns
  names for exactly this reason, and a dashboard is a log with a browser attached.
- Everything the UI can write is a small explicit whitelist (`settings.EDITABLE`), each field
  type-coerced and range-clamped, with all errors collected so a form with two bad fields reports both.
- `providers.renderer` is **not** settable: it selects a code path that shells out to `npx` and has
  exactly one implementation, so a dropdown would be attack surface with no feature behind it.
- Every insertion of engine-provided text goes through `textContent`, never `innerHTML`. A narration
  script containing markup renders as characters.

### Deliberate limits

- **One run at a time** (409 otherwise). Two renders compete for CPU — Remotion already runs Chromium —
  and for the same cache staging paths. A dashboard that lets you start six renders on a laptop is a
  footgun disguised as a feature.
- **Run history is in memory only.** A restart forgets the *list*, not the artifacts, which stay under
  `.cache/runs/`. Persisting would mean a schema, a migration and a stale-PID problem for a feature
  nobody asked for.
- **Artifact existence is checked at request time**, so a run that failed after the spec but before the
  video shows no video button rather than a dead one.
- **The typography preview is browser-rendered**, using the same font files the engine loads. It shows
  the real typeface, but it is not a frame grab — only a render proves the frame.
- SSE polls a shared line list rather than using a per-subscriber queue, because two browser tabs on one
  run is a real case and a queue would deliver each line to only one of them.
