/**
 * ComparisonGrid — several values side by side, scaled against each other.
 *
 * ## Why this is bars, not a table
 *
 * The original spec described a table: `{columns: [label], rows: [{label, cells}]}`.
 * That shape was abandoned with the flat annotation contract (context.md §6 Stage
 * 2), and this template was left out of IMPLEMENTED_TEMPLATES because a flat
 * `items[]` cannot describe a 2-D grid.
 *
 * The reframing: what a comparison scene is *for* is showing relative magnitude,
 * and magnitude is exactly what a flat list of computed values already carries. So
 * this renders proportional bars rather than a table. That needs no new annotation
 * shape, and it uses the medium better than a grid of text would — "the third
 * decade added far more" is a sentence about a *ratio*, and a bar chart states a
 * ratio in a way a table of numbers does not.
 *
 * ## The comparison is drawn, never computed as a claim
 *
 * Bar widths are a ratio of numbers Python already resolved. No new figure is
 * derived and none is displayed: every label on screen is `value.resolved`
 * verbatim. The ratio only drives geometry, so a rounding difference in a bar
 * width can never become a wrong number on screen.
 *
 * Values that are not plain numbers (tuples, ranges, text) cannot be sized against
 * each other, so they render as full-width rows with no bar. Faking a magnitude for
 * them would invent a comparison the data does not support.
 */

import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

import {parseFigure} from '../components/CountUp';
import {SceneIcon} from '../components/SceneIcon';
import {triggerFor} from '../components/ValueBlock';
import {useCueProgress} from '../components/WordCue';
import {useMetrics, rootStyle} from '../theme';
import type {SceneProps, Theme, Value, WordTrigger} from '../types';
import type {Metrics} from '../theme';

interface GridProps {
	title?: string;
	caption?: string;
	items?: Value[];
}

/** One row: label, bar sized against the largest value, figure. */
const Row: React.FC<{
	value: Value;
	peak: number;
	triggers: WordTrigger[];
	theme: Theme;
	m: Metrics;
	accent: boolean;
}> = ({value, peak, triggers, theme, m, accent}) => {
	const progress = useCueProgress(triggerFor(value, triggers), {durationMs: 620});
	const figure = parseFigure(value.resolved);
	// Ratio against the largest comparable value. Geometry only — never displayed.
	const ratio = figure && peak > 0 ? Math.abs(figure.target) / peak : 0;
	const barH = m.space(2.6);

	return (
		<div style={{display: 'flex', flexDirection: 'column', gap: m.space(0.8)}}>
			<div
				style={{
					display: 'flex',
					justifyContent: 'space-between',
					alignItems: 'baseline',
					gap: m.space(2),
				}}
			>
				<span style={{fontSize: m.fontSize('caption'), color: theme.muted_color}}>
					{value.label}
				</span>
				<span
					style={{
						fontSize: m.fontSize('lead'),
						fontWeight: 700,
						color: accent ? theme.primary_color : theme.text_color,
						opacity: progress,
						fontVariantNumeric: 'tabular-nums',
					}}
				>
					{value.resolved ?? '—'}
					{value.unit ? (
						<span
							style={{
								fontSize: m.fontSize('caption'),
								color: theme.muted_color,
								marginLeft: m.space(0.6),
							}}
						>
							{value.unit}
						</span>
					) : null}
				</span>
			</div>

			{figure ? (
				<div
					style={{
						height: barH,
						borderRadius: barH / 2,
						backgroundColor: `${theme.muted_color}1F`,
						overflow: 'hidden',
					}}
				>
					<div
						style={{
							height: '100%',
							// The bar grows with the cue, so the comparison builds as it is
							// spoken rather than being fully drawn before it is mentioned.
							width: `${ratio * progress * 100}%`,
							borderRadius: barH / 2,
							backgroundColor: accent ? theme.primary_color : theme.secondary_color,
						}}
					/>
				</div>
			) : null}
		</div>
	);
};

export const ComparisonGrid: React.FC<SceneProps> = ({
	props,
	theme,
	orientation,
	output_scale,
	word_triggers,
	assets,
}) => {
	const m = useMetrics(theme, orientation, output_scale);
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const p = props as GridProps;

	const items = (p.items ?? []).slice(0, 5);
	// Peak over comparable values only, so one tuple in the list does not flatten
	// every bar to nothing.
	const peak = Math.max(
		...items.map((v) => {
			const f = parseFigure(v.resolved);
			return f ? Math.abs(f.target) : 0;
		}),
		0,
	);

	const enter = interpolate(frame, [0, Math.round(fps * 0.5)], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});

	// Largest value gets the accent colour — the visual point of a comparison is
	// which one wins, and deriving that from the data beats hardcoding index 0.
	const peakIndex = items.findIndex((v) => {
		const f = parseFigure(v.resolved);
		return f !== null && Math.abs(f.target) === peak && peak > 0;
	});

	return (
		<div
			style={{
				...rootStyle(theme),
				justifyContent: 'center',
				padding: `${m.space(8)}px ${m.space(9)}px`,
				gap: m.space(3.5),
			}}
		>
			{/* Bars run the full width, so a right-hand icon would sit over the longest
			    one. Top-left is the only corner this layout leaves genuinely free. */}
			<SceneIcon
				assets={assets}
				triggers={word_triggers}
				m={m}
				size={10}
				corner="top-left"
			/>
			{p.title ? (
				<div
					style={{
						fontSize: m.fontSize('title'),
						fontWeight: 700,
						opacity: enter,
						transform: `translateY(${(1 - enter) * 1.2}vmin)`,
					}}
				>
					{p.title}
				</div>
			) : null}

			<div style={{display: 'flex', flexDirection: 'column', gap: m.space(2.6)}}>
				{items.map((item, i) => (
					<Row
						key={`${item.label}-${i}`}
						value={item}
						peak={peak}
						triggers={word_triggers}
						theme={theme}
						m={m}
						accent={i === peakIndex}
					/>
				))}
			</div>

			{p.caption ? (
				<div
					style={{
						fontSize: m.fontSize('caption'),
						color: theme.muted_color,
						opacity: enter,
					}}
				>
					{p.caption}
				</div>
			) : null}
		</div>
	);
};
