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
 *
 * The figure counts up as it is narrated (see CountUp), because this template's
 * whole job is one number and a static hero figure wastes the ~20 s it holds for.
 * CountUp settles on `resolved` verbatim, so the number that rests on screen is
 * still exactly what Python computed.
 */

import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

import {CountUp} from '../components/CountUp';
import {SceneIcon} from '../components/SceneIcon';
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
	assets,
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
	const trigger = triggerFor(expression, word_triggers);
	const progress = useCueProgress(trigger, {durationMs: 500});

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
			{/* Kept small here specifically: the hero figure is the widest element in
			    the project and fitFactor already shrinks it to fit, so the icon must
			    not crowd the row it might grow into. */}
			<SceneIcon assets={assets} triggers={word_triggers} m={m} size={11} />
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
				{/* fitFactor is measured against the FINAL string, not the current
				    count value, so the size is constant while counting. Sizing per
				    frame would make the figure visibly grow as digits accumulate. */}
				<div
					style={{
						transform: `translateY(${(1 - progress) * 1.6}vmin)`,
						whiteSpace: 'nowrap',
						letterSpacing: '-0.02em',
						fontSize: m.fontSize('hero') * fitFactor(shown, m.isPortrait),
					}}
				>
					{expression ? (
						<CountUp
							value={expression}
							trigger={trigger}
							theme={theme}
							m={m}
							size="hero"
							// Match the container so fitFactor governs; CountUp's own size
							// step would otherwise re-introduce the overflow this guards.
							style={{fontSize: 'inherit'}}
						/>
					) : (
						<span style={{color: theme.primary_color, fontWeight: 800}}>—</span>
					)}
				</div>
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
