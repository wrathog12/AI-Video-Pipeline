/**
 * KeyValuePanel — two to four related values, no single headline.
 *
 * Props: {title?, items: Value[]}.
 *
 * The grid is derived from the item count rather than fixed, because a two-item
 * panel laid out on a four-column grid leaves half the frame empty. Column count
 * also collapses to one in portrait, which is the whole reason sizes are in vmin.
 *
 * When the items carry `channels` — tuple-valued results, which the evaluator
 * fills — the panel renders swatches instead of text boxes, so a scene about
 * colour actually shows colour. The switch is driven by the data, not by the
 * topic: `SwatchStrip` returns null when nothing has channels, so this costs a
 * numeric panel nothing and no template needs to know what the script is about.
 */

import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

import {SceneIcon} from '../components/SceneIcon';
import {SwatchStrip} from '../components/SwatchStrip';
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
	assets,
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
	// Any tuple-valued item makes this a swatch scene. Mixing painted swatches and
	// bordered text boxes in one panel reads as two unrelated layouts, so it is one
	// or the other.
	const drawable = items.filter((v) => (v.channels ?? []).length > 0);
	const asSwatches = drawable.length > 0;

	return (
		<div
			style={{
				...rootStyle(theme),
				justifyContent: 'center',
				padding: `${m.space(8)}px ${m.space(9)}px`,
				gap: m.space(4),
			}}
		>
			{/* bottom-right: the swatch row and the value grid both grow downward from
			    the title, so the top-right corner is where a wrapped item lands. */}
			<SceneIcon
				assets={assets}
				triggers={word_triggers}
				m={m}
				size={12}
				corner="bottom-right"
			/>
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

			{asSwatches ? (
				<SwatchStrip
					values={items}
					triggers={word_triggers}
					theme={theme}
					m={m}
					size={items.length > 3 ? 11 : 15}
				/>
			) : (
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
			)}
		</div>
	);
};
