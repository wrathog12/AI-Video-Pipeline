/**
 * Composition registry.
 *
 * A single "Scene" composition is rendered once per scene, with dimensions,
 * fps and durationInFrames supplied per invocation via --props. That is what
 * makes resolution and aspect ratio configuration rather than code (R7):
 * calculateMetadata reads them from props instead of hardcoding a size.
 *
 * Renders VIDEO ONLY. Audio is assembled as one continuous track by the Python
 * pipeline and muxed at the end — see python_pipeline/audio_track.py for why.
 */

import React from 'react';
import {Composition} from 'remotion';

import {SceneDispatcher} from './SceneDispatcher';
import {DEFAULT_THEME} from './theme';
import type {SceneProps} from './types';

interface SceneCompProps extends SceneProps {
	width: number;
	height: number;
	fps: number;
	duration_in_frames: number;
}

const DEFAULT_PROPS: SceneCompProps = {
	scene_id: 'scene_preview',
	template_name: 'ExpressionCard',
	narration_text:
		'Mathematically, this means two to the eighth power, resulting in 256 possible combinations per channel.',
	props: {
		title: 'Bit depth',
		expression: {
			label: 'Two to the eighth power',
			expr: '2**8',
			format: 'int',
			unit: 'levels per channel',
			resolved: '256',
		},
		items: [
			{label: 'Range', expr: null, format: 'range', unit: null, resolved: '0–255'},
		],
	},
	// Exercises the icon path in `npx remotion studio` without needing a pipeline
	// run. Empty would preview the null-provider layout, which is the case least in
	// need of eyeballing — a wrongly placed icon is what someone opens the studio
	// to catch.
	assets: [
		{kind: 'svg', id: 'artist_palette', path: 'icons/artist_palette.svg', cue_word: 'channel'},
	],
	word_triggers: [
		{word: '256', start_ms: 3200, end_ms: 3800},
		{word: 'channel', start_ms: 4600, end_ms: 5100},
		{word: '0–255', start_ms: 5200, end_ms: 5800},
	],
	theme: DEFAULT_THEME,
	orientation: 'landscape',
	output_scale: 1,
	width: 1920,
	height: 1080,
	fps: 30,
	duration_in_frames: 240,
};

export const RemotionRoot: React.FC = () => {
	return (
		<Composition
			id="Scene"
			component={SceneDispatcher as React.FC<Record<string, unknown>>}
			defaultProps={DEFAULT_PROPS as unknown as Record<string, unknown>}
			// Placeholders; calculateMetadata overrides all four from props.
			durationInFrames={240}
			fps={30}
			width={1920}
			height={1080}
			calculateMetadata={({props}) => {
				const p = props as unknown as SceneCompProps;
				return {
					durationInFrames: Math.max(1, Math.round(p.duration_in_frames ?? 240)),
					fps: p.fps ?? 30,
					width: p.width ?? 1920,
					height: p.height ?? 1080,
				};
			}}
		/>
	);
};
