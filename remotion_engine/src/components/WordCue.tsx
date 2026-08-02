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

export const normalizeWord = (s: string): string =>
	s.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');

/**
 * Find the trigger for a word (case-insensitive, punctuation-insensitive).
 *
 * Three passes, narrowing from exact to loose. The looser passes exist because
 * the annotator names a word from the narration but the TTS provider decides how
 * to tokenise it, and the two disagree in two specific ways:
 *
 *   1. Compounds. The cue is "8"; the provider emits "8-bit" as one token, which
 *      normalises to "8bit". A prefix match recovers it.
 *   2. Blobs. Providers occasionally emit a run of words as a single event
 *      ("256 equals 16,777,216"). Matching against the contained sub-tokens
 *      recovers the cue, at the cost of firing at the blob's start.
 *
 * A miss is not silent-safe — `useCueProgress` treats "no trigger" as "show
 * immediately", so an unmatched cue means the element ignores the audio entirely.
 * That is why the fallbacks are worth having.
 */
export const findTrigger = (
	triggers: WordTrigger[],
	word: string,
): WordTrigger | undefined => {
	const target = normalizeWord(word);
	if (!target) return undefined;

	const exact = triggers.find((t) => normalizeWord(t.word) === target);
	if (exact) return exact;

	// Pass 2: the cue is a prefix of a compound token ("8" in "8-bit").
	const prefix = triggers.find((t) => normalizeWord(t.word).startsWith(target));
	if (prefix) return prefix;

	// Pass 3: the cue is one word inside a multi-word event.
	return triggers.find((t) =>
		t.word.split(/\s+/).some((part) => normalizeWord(part) === target),
	);
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
