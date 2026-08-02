/**
 * Mirror of python_pipeline/schema.py. Keep the two in sync — this is the
 * renderer's half of the IR contract.
 *
 * Note that timings are milliseconds everywhere. Conversion to frames happens
 * only inside components, using useVideoConfig().fps.
 */

export type TemplateName =
  | 'TitleCard'
  | 'KeyValuePanel'
  | 'ExpressionCard'
  | 'BigNumber'
  | 'ComparisonGrid'
  | 'ProcessSteps'
  | 'Fallback';

export type ValueFormat =
  | 'int'
  | 'thousands'
  | 'float'
  | 'tuple'
  | 'range'
  | 'percent'
  | 'raw';

/**
 * A displayed value. `expr` is what the annotator proposed; `resolved` is what
 * the Python evaluator computed. Templates MUST render `resolved` and must never
 * compute anything themselves — that is the R4 boundary.
 */
export interface Value {
  label: string;
  expr: string | null;
  format: ValueFormat;
  unit: string | null;
  resolved: string | null;
  /**
   * The narrated word this value should appear on (R5). Named by the annotator
   * rather than guessed here, because the spoken form and the displayed form
   * often differ ("sixteen point eight million" vs "16.8").
   */
  cue_word: string | null;
  /**
   * Numeric components of a tuple result, supplied by the Python evaluator.
   *
   * Read this — never parse `resolved`. `resolved` is a display string, and
   * turning "(255, 0, 0)" back into numbers here would be arithmetic in the
   * renderer, which is the R4 boundary the expr/resolved split exists to hold.
   * Empty for every non-tuple value, so check length before drawing.
   */
  channels: number[];
}

export interface WordTrigger {
  word: string;
  start_ms: number;
  end_ms: number;
}

/**
 * A visual asset chosen by the Python asset provider (R7).
 *
 * `path` is relative to `public/`, resolved with Remotion's staticFile(). It is
 * never absolute: an absolute path would bake the producing machine's directory
 * layout into the IR, and a spec is meant to be re-renderable elsewhere.
 *
 * `cue_word` is the narration word that selected the asset, spelled as the
 * narration spells it, so it can be matched against a word trigger and revealed as
 * it is spoken.
 */
export interface AssetRef {
  kind: 'svg' | 'image' | 'none';
  id: string;
  path: string | null;
  cue_word: string | null;
}

export interface Theme {
  font_family: string;
  type_scale_base_vmin: number;
  min_font_px: number;
  primary_color: string;
  secondary_color: string;
  background_color: string;
  text_color: string;
  muted_color: string;
}

export type Orientation = 'landscape' | 'portrait';

/** Props injected into the Remotion composition via --props. */
export interface SceneProps {
  scene_id: string;
  template_name: TemplateName;
  narration_text: string;
  props: Record<string, unknown>;
  /** Empty under the `null` asset provider, which is the default. */
  assets: AssetRef[];
  word_triggers: WordTrigger[];
  theme: Theme;
  orientation: Orientation;
  /** Final delivered scale, so templates can enforce a legibility floor. */
  output_scale: number;
}
