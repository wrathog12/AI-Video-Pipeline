/**
 * CountUp — a computed figure counts up to its resolved value as it is narrated.
 *
 * ## The R4 hazard, and how this avoids it
 *
 * Animating a number means showing intermediate numbers, which is renderer-side
 * arithmetic on a value the pipeline promises Python computed. Two rules keep that
 * honest:
 *
 *   1. **The final frame renders `value.resolved` verbatim** — the exact string
 *      Python produced, never a re-derived one. Intermediate frames are visibly
 *      transient; the resting state is the authored truth. If a parse of
 *      `resolved` ever disagreed with `resolved` itself, the number that survives
 *      on screen is still Python's.
 *   2. **If `resolved` is not a plain number, there is no animation at all.**
 *      Tuples, ranges and text render immediately and unchanged. Interpolating
 *      "(255, 0, 0)" or "0-255" is meaningless, and a half-interpolated tuple is
 *      the "mangled digits" failure R4 names explicitly.
 *
 * So the parse below decides only the transient frames and whether the value is
 * animatable at all. It is never the source of the displayed final value.
 *
 * ## Formatting the transient frames
 *
 * Thousands separators are re-applied during the count, because a figure that
 * gains commas only on the last frame jitters distractingly. `Intl.NumberFormat`
 * and `toLocaleString` are deliberately avoided: locale resolution depends on the
 * host environment, which would make frame output machine-dependent and break R3.
 */

import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

import {msToFrame} from './WordCue';
import type {Metrics, ScaleStep} from '../theme';
import type {Theme, Value, WordTrigger} from '../types';

/** A plain decimal number, optionally comma-grouped, optionally percent. */
const PLAIN_NUMBER = /^-?\d{1,3}(?:,\d{3})*(?:\.\d+)?%?$|^-?\d+(?:\.\d+)?%?$/;

export interface ParsedFigure {
	target: number;
	decimals: number;
	grouped: boolean;
	suffix: string;
}

/**
 * Decide whether a resolved string can be counted, and how to shape the count.
 * Returns null for anything that must not be animated.
 */
export const parseFigure = (resolved: string | null | undefined): ParsedFigure | null => {
	if (!resolved) return null;
	const text = resolved.trim();
	if (!PLAIN_NUMBER.test(text)) return null;

	const suffix = text.endsWith('%') ? '%' : '';
	const body = suffix ? text.slice(0, -1) : text;
	const grouped = body.includes(',');
	const bare = body.replace(/,/g, '');
	const target = Number(bare);
	if (!Number.isFinite(target)) return null;

	const dot = bare.indexOf('.');
	return {
		target,
		decimals: dot === -1 ? 0 : bare.length - dot - 1,
		grouped,
		suffix,
	};
};

/** Comma-group an integer string. Locale-independent, unlike toLocaleString. */
const group = (digits: string): string => {
	const negative = digits.startsWith('-');
	const body = negative ? digits.slice(1) : digits;
	let out = '';
	for (let i = 0; i < body.length; i++) {
		if (i > 0 && (body.length - i) % 3 === 0) out += ',';
		out += body[i];
	}
	return negative ? `-${out}` : out;
};

/** Render an intermediate count value in the same shape as the final string. */
export const formatFigure = (n: number, f: ParsedFigure): string => {
	const fixed = Math.abs(n).toFixed(f.decimals);
	const [whole, frac] = fixed.split('.');
	const body = (f.grouped ? group(whole) : whole) + (frac ? `.${frac}` : '');
	return `${n < 0 ? '-' : ''}${body}${f.suffix}`;
};

/**
 * A figure that counts up to its computed value, anchored to a spoken word.
 *
 * `trigger` is resolved by the caller (via triggerFor) so this component stays
 * agnostic about how cues are matched — that logic lives in one place, in
 * ValueBlock/WordCue.
 */
export const CountUp: React.FC<{
	value: Value;
	trigger: WordTrigger | undefined;
	theme: Theme;
	m: Metrics;
	size?: ScaleStep;
	accent?: boolean;
	/** Count duration in ms. Kept in ms so it is fps-independent. */
	durationMs?: number;
	leadMs?: number;
	/**
	 * Style overrides merged last. Exists so a caller that owns the sizing — e.g.
	 * BigNumber, whose fitFactor must stay authoritative to prevent overflow — can
	 * pass `{fontSize: 'inherit'}` instead of having two components disagree about
	 * how large the figure is.
	 */
	style?: React.CSSProperties;
}> = ({
	value,
	trigger,
	theme,
	m,
	size = 'hero',
	accent = true,
	durationMs = 900,
	leadMs = 80,
	style: styleOverride,
}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const figure = parseFigure(value.resolved);

	const style: React.CSSProperties = {
		fontSize: m.fontSize(size),
		fontWeight: 800,
		color: accent ? theme.primary_color : theme.text_color,
		lineHeight: 1.02,
		// Essential here: proportional digits change width as they cycle, so the
		// whole figure would shift sideways for the duration of the count.
		fontVariantNumeric: 'tabular-nums',
		...styleOverride,
	};

	// Not animatable (tuple, range, text) or no cue: render the authored string.
	if (!figure || !trigger) {
		return <span style={style}>{value.resolved ?? '—'}</span>;
	}

	const start = msToFrame(Math.max(0, trigger.start_ms - leadMs), fps);
	const end = start + Math.max(1, msToFrame(durationMs, fps));

	// Past the count window, emit Python's exact string. This is rule 1: the
	// resting state is never a re-derived number.
	if (frame >= end) {
		return <span style={style}>{value.resolved}</span>;
	}
	// Before the cue, hold the layout but stay invisible, so the surrounding
	// elements do not reflow when the figure appears.
	if (frame <= start) {
		return <span style={{...style, opacity: 0}}>{value.resolved}</span>;
	}

	// easeOutCubic, so the count decelerates into its final value instead of
	// stopping dead. A pure function of the frame.
	const linear = interpolate(frame, [start, end], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});
	const eased = 1 - Math.pow(1 - linear, 3);

	return <span style={style}>{formatFigure(figure.target * eased, figure)}</span>;
};
