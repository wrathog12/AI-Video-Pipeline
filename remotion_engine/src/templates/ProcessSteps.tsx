/**
 * ProcessSteps — a sequence of stages.
 *
 * Props: {title?, steps: [{label, detail?}]}.
 *
 * Steps have no `cue_word`, because a step is prose rather than a value. They are
 * revealed on an even time division of the scene instead: the stagger is a pure
 * function of the frame and the step count, so it stays deterministic and adapts
 * to a scene of any length without the annotator supplying timings (which would
 * put timing decisions in model output — the thing the IR keeps out).
 */

import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

import {SceneIcon} from '../components/SceneIcon';
import {useMetrics, rootStyle} from '../theme';
import type {SceneProps} from '../types';

interface Step {
	label: string;
	detail?: string | null;
}

interface ProcessProps {
	title?: string;
	steps?: Step[];
}

export const ProcessSteps: React.FC<SceneProps> = ({
	props,
	theme,
	orientation,
	output_scale,
	assets,
	word_triggers,
}) => {
	const m = useMetrics(theme, orientation, output_scale);
	const frame = useCurrentFrame();
	const {fps, durationInFrames} = useVideoConfig();
	const p = props as ProcessProps;

	const steps = (p.steps ?? []).slice(0, 5);
	const enter = interpolate(frame, [0, Math.round(fps * 0.5)], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});

	// Spread reveals across the first ~70% of the scene, leaving the complete
	// list on screen for a beat before the cut.
	const usable = Math.max(1, Math.round(durationInFrames * 0.7));
	const perStep = steps.length > 0 ? usable / steps.length : usable;

	return (
		<div
			style={{
				...rootStyle(theme),
				justifyContent: 'center',
				padding: `${m.space(8)}px ${m.space(9)}px`,
				gap: m.space(3),
			}}
		>
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

			<div style={{display: 'flex', flexDirection: 'column', gap: m.space(2.2)}}>
				{steps.map((step, i) => {
					const start = Math.round(i * perStep);
					const progress = interpolate(
						frame,
						[start, start + Math.round(fps * 0.45)],
						[0, 1],
						{extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
					);
					return (
						<div
							key={`${step.label}-${i}`}
							style={{
								display: 'flex',
								alignItems: 'flex-start',
								gap: m.space(2),
								opacity: progress,
								transform: `translateX(${(1 - progress) * 2}vmin)`,
							}}
						>
							<div
								style={{
									flex: '0 0 auto',
									width: m.space(4.6),
									height: m.space(4.6),
									borderRadius: '50%',
									display: 'flex',
									alignItems: 'center',
									justifyContent: 'center',
									fontSize: m.fontSize('body'),
									fontWeight: 700,
									color: theme.background_color,
									backgroundColor: theme.secondary_color,
								}}
							>
								{/* A step's ordinal is positional, not a computed value —
								    there is no expression behind it and none is needed. */}
								{i + 1}
							</div>
							<div style={{display: 'flex', flexDirection: 'column', gap: m.space(0.5)}}>
								<div style={{fontSize: m.fontSize('body'), fontWeight: 600, lineHeight: 1.3}}>
									{step.label}
								</div>
								{step.detail ? (
									<div
										style={{
											fontSize: m.fontSize('caption'),
											color: theme.muted_color,
											lineHeight: 1.4,
											maxWidth: '86%',
										}}
									>
										{step.detail}
									</div>
								) : null}
							</div>
						</div>
					);
				})}
			</div>
		</div>
	);
};
