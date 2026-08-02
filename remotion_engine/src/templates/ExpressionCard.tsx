/**
 * ExpressionCard — a headline expression with its computed result, plus optional
 * supporting items.
 *
 * Props are deliberately topic-agnostic: {title, expression, items}. It renders
 * 2^8 = 256 and "quarterly revenue grew 23%" with equal indifference, because it
 * never inspects what the value means. A prop shape like {r, g, b} would make
 * this template useless for any script but Script A.
 *
 * The component displays `resolved` and never evaluates `expr` — arithmetic is
 * Python's job (R4).
 */

import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

import {useMetrics, rootStyle} from '../theme';
import {SceneIcon} from '../components/SceneIcon';
import {ValueBlock, triggerFor} from '../components/ValueBlock';
import {useCueProgress} from '../components/WordCue';
import type {SceneProps, Value} from '../types';

interface ExpressionProps {
	title?: string;
	expression?: Value;
	items?: Value[];
}

export const ExpressionCard: React.FC<SceneProps> = ({
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
	const p = props as ExpressionProps;

	const expression = p.expression;
	const items = p.items ?? [];

	// Entry animation, driven purely by frame number.
	const enter = interpolate(frame, [0, Math.round(fps * 0.5)], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});

	// Anchor the result to the word the annotator named (R5). triggerFor falls
	// back to matching the resolved text for specs written before cue_word existed.
	const resultProgress = useCueProgress(triggerFor(expression, word_triggers), {
		durationMs: 450,
	});

	return (
		<div
			style={{
				...rootStyle(theme),
				justifyContent: 'center',
				padding: `${m.space(8)}px ${m.space(9)}px`,
				gap: m.space(4),
			}}
		>
			<SceneIcon assets={assets} triggers={word_triggers} m={m} size={12} />
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

			{expression ? (
				<div style={{display: 'flex', flexDirection: 'column', gap: m.space(2)}}>
					{expression.label ? (
						<div
							style={{
								fontSize: m.fontSize('lead'),
								color: theme.text_color,
								opacity: enter,
							}}
						>
							{expression.label}
						</div>
					) : null}

					<div
						style={{
							display: 'flex',
							alignItems: 'baseline',
							gap: m.space(2.5),
							flexWrap: 'wrap',
						}}
					>
						<div
							style={{
								fontSize: m.fontSize('display'),
								fontWeight: 700,
								color: theme.primary_color,
								opacity: resultProgress,
								transform: `translateY(${(1 - resultProgress) * 1.2}vmin)`,
							}}
						>
							{/* A null resolved means the evaluator did not run: show the
							    fault rather than silently rendering nothing. */}
							{expression.resolved ?? '—'}
						</div>
						{expression.unit ? (
							<div
								style={{
									fontSize: m.fontSize('lead'),
									color: theme.muted_color,
									opacity: resultProgress,
								}}
							>
								{expression.unit}
							</div>
						) : null}
					</div>

					<div
						style={{
							height: Math.max(2, m.space(0.35)),
							width: `${30 * enter}%`,
							backgroundColor: theme.secondary_color,
							borderRadius: 999,
						}}
					/>
				</div>
			) : null}

			{items.length > 0 ? (
				<div
					style={{
						display: 'flex',
						flexDirection: m.isPortrait ? 'column' : 'row',
						gap: m.space(3),
						flexWrap: 'wrap',
					}}
				>
					{items.map((item, i) => (
						<div
							key={`${item.label}-${i}`}
							style={{
								padding: `${m.space(2)}px ${m.space(2.6)}px`,
								borderRadius: m.space(1.2),
								border: `1px solid ${theme.muted_color}44`,
								minWidth: m.space(24),
							}}
						>
							<ValueBlock
								value={item}
								triggers={word_triggers}
								theme={theme}
								m={m}
								size="title"
							/>
						</div>
					))}
				</div>
			) : null}
		</div>
	);
};
