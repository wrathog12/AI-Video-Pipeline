/**
 * SwatchStrip — paints tuple-valued data as colour, cued to the narration.
 *
 * This exists because a video that explains RGB while showing only grey text is
 * failing at the one thing video is for. The values are already in the IR:
 * `(255, 0, 0)` is sitting in scene 5's props as a computed tuple. This component
 * draws it.
 *
 * ## It must not be RGB-specific
 *
 * Hardcoding "channels[0] is red" is the Script-A trap R2 exists to catch: the
 * next script's tuple might be a coordinate pair, a CMYK colour, a date range, or
 * a pair of dollar amounts. So the mapping is decided by *arity and range*, not by
 * assuming the topic:
 *
 *   - 3 channels in 0..255  -> rgb(). The only case where a real colour is meant.
 *   - 4 channels in 0..100  -> CMYK, converted. Also unambiguous.
 *   - 1 channel             -> a proportion of the theme accent (a ramp).
 *   - anything else         -> NOT a colour. Renders proportional bars in theme
 *                             colours, which is honest about not knowing.
 *
 * The fallback is the important branch. Guessing a colour from two arbitrary
 * numbers would produce a confidently wrong swatch, and a wrong colour beside a
 * correct number reads as the number being wrong too.
 *
 * ## No arithmetic on display strings
 *
 * Channels come from `value.channels`, which Python's evaluator fills from the
 * tuple it already computed. Parsing `resolved` back into numbers here would move
 * computation into the renderer — the exact R4 violation the expr/resolved split
 * prevents.
 */

import React from 'react';
import {interpolate} from 'remotion';

import {triggerFor} from './ValueBlock';
import {useCueProgress} from './WordCue';
import type {Metrics} from '../theme';
import type {Theme, Value, WordTrigger} from '../types';

/** How a channel list was interpreted. 'bars' means "not a colour". */
export type SwatchKind = 'rgb' | 'cmyk' | 'ramp' | 'bars';

const inRange = (xs: number[], lo: number, hi: number): boolean =>
	xs.every((x) => x >= lo && x <= hi);

const clamp255 = (x: number): number => Math.max(0, Math.min(255, Math.round(x)));

/**
 * Decide what a channel list represents. Exported for testing: the branch that
 * matters is that ambiguous input lands on 'bars' rather than on a guess.
 */
export const classifyChannels = (channels: number[]): SwatchKind => {
	if (channels.length === 3 && inRange(channels, 0, 255)) return 'rgb';
	if (channels.length === 4 && inRange(channels, 0, 100)) return 'cmyk';
	if (channels.length === 1 && inRange(channels, 0, 255)) return 'ramp';
	return 'bars';
};

/** CSS colour for a channel list, or null when it does not denote a colour. */
export const swatchColor = (
	channels: number[],
	kind: SwatchKind,
	theme: Theme,
): string | null => {
	if (kind === 'rgb') {
		const [r, g, b] = channels;
		return `rgb(${clamp255(r)}, ${clamp255(g)}, ${clamp255(b)})`;
	}
	if (kind === 'cmyk') {
		const [c, m, y, k] = channels.map((v) => v / 100);
		return `rgb(${clamp255(255 * (1 - c) * (1 - k))}, ${clamp255(
			255 * (1 - m) * (1 - k),
		)}, ${clamp255(255 * (1 - y) * (1 - k))})`;
	}
	if (kind === 'ramp') {
		// A single channel is an intensity, not a hue. Show it as opacity over the
		// theme accent so it reads as "how much" rather than inventing a colour.
		return theme.primary_color;
	}
	return null;
};

/**
 * A swatch for one tuple value: the painted colour plus its label and figure.
 *
 * `revealed` gates the paint on the narration. A swatch that is already visible
 * when the word is spoken is not synced to anything.
 */
