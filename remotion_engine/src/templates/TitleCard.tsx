/**
 * TitleCard — the opening scene.
 *
 * Props: {title, subtitle?}. Nothing numeric, so there is no R4 surface here;
 * the requirements are that it survives a missing subtitle and a title long
 * enough to wrap.
 */

import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

import {SceneIcon} from '../components/SceneIcon';
import {useMetrics, rootStyle} from '../theme';
import type {SceneProps} from '../types';

interface TitleProps {
	title?: string;
	subtitle?: string | null;
}

export const TitleCard: React.FC<SceneProps> = ({
	props,
	narration_text,
	theme,
	orientation,
	output_scale,
	assets,
	word_triggers,
}) => {
	const m = useMetrics(theme, orientation, output_scale);
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const p = props as TitleProps;

	const enter = interpolate(frame, [0, Math.round(fps * 0.7)], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});
	// The subtitle follows the title rather than arriving with it, so the eye has
	// somewhere to go.
	const second = interpolate(frame, [Math.round(fps * 0.5), Math.round(fps * 1.3)], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});

	const title = p.title || narration_text.split(/(?<=[.!?])\s/)[0];
	const subtitle = p.subtitle ?? null;

	return (
		<div
			style={{
				...rootStyle(theme),
				justifyContent: 'center',
				padding: `${m.space(9)}px ${m.space(10)}px`,
				gap: m.space(3),
			}}
		>
			<SceneIcon assets={assets} triggers={word_triggers} m={m} size={15} />
			<div
				style={{
					height: Math.max(3, m.space(0.6)),
					width: `${18 * enter}%`,
					backgroundColor: theme.primary_color,
					borderRadius: 999,
				}}
			/>
			<div
				style={{
					fontSize: m.fontSize('title'),
					fontWeight: 700,
					lineHeight: 1.12,
					letterSpacing: '-0.015em',
					opacity: enter,
					transform: `translateY(${(1 - enter) * 2}vmin)`,
				}}
			>
				{title}
			</div>
			{subtitle ? (
				<div
					style={{
						fontSize: m.fontSize('lead'),
						color: theme.muted_color,
						lineHeight: 1.4,
						maxWidth: '78%',
						opacity: second,
						transform: `translateY(${(1 - second) * 1.4}vmin)`,
					}}
				>
					{subtitle}
				</div>
			) : null}
		</div>
	);
};
