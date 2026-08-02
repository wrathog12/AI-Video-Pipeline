/**
 * Typography and layout tokens derived from config, never hardcoded.
 *
 * Every font size is a multiple of vmin (the smaller canvas dimension), so a
 * layout that fits 1920x1080 also fits 1080x1920 without per-aspect CSS. Sizes
 * are then clamped against `min_font_px` measured AFTER output_scale, because
 * legibility at 480p is what R4 actually grades — a 24px caption on a 1920px
 * canvas is 10px in the delivered file.
 */

import {useVideoConfig} from 'remotion';

import type {Orientation, Theme} from './types';

export const DEFAULT_THEME: Theme = {
	font_family: 'Inter',
	type_scale_base_vmin: 3.2,
	min_font_px: 22,
	primary_color: '#E63946',
	secondary_color: '#4EA8DE',
	background_color: '#0D1117',
	text_color: '#F8F9FA',
	muted_color: '#8B949E',
};

/** Ratios relative to the base step. */
export const SCALE = {
	caption: 0.7,
	body: 1,
	lead: 1.35,
	title: 2.1,
	display: 3.6,
	hero: 5.2,
} as const;

export type ScaleStep = keyof typeof SCALE;

export interface Metrics {
	vmin: number;
	fontSize: (step: ScaleStep) => number;
	space: (n: number) => number;
	orientation: Orientation;
	isPortrait: boolean;
	/** 'column' in portrait, 'row' in landscape — templates flip on this. */
	stackDirection: 'row' | 'column';
}

export const useMetrics = (theme: Theme, orientation: Orientation, outputScale = 1): Metrics => {
	const {width, height} = useVideoConfig();
	const vmin = Math.min(width, height) / 100;
	const base = theme.type_scale_base_vmin * vmin;

	const fontSize = (step: ScaleStep): number => {
		const raw = base * SCALE[step];
		// min_font_px is a floor on the DELIVERED pixel size, so convert it back
		// into canvas units before clamping.
		const floorOnCanvas = theme.min_font_px / Math.max(outputScale, 0.0001);
		return Math.max(raw, floorOnCanvas);
	};

	const isPortrait = orientation === 'portrait';
	return {
		vmin,
		fontSize,
		space: (n: number) => n * vmin,
		orientation,
		isPortrait,
		stackDirection: isPortrait ? 'column' : 'row',
	};
};

/** Root container styling shared by every template. */
export const rootStyle = (theme: Theme): React.CSSProperties => ({
	width: '100%',
	height: '100%',
	// Containing block for out-of-flow decoration (SceneIcon). Set here rather than
	// per template so an icon is positioned against its own scene's frame; without
	// it, absolute placement would resolve against Remotion's AbsoluteFill and a
	// template with its own padding would put the glyph in the wrong place.
	position: 'relative',
	backgroundColor: theme.background_color,
	color: theme.text_color,
	fontFamily: `${theme.font_family}, system-ui, -apple-system, "Segoe UI", sans-serif`,
	display: 'flex',
	flexDirection: 'column',
	// Tabular figures keep digits from jittering as a counter animates, which
	// otherwise reads as "mangled digits" on scrub.
	fontVariantNumeric: 'tabular-nums',
	WebkitFontSmoothing: 'antialiased',
	overflow: 'hidden',
});
