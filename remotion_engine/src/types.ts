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
}

export interface WordTrigger {
  word: string;
  start_ms: number;
  end_ms: number;
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
  word_triggers: WordTrigger[];
  theme: Theme;
  orientation: Orientation;
  /** Final delivered scale, so templates can enforce a legibility floor. */
  output_scale: number;
}
