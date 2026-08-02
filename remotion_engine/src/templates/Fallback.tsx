/**
 * Fallback — the template that must never fail.
 *
 * Mounted whenever `template_name` is unknown or a template's props are shaped
 * unexpectedly. On an unseen script (R2) the realistic failure is not an ugly
 * scene, it is a crashed render that produces no video at all in front of the
 * reviewer. This degrades instead: narration text on the theme background,
 * legible, with any values it can find.
 *
 * It reads nothing beyond `narration_text`, so it cannot itself throw.
 */

import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

import {useMetrics, rootStyle} from '../theme';
import type {SceneProps, Value} from '../types';

const isValue = (v: unknown): v is Value =>
	typeof v === 'object' && v !== null && 'resolved' in (v as Record<string, unknown>);

/** Pull any resolved values out of an arbitrary props object, one level deep. */
const collectValues = (props: Record<string, unknown>): Value[] => {
	const out: Value[] = [];
	for (const v of Object.values(props ?? {})) {
		if (isValue(v)) {
			out.push(v);
		} else if (Array.isArray(v)) {
			for (const item of v) {
				if (isValue(item)) out.push(item);
			}
		}
	}
	return out.filter((v) => v.resolved);
};

export const Fallback: React.FC<SceneProps> = ({
	props,
	narration_text,
	theme,
	orientation,
	output_scale,
}) => {
	const m = useMetrics(theme, orientation, output_scale);
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();

	const enter = interpolate(frame, [0, Math.round(fps * 0.6)], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});

	const title = typeof props?.title === 'string' ? props.title : null;
	const values = collectValues(props ?? {});

	// Long narration at a large size overflows; step down once past a threshold.
	const step = narration_text.length > 220 ? 'body' : 'lead';

	return (
		<div
			style={{
				...rootStyle(theme),
				justifyContent: 'center',
				padding: `${m.space(9)}px ${m.space(10)}px`,
				gap: m.space(3),
			}}
		>
			{title ? (
				<div
					style={{
						fontSize: m.fontSize('caption'),
						letterSpacing: '0.14em',
						textTransform: 'uppercase',
						color: theme.muted_color,
						opacity: enter,
					}}
				>
					{title}
				</div>
			) : null}

			<div
				style={{
					fontSize: m.fontSize(step),
					lineHeight: 1.45,
					opacity: enter,
					transform: `translateY(${(1 - enter) * 1.2}vmin)`,
					// Clamp rather than overflow: clipped text is an R4 failure.
					display: '-webkit-box',
					WebkitLineClamp: 8,
					WebkitBoxOrient: 'vertical',
					overflow: 'hidden',
				}}
			>
				{narration_text}
			</div>

			{values.length > 0 ? (
				<div style={{display: 'flex', gap: m.space(3), flexWrap: 'wrap', opacity: enter}}>
					{values.map((v, i) => (
						<div
							key={i}
							style={{
								fontSize: m.fontSize('title'),
								fontWeight: 700,
								color: theme.primary_color,
							}}
						>
							{v.resolved}
						</div>
					))}
				</div>
			) : null}
		</div>
	);
};
