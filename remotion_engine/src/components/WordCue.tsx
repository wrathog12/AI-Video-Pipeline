/**
 * The single place where milliseconds become frames.
 *
 * The IR stores word timings in ms because fps is configuration (R7); if frame
 * numbers were baked into the spec, changing fps would silently desync every
 * cue. Conversion happens here, against the live composition fps.
 *
 * Every animation is a pure function of useCurrentFrame(). No Date.now(), no
 * Math.random() — see .eslintrc.cjs, where that ban is enforced rather than
 * merely documented.
 */

import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

import type {WordTrigger} from '../types';

export const msToFrame = (ms: number, fps: number): number => Math.round((ms / 1000) * fps);

/** Find the trigger for a word (case-insensitive, punctuation-insensitive). */
export const findTrigger = (
	triggers: WordTrigger[],
	word: string,
): WordTrigger | undefined => {
	const norm = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
	const target = norm(word);
	return triggers.find((t) => norm(t.word) === target);
};

export interface CueOptions {
	/** Fade/rise duration in ms. Kept in ms so it is fps-independent. */
	durationMs?: number;
	/** Start slightly before the word is spoken; the eye lags the ear. */
	leadMs?: number;
	travel?: number;
}

/**
 * Progress in [0,1] for a cue anchored to a spoken word.
 * Returns 1 when there is no matching trigger, so a scene with no alignment
 * data renders fully visible rather than blank.
 */
export const useCueProgress = (
	trigger: WordTrigger | undefined,
	{durationMs = 400, leadMs = 80}: CueOptions = {},
): number => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();

	if (!trigger) {
		return 1;
	}

	const start = msToFrame(Math.max(0, trigger.start_ms - leadMs), fps);
	const end = start + Math.max(1, msToFrame(durationMs, fps));
	return interpolate(frame, [start, end], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});
};

/** Reveals children as the given word is spoken. */
export const WordCue: React.FC<{
	word: string;
	triggers: WordTrigger[];
	options?: CueOptions;
	children: React.ReactNode;
}> = ({word, triggers, options, children}) => {
	const trigger = findTrigger(triggers, word);
	const progress = useCueProgress(trigger, options);
	const travel = options?.travel ?? 1.5;

	return (
		<div
			style={{
				opacity: progress,
				transform: `translateY(${(1 - progress) * travel}vmin)`,
				willChange: 'opacity, transform',
			}}
		>
			{children}
		</div>
	);
};
