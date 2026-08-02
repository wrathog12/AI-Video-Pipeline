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
| **R7** | Swappable components | Four interfaces: `TTSProvider`, `Aligner`, `AssetProvider`, `Renderer`. Selected in `config.yaml`. Resolution, aspect ratio (16:9 **and** 9:16), palette, typography are all config. |
| **R8** | Incremental re-render | Two-tier content-addressed cache (audio, render) keyed on decoded PCM + theme + dimensions + fps. `--explain-cache` prints per-scene hit/miss and the reason for each miss. |

---

## 3. Project Directory Structure

```
ai-video-engine/
├── run                          # bash entry point (R1)
├── run.cmd / run.ps1            # Windows shims — same CLI surface
├── config.yaml                  # master configuration (R7)
├── Dockerfile                   # pins python, node, chromium, ffmpeg, fonts
├── docker-compose.yml
├── README.md                    # clone → video on a clean machine
├── context.md                   # this document
│
├── python_pipeline/
│   ├── __init__.py
│   ├── main.py                  # orchestrator + CLI
│   ├── segmenter.py             # DETERMINISTIC scene splitting (no LLM)
│   ├── llm_annotator.py         # LLM: template + props for fixed segments
│   ├── schema.py                # Pydantic models, schema_version
│   ├── evaluator.py             # symbolic expression evaluation + formatting
│   ├── heuristic_annotator.py   # rule-based fallback if the LLM is unreachable
│   ├── tts/
│   │   ├── base.py              # TTSProvider interface
│   │   ├── edge.py              # edge-tts (+ native WordBoundary timings)
│   │   ├── piper.py             # local/offline provider
│   │   └── elevenlabs.py
│   ├── align/
│   │   ├── base.py              # Aligner interface
│   │   ├── native.py            # timings straight from the TTS provider
│   │   └── whisperx.py          # forced-alignment fallback
│   ├── assets/
│   │   ├── base.py              # AssetProvider interface
│   │   ├── null.py              # returns nothing; templates degrade gracefully
│   │   └── icon_pack.py         # local SVG lookup by keyword
│   ├── cache.py                 # two-tier content-addressed cache
│   ├── audio_track.py           # frame-aligned continuous PCM assembly
│   ├── renderer.py              # Remotion invocation (Renderer interface)
│   ├── mux.py                   # single final ffmpeg mux
│   ├── qa_ocr.py                # R4 verification gate
│   └── verify_determinism.py    # frame-hash comparison of two runs
│
├── remotion_engine/
│   ├── package.json
│   ├── remotion.config.ts
│   ├── fonts/                   # bundled locally — never fetched at render time
│   └── src/
│       ├── Root.tsx
│       ├── SceneDispatcher.tsx  # maps template_name → component, Fallback on miss
│       ├── theme.ts             # reads theme + orientation from injected props
│       ├── templates/
│       │   ├── TitleCard.tsx
│       │   ├── KeyValuePanel.tsx
│       │   ├── ExpressionCard.tsx
│       │   ├── BigNumber.tsx
│       │   ├── ComparisonGrid.tsx
│       │   ├── ProcessSteps.tsx
│       │   └── Fallback.tsx      # narration + safe generic layout
│       └── components/
│           ├── WordCue.tsx       # ms → frame conversion happens HERE
│           ├── SwatchStrip.tsx   # generic colour/value strip (not RGB-specific)
│           └── CountUp.tsx
│
├── scripts/script_a.txt
├── output/
└── .cache/
    ├── spec/                    # LLM annotation results
    ├── audio/                   # PCM WAV per (text, provider, voice, rate)
    ├── align/                   # aligner output per audio hash
    └── scenes/                  # video-only scene MP4s
```

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
  llm: "claude-opus-5"          # annotation only
  tts: "edge-tts"               # edge-tts | piper | elevenlabs
  aligner: "native"             # native | whisperx
  assets: "null"                # null | icon_pack
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
    "llm_model": "claude-opus-5",
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
          "resolved": "(255, 0, 0)"
        },
        "items": [
          { "label": "All channels off", "expr": "(0, 0, 0)", "format": "tuple", "resolved": "(0, 0, 0)" }
        ],
        "swatches": [
          { "label": "resolved", "channels": [255, 0, 0] },
          { "label": "resolved", "channels": [0, 0, 0] }
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
          "resolved": "16,777,216"
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
- `word_triggers` are **milliseconds relative to the scene's own audio**, and are invalidated when
  `derived_from.narration_sha256` no longer matches `narration_text`. Editing narration in the spec
  therefore re-aligns only that one scene.
- `transitions` sit *between* scenes, not on them. A `transition_type` field on a scene has no
  well-defined meaning for the last scene and no second clip to blend with.
- `assets` is present even when empty so the `AssetProvider` interface is visible in the IR (R7).

### Generic prop shapes

Templates must never receive topic-specific props. `channel_values: {r, g, b}` is a Script-A trap.
The universal shapes:

| Template | Props |
|---|---|
| `TitleCard` | `{ title, subtitle? }` |
| `KeyValuePanel` | `{ title, items: [{label, expr?, resolved, unit?}] }` |
| `ExpressionCard` | `{ title, expression: {label, expr, format, resolved}, steps?: [...], items? }` |
| `BigNumber` | `{ title, expression, caption? }` |
| `ComparisonGrid` | `{ title, columns: [label], rows: [{label, cells: [resolved]}] }` |
| `ProcessSteps` | `{ title, steps: [{label, detail?}] }` |
| `Fallback` | `{ title?, items? }` — renders narration-derived text on the theme background |

`SwatchStrip` takes `channels: number[]` of arbitrary length, so it works for RGB, CMYK, a
grayscale ramp, or anything else a future script needs.

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

Input: the fixed segment list. Output: `template_name` + `props` per segment, nothing else.

- Use the Anthropic SDK with **structured outputs**: `client.messages.parse(...)` with
  `output_config={"format": ...}` derived from the Pydantic model. Model: `claude-opus-5`.
- **Do not send `temperature`.** It is not a "set it to 0 for determinism" knob here — it is
  rejected with a 400 on `claude-opus-5`. Determinism comes from the cache (§7).
- Prompt rules, verbatim in the system prompt:
  - You are given segments. Do not merge, split, reword, or re-order them.
  - Never compute a numeric result. Emit `{"expr": "...", "format": "..."}` and let the caller
    evaluate it. `expr` must be a pure arithmetic/tuple expression in Python syntax.
  - Choose exactly one `template_name` from the enumerated list. If nothing fits, choose
    `Fallback`.
  - Emit no code, no markdown, no commentary.
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
- Cap magnitude (`abs(base) <= 10**6`, `abs(exponent) <= 64`) so `9**9**9` cannot hang the render.
- Formatters: `int`, `thousands` (`f"{v:,}"`), `float:N`, `tuple`, `range` (`"0–255"` with an en
  dash), `percent`, `raw`.
- Write the result into `props…resolved` and freeze it. Templates render `resolved` and never
  compute.

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

- Fonts bundled in `remotion_engine/fonts/` and loaded via `@remotion/fonts` from disk. A Google
  Fonts fetch is both a network dependency at render time and a nondeterminism source.
- `Math.random()`, `Date.now()`, `new Date()` and `performance.now()` are banned — add an ESLint
  rule so the ban is enforced, not aspirational. Every animation is a pure function of
  `useCurrentFrame()`.
- Pin the Chrome Headless Shell version in `remotion.config.ts`; record it in `provenance`.
- `SceneDispatcher.tsx` looks up `template_name` in a registry and mounts `Fallback` on a miss.
  Never throw — a crash on an unmapped template is exactly the R2 failure mode.
- Layout: `theme.ts` exposes `orientation` and a vmin-based type scale. Templates set flex
  direction from `orientation` rather than assuming a wide canvas. Clamp every computed font size
  to `min_font_px`. CI smoke-renders one frame at both 1920×1080 and 1080×1920.

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
2. `temperature` is not an accepted parameter on `claude-opus-5` at all; sending it returns a 400.

R3 explicitly permits caching as the constraint mechanism, so state it plainly:

| Component | Non-determinism source | Constraint |
|---|---|---|
| LLM annotation | Inference non-reproducibility | Content-addressed cache on `(script, prompt, model_id, schema_version)`. The second run reads the same spec. |
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
./run --script … --from-stage render                                # resume
./run --script … --config alt.yaml --profile portrait               # R7
./run --script … --explain-cache                                    # R8
./run --script … --no-cache | --cache-dir .cache2
./run --verify-determinism output/a.mp4 output/b.mp4                # R3
./run --help
```

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
seam. Ship `null` (returns nothing; templates are designed to look complete without assets) and
`icon_pack` (local SVG lookup by keyword). Argue in the log for programmatic SVG over a diffusion
model: it is deterministic, ~free, instant, and R4 *forbids* image models for on-screen values
anyway.

---

## 10. Reproducibility on a clean machine

The README promises clone-to-video on a clean machine, and the dev environment is Windows. Torch +
Chromium + ffmpeg + tesseract on bare Windows is a multi-hour yak shave for a reviewer.

- Ship a `Dockerfile` pinning Python, Node, Chrome Headless Shell, ffmpeg, tesseract and the fonts.
  This is simultaneously the reproducibility story *and* part of the determinism story.
- `docker compose run engine ./run --script … --out …` as the documented happy path; native install
  documented as the alternative.
- `run.cmd` / `run.ps1` so `./run` has a Windows equivalent.

**Licensing (an explicit evaluation axis in the assignment).** Remotion is *not* unconditionally
free — a paid company licence applies above a small-headcount threshold. State the threshold, state
that this project falls under the free tier, and name the alternative that was rejected
(Playwright + a hand-rolled frame loop: more control, far more work, worse text layout). Have the
answer ready before it is asked.

---

## 11. Build Order

Ship a walking skeleton first. Breadth after depth.

**Phase 0 — end-to-end skeleton (day 1).** Hardcoded 2-scene spec → one generic template →
edge-tts → native word timings → video-only render → PCM track → mux. Verify a real MP4 plays with
audio in sync. Everything else is an elaboration of a working pipe.

**Phase 1 — determinism and cache.** Two-tier cache, `--explain-cache`, `verify_determinism.py`.
Run twice, prove frame-identical.

**Phase 2 — the R4 spine.** `segmenter.py` + fidelity assertion, `evaluator.py` + the AST whitelist,
`llm_annotator.py` with structured outputs. Test the evaluator against Script A's four values *and*
against invented values from an unrelated topic.

**Phase 3 — breadth for R2/R7.** Remaining templates + `Fallback`, portrait profile, `piper`,
`whisperx`, `icon_pack`, OCR gate, Docker.

**Phase 4 — hardening.** Run on 3–4 self-written scripts on unrelated topics (one with no numbers
at all, one with dates and percentages, one twice Script A's length). Fix what breaks. Then rehearse
the live-review sequence: fresh clone → Script B → run twice → edit a spec value → re-render one
scene.

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
