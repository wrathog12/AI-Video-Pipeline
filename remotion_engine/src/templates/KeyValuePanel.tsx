/**
 * KeyValuePanel — two to four related values, no single headline.
 *
 * Props: {title?, items: Value[]}.
 *
 * The grid is derived from the item count rather than fixed, because a two-item
 * panel laid out on a four-column grid leaves half the frame empty. Column count
 * also collapses to one in portrait, which is the whole reason sizes are in vmin.
 */

import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

import {ValueBlock} from '../components/ValueBlock';
import {useMetrics, rootStyle} from '../theme';
import type {SceneProps, Value} from '../types';

interface PanelProps {
	title?: string;
	items?: Value[];
}

export const KeyValuePanel: React.FC<SceneProps> = ({
	props,
	theme,
	orientation,
	output_scale,
	word_triggers,
}) => {
	const m = useMetrics(theme, orientation, output_scale);
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();
	const p = props as PanelProps;

	const items = (p.items ?? []).slice(0, 4);
	const enter = interpolate(frame, [0, Math.round(fps * 0.5)], [0, 1], {
		extrapolateLeft: 'clamp',
		extrapolateRight: 'clamp',
	});

	const columns = m.isPortrait ? 1 : Math.min(items.length || 1, 2);

	return (
		<div
			style={{
				...rootStyle(theme),
				justifyContent: 'center',
				padding: `${m.space(8)}px ${m.space(9)}px`,
				gap: m.space(4),
			}}
		>
			{p.title ? (
				<div
					style={{
						fontSize: m.fontSize('lead'),
						fontWeight: 600,
						opacity: enter,
						transform: `translateY(${(1 - enter) * 1.2}vmin)`,
					}}
				>
					{p.title}
				</div>
			) : null}

			<div
				style={{
					display: 'grid',
					gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
					gap: m.space(3),
				}}
			>
				{items.map((item, i) => (
					<div
						key={`${item.label}-${i}`}
						style={{
							padding: `${m.space(2.2)}px ${m.space(2.6)}px`,
							borderRadius: m.space(1.2),
							border: `1px solid ${theme.muted_color}33`,
							backgroundColor: `${theme.muted_color}0D`,
						}}
					>
						<ValueBlock
							value={item}
							triggers={word_triggers}
							theme={theme}
							m={m}
							size="title"
							accent={i === 0}
						/>
					</div>
				))}
			</div>
		</div>
	);
};