export const Swatch: React.FC<{
	value: Value;
	triggers: WordTrigger[];
	theme: Theme;
	m: Metrics;
	/** Side length in vmin units. */
	size?: number;
}> = ({value, triggers, theme, m, size = 14}) => {
	const progress = useCueProgress(triggerFor(value, triggers), {durationMs: 460});
	const channels = value.channels ?? [];
	const kind = classifyChannels(channels);
	const color = swatchColor(channels, kind, theme);

	// Scale up slightly as it appears. Pure function of the cue progress, so it
	// stays deterministic under the wall-clock ban.
	const scale = interpolate(progress, [0, 1], [0.92, 1]);
	const box = m.space(size);

	return (
		<div
			style={{
				display: 'flex',
				flexDirection: 'column',
				alignItems: 'flex-start',
				gap: m.space(1),
				opacity: progress,
				transform: `scale(${scale})`,
				transformOrigin: 'top left',
				willChange: 'opacity, transform',
			}}
		>
			{color !== null ? (
				<div
					style={{
						width: box,
						height: box,
						borderRadius: m.space(1.4),
						backgroundColor: color,
						// A pure-black swatch on a near-black background is invisible
						// without a border, and "(0, 0, 0) is black" is a thing scripts
						// actually say.
						border: `1px solid ${theme.text_color}2E`,
						opacity: kind === 'ramp' ? channels[0] / 255 : 1,
					}}
				/>
			) : (
				<ChannelBars channels={channels} theme={theme} m={m} width={box} progress={progress} />
			)}

			{value.label ? (
				<div style={{fontSize: m.fontSize('caption'), color: theme.muted_color}}>
					{value.label}
				</div>
			) : null}
			<div style={{fontSize: m.fontSize('body'), fontWeight: 700}}>
				{value.resolved ?? '—'}
			</div>
		</div>
	);
};

/**
 * Proportional bars for channels that do not denote a colour.
 *
 * Normalised against the largest channel present rather than a fixed maximum,
 * because the range is unknown by construction — this is the branch for tuples
 * whose meaning we deliberately refuse to guess.
 */
const ChannelBars: React.FC<{
	channels: number[];
	theme: Theme;
	m: Metrics;
	width: number;
	progress: number;
}> = ({channels, theme, m, width, progress}) => {
	const peak = Math.max(...channels.map((c) => Math.abs(c)), 1);
	const barH = m.space(1.6);

	return (
		<div
			style={{
				width,
				display: 'flex',
				flexDirection: 'column',
				gap: m.space(0.8),
				justifyContent: 'center',
			}}
		>
			{channels.map((c, i) => (
				<div
					key={i}
					style={{
						height: barH,
						borderRadius: barH / 2,
						backgroundColor: `${theme.muted_color}26`,
						overflow: 'hidden',
					}}
				>
					<div
						style={{
							height: '100%',
							width: `${(Math.abs(c) / peak) * 100 * progress}%`,
							borderRadius: barH / 2,
							backgroundColor: i === 0 ? theme.primary_color : theme.secondary_color,
						}}
					/>
				</div>
			))}
		</div>
	);
};

/**
 * A row of swatches for every tuple-valued item passed in.
 *
 * Returns null when nothing has channels, so a template can drop this in
 * unconditionally and it simply does not appear for non-tuple scenes. That is
 * what keeps it topic-agnostic: no template has to know whether *this* script is
 * about colour.
 */
export const SwatchStrip: React.FC<{
	values: Value[];
	triggers: WordTrigger[];
	theme: Theme;
	m: Metrics;
	size?: number;
}> = ({values, triggers, theme, m, size}) => {
	const drawable = values.filter((v) => (v.channels ?? []).length > 0);
	if (drawable.length === 0) return null;

	return (
		<div
			style={{
				display: 'flex',
				flexDirection: m.isPortrait ? 'column' : 'row',
				gap: m.space(3),
				flexWrap: 'wrap',
				alignItems: 'flex-start',
			}}
		>
			{drawable.map((value, i) => (
				<Swatch
					key={`${value.label}-${i}`}
					value={value}
					triggers={triggers}
					theme={theme}
					m={m}
					size={size}
				/>
			))}
		</div>
	);
};
