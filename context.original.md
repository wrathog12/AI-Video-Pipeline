Automated AI Video Engine — System Architecture & Implementation SpecificationInstructions for Claude Code:
You are building a 100% automated, programmatic video generation engine for an AI production pipeline.
Your task is to implement the code, configurations, templates, and CLI tools specified in this document without human intervention between script ingestion and output .mp4 generation.  1. Core Architectural StrategyThe Compiler Engine PatternTo guarantee pass/fail compliance on unseen scripts (Requirement R2) and determinism (Requirement R3), do not ask the LLM to write raw TSX or Python code live during runtime.  Instead, implement a Compiler Pattern:The LLM is a Structural JSON Parser: It converts raw script text into a strictly validated scene_spec.json matching a Pydantic schema.  Pre-built Remotion Templates: React/CSS components consume properties directly from scene_spec.json.  Programmatic Execution: Python orchestrates LLM parsing, TTS audio generation, WhisperX forced alignment, Remotion headless rendering, and FFmpeg assembly.  2. Requirement Compliance MatrixRequirementDescriptionTechnical Implementation StrategyR1: One Command  Single entry point (./run --script ... --out ...)  Executable Bash wrapper calling the Python pipeline master script.  R2: Unseen Script  Zero hardcoded constants for specific topics  Abstract UI templates with general-purpose layout props driven solely by LLM JSON specs.  R3: Determinism  Identical script produces identical output video  LLM temperature: 0.0, pinned random seeds, cached audio artifacts, pinned Remotion frame rates.  R4: Computed Values  On-screen math/text derived programmatically  LLM parses and computes variables ($2^8 = 256$, range $0\text{--}255$, $(255, 0, 0)$, $256^3 = 16,777,216$). Values are rendered using standard React DOM/SVG text.  R5: Auto Sync  Visual events synchronized to spoken words within $\pm 150\text{ ms}$  WhisperX forced alignment extracts word-level millisecond timestamps mapped to exact Remotion frame numbers.  R6: Intermediate Representation  Human-readable, inspectable scene_spec.json  Schema-validated JSON file emitted before rendering. Editing JSON directly alters output video.  R7: Swappable Components  Modular providers and configuration-driven styling  config.yaml controls TTS engines, aspect ratio ($16:9$ vs $9:16$), resolution, palette, and fonts.  R8: Incremental Re-rendering  Changing one scene skips regenerating unchanged scenes  SHA-256 scene dependency hashing (SHA256(text + template + props + audio)). Skipped via .cache/ hit.  3. Project Directory StructurePlaintextai-video-engine/
├── run                         # Executable CLI script (R1)
├── config.yaml                 # Master configuration file (R7)
├── CLAUDE_PROJECT_SPEC.md      # This specification document
├── AI Video Production Assignment.pdf
│
├── python_pipeline/            # Orchestrator Backend
│   ├── __init__.py
│   ├── main.py                 # Master orchestrator
│   ├── llm_parser.py           # LLM parser & Pydantic Schema validator
│   ├── tts_engine.py           # Modular TTS generator (EdgeTTS / ElevenLabs)
│   ├── whisper_aligner.py      # WhisperX forced alignment wrapper
│   ├── cache_manager.py        # SHA-256 dependency hashing & cache engine
│   └── ffmpeg_stitcher.py      # Final video/audio concatenation
│
├── remotion_engine/            # React Video Rendering Engine
│   ├── package.json
│   ├── remotion.config.ts
│   ├── src/
│   │   ├── Root.tsx            # Main Composition entry point
│   │   ├── SceneDispatcher.tsx # Dynamic template loader
│   │   ├── templates/
│   │   │   ├── SplitTelemetry.tsx # Left visual stage, right data panel
│   │   │   ├── FormulaCard.tsx    # Math & equation visualizer
│   │   │   └── ComparisonGrid.tsx # Structural data comparisons
│   │   └── components/
│   │       ├── SubPixelVisualizer.tsx
│   │       └── AnimatedCounter.tsx
│
├── scripts/                    # Script inputs
│   └── script_a.txt
├── output/                     # Rendered video outputs
└── .cache/                     # Intermediate hashes, audio chunks, and scene MP4s
4. Configuration Schema (config.yaml)Satisfies Requirement R7 by keeping technical parameters out of code:  YAMLproject:
  fps: 30
  aspect_ratio: "16:9" # Options: "16:9" (1920x1080) or "9:16" (1080x1920)
  resolution: "480p"   # Legibility prioritized over heavy rendering

theme:
  font_family: "Inter, system-ui, sans-serif"
  primary_color: "#E63946"
  secondary_color: "#4EA8DE"
  background_color: "#0D1117"
  text_color: "#F8F9FA"

providers:
  llm: "gemini-1.5-flash" # Options: gemini-1.5-flash, claude-3-5-sonnet
  tts: "edge-tts"         # Options: edge-tts, elevenlabs
  alignment: "whisperx"
