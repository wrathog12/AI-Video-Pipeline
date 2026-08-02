/**
 * BigNumber — one figure is the point of the scene.
 *
 * Props: {title?, expression, caption?}.
 *
 * The figure is rendered at the `hero` step, which is the one place in the
 * project where overflow is a genuine risk: "16,777,216" at hero size on a
 * portrait canvas is wider than the frame. Rather than let it clip (an R4
 * failure — a mangled number), the size is scaled down by character count. The
 * arithmetic is on the *layout*, never on the value.
 */

import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

import {triggerFor} from '../components/ValueBlock';
import {useCueProgress} from '../components/WordCue';
import {useMetrics, rootStyle} from '../theme';
import type {SceneProps, Value} from '../types';

interface BigNumberProps {
	title?: string;
	expression?: Value;
	caption?: string | null;
}

/** Shrink the hero size once the string is long enough to threaten the margins. */
const fitFactor = (text: string, isPortrait: boolean): number => {
	const budget = isPortrait ? 7 : 11;
	if (text.length <= budget) return 1;
	return Math.max(0.42, budget / text.length);
};

export const BigNumber: React.FC<SceneProps> = ({
	props,
	theme,
	orientation,
	output_scale,
	word_triggers,
}) => {
	const m = useMetrics(theme, orientation, output_scale);
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const p = props as BigNumberProps;

	const enter = interpolate(frame, [0, Math.round(fps * 0.5)], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});

	const expression = p.expression;
	const shown = expression?.resolved ?? '—';
	const progress = useCueProgress(triggerFor(expression, word_triggers), {durationMs: 500});

	return (
		<div
			style={{
				...rootStyle(theme),
				justifyContent: 'center',
				alignItems: 'flex-start',
				padding: `${m.space(8)}px ${m.space(9)}px`,
				gap: m.space(2.5),
			}}
		>
			{p.title ? (
				<div
					style={{
						fontSize: m.fontSize('caption'),
						letterSpacing: '0.14em',
						textTransform: 'uppercase',
						color: theme.muted_color,
						opacity: enter,
					}}
				>
					{p.title}
				</div>
			) : null}

			{expression?.label ? (
				<div style={{fontSize: m.fontSize('body'), color: theme.muted_color, opacity: enter}}>
					{expression.label}
				</div>
			) : null}

			<div
				style={{
					display: 'flex',
					alignItems: 'baseline',
					gap: m.space(1.5),
					maxWidth: '100%',
				}}
			>
				<span
					style={{
						fontSize: m.fontSize('hero') * fitFactor(shown, m.isPortrait),
						fontWeight: 800,
						lineHeight: 1,
						letterSpacing: '-0.02em',
						color: theme.primary_color,
						opacity: progress,
						transform: `translateY(${(1 - progress) * 1.6}vmin)`,
						whiteSpace: 'nowrap',
					}}
				>
					{shown}
				</span>
				{expression?.unit ? (
					<span
						style={{
							fontSize: m.fontSize('lead'),
							color: theme.muted_color,
							opacity: progress,
						}}
					>
						{expression.unit}
					</span>
				) : null}
			</div>

			<div
				style={{
					height: Math.max(2, m.space(0.35)),
					width: `${26 * enter}%`,
					backgroundColor: theme.secondary_color,
					borderRadius: 999,
				}}
			/>

			{p.caption ? (
				<div
					style={{
						fontSize: m.fontSize('body'),
						color: theme.text_color,
						lineHeight: 1.45,
						maxWidth: '80%',
						opacity: enter,
					}}
				>
					{p.caption}
				</div>
			) : null}
		</div>
	);
};
