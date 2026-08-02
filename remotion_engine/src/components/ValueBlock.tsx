/**
 * Shared value rendering, so cue anchoring exists in exactly one place.
 *
 * The important function here is `triggerFor`. Before `cue_word` existed in the
 * IR, each template guessed which word a value belonged to by fuzzy-matching the
 * *displayed* string against the transcript. That fails exactly where it matters:
 * narration says "sixteen point eight million" and the screen says "16.8", so no
 * match was found and the value appeared at frame 0 regardless of the audio —
 * silently, since a missing trigger means "show immediately".
 *
 * Now the annotator names the word. The fuzzy path is retained as a fallback for
 * hand-edited specs that predate `cue_word` (R6 lets anyone edit the spec, and an
 * older spec should still render).
 */

import React from 'react';

import {findTrigger, useCueProgress} from './WordCue';
import type {Metrics, ScaleStep} from '../theme';
import type {Theme, Value, WordTrigger} from '../types';

export const triggerFor = (
	value: Value | undefined,
	triggers: WordTrigger[],
): WordTrigger | undefined => {
	if (!value) return undefined;
	if (value.cue_word) {
		const exact = findTrigger(triggers, value.cue_word);
		if (exact) return exact;
	}
	// Fallback: try the resolved text, stripped of separators.
	if (value.resolved) {
		return findTrigger(triggers, value.resolved.replace(/[(),\s]/g, ''));
	}
	return undefined;
};

/** A displayed value: label above, resolved figure below, unit trailing. */
export const ValueBlock: React.FC<{
	value: Value;
	triggers: WordTrigger[];
	theme: Theme;
	m: Metrics;
	size?: ScaleStep;
	accent?: boolean;
}> = ({value, triggers, theme, m, size = 'title', accent = false}) => {
	const progress = useCueProgress(triggerFor(value, triggers), {durationMs: 420});

	return (
		<div style={{display: 'flex', flexDirection: 'column', gap: m.space(0.7)}}>
			{value.label ? (
				<div
					style={{
						fontSize: m.fontSize('caption'),
						color: theme.muted_color,
						letterSpacing: '0.04em',
					}}
				>
					{value.label}
				</div>
			) : null}
			<div
				style={{
					display: 'flex',
					alignItems: 'baseline',
					gap: m.space(1.2),
					opacity: progress,
					transform: `translateY(${(1 - progress) * 1.1}vmin)`,
					willChange: 'opacity, transform',
				}}
			>
				<span
					style={{
						fontSize: m.fontSize(size),
						fontWeight: 700,
						color: accent ? theme.primary_color : theme.text_color,
					}}
				>
					{/* A null `resolved` means the evaluator did not run. Show the fault
					    rather than rendering an empty space. */}
					{value.resolved ?? '—'}
				</span>
				{value.unit ? (
					<span style={{fontSize: m.fontSize('body'), color: theme.muted_color}}>
						{value.unit}
					</span>
				) : null}
			</div>
		</div>
	);
};