5. Scene Specification Schema (scene_spec.json)Satisfies Requirements R4 & R6 as an inspectable, human-editable intermediate representation:  JSON{
  "project_title": "How Computers See Color",
  "total_scenes": 5,
  "scenes": [
    {
      "scene_id": "scene_01",
      "template_name": "SplitTelemetry",
      "narration_text": "When you look at an apple, you see red. A computer, however, only understands numbers.",
      "transition_type": "fade",
      "props": {
        "stage_title": "Visual Perception vs Digital Representation",
        "stage_type": "macro_to_micro",
        "primary_label": "Human Vision",
        "secondary_label": "Digital Data",
        "computed_values": [
          { "key": "Perception", "value": "Color Spectrum" },
          { "key": "Machine Format", "value": "Binary / Integer" }
        ]
      },
      "word_triggers": []
    },
    {
      "scene_id": "scene_04",
      "template_name": "FormulaCard",
      "narration_text": "Pure red is (255, 0, 0). If you turn all the lights off to (0, 0, 0), you get pitch black.",
      "transition_type": "slide",
      "props": {
        "formula_title": "RGB Coordinate Mapping",
        "computed_math_string": "Pure Red = (255, 0, 0)",
        "channel_values": { "r": 255, "g": 0, "b": 0 },
        "hex_color": "#FF0000"
      },
      "word_triggers": [
        { "word": "pure", "frame": 10 },
        { "word": "red", "frame": 28 },
        { "word": "255", "frame": 52 },
        { "word": "black", "frame": 110 }
      ]
    }
  ]
}
6. Execution Pipeline Implementation GuideStage 1: LLM Parsing (llm_parser.py)Use Pydantic schema enforcement (e.g., Instructor or Gemini Structured Outputs) to ensure strict JSON structure.System Prompt Core Instructions:Break the narration script into modular scenes (approx. 10–15 seconds per scene).Map each scene to one of the 3 templates: SplitTelemetry, FormulaCard, or ComparisonGrid.Extract all mathematical expressions, ranges, and explicit values into discrete props fields.  Never output code; output structured JSON matching the provided schema.Stage 2: TTS & WhisperX Alignment (tts_engine.py & whisper_aligner.py)Generate audio chunk per scene (.cache/audio/{scene_id}.wav) using edge-tts or elevenlabs.Run WhisperX on each .wav file with the scene's narration text.Convert millisecond timestamps to target frame numbers:$$\text{frame} = \text{round}\left(\text{timestamp\_seconds} \times \text{FPS}\right)$$Inject the calculated word_triggers directly into scene_spec.json.Stage 3: SHA-256 Caching Engine (cache_manager.py)Calculate hash for each scene:$$\text{Hash} = \text{SHA256}(\text{narration\_text} + \text{template\_name} + \text{props\_json} + \text{audio\_wav\_bytes})$$Check if .cache/rendered_scenes/{hash}.mp4 exists:Hit: Log cache hit and reuse existing clip.Miss: Invoke Remotion render for that scene index.Stage 4: Remotion Render Engine (remotion_engine/)Pass scene_spec.json into Remotion via --props.SceneDispatcher.tsx dynamically mounts the matching React template based on template_name.Components check current frame using useCurrentFrame() and trigger animations when frame >= word_trigger.frame.  Support aspect ratios dynamically via CSS CSS flexbox/grid layout containers driven by config.yaml.  Stage 5: Final FFmpeg Assembly (ffmpeg_stitcher.py)Generate an FFmpeg input file list of cached scene .mp4 chunks.Execute FFmpeg demuxer concatenation:ffmpeg -f concat -safe 0 -i concat_list.txt -c copy output/final_video.mp4Ensure audio track is synchronized and muxed without re-encoding delays.7. Entry Point Wrapper (./run)Make sure ./run is executable (chmod +x ./run) and routes arguments cleanly:  Bash#!/usr/bin/env bash
set -e

# Parse arguments
SCRIPT_PATH=""
OUTPUT_PATH=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --script) SCRIPT_PATH="$2"; shift ;;
        --out) OUTPUT_PATH="$2"; shift ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

if [ -z "$SCRIPT_PATH" ] || [ -z "$OUTPUT_PATH" ]; then
    echo "Usage: ./run --script <script_path> --out <output_path>"
    exit 1
fi

# Execute python orchestrator
python3 python_pipeline/main.py --script "$SCRIPT_PATH" --out "$OUTPUT_PATH"
8. Development Roadmap for Claude Code AgentBuild the pipeline sequentially in these 4 phases:Phase 1 (Remotion UI Engine):Set up remotion_engine/.Create SplitTelemetry.tsx, FormulaCard.tsx, and ComparisonGrid.tsx using standard React + CSS styling.Verify rendering via dummy scene_spec.json.Phase 2 (Python Audio & Alignment Pipeline):Implement tts_engine.py using edge-tts.Implement whisper_aligner.py using whisperx to extract word frame triggers.Phase 3 (LLM Parser & Caching Engine):Implement llm_parser.py using Gemini Flash / Claude API with Pydantic response validation.Implement cache_manager.py with SHA-256 fingerprinting.Phase 4 (CLI Integration & Verification):Implement main.py and ./run wrapper.Run full test on scripts/script_a.txt to verify end-to-end execution.Edit one scene in scene_spec.json and re-run to confirm incremental re-rendering (R8).