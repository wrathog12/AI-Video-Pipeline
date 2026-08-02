/**
 * SceneIcon — the one vendored glyph a scene is allowed, cued to the narration.
 *
 * ## It is a label, not the content
 *
 * The swatch strip and the comparison bars *encode* values, so they are content.
 * An icon only names the topic. That distinction sets every visual decision here:
 * the glyph is small, it sits in a corner, and the layout does not depend on it.
 * A big centred emoji would compete with the figure the scene exists to show and
 * would read as clip-art rather than illustration.
 *
 * ## Nothing about it may move the layout
 *
 * Absolutely positioned, on purpose. If a scene's icon were part of the flow,
 * whether the provider matched a keyword would change where the text sits — so the
 * same script would compose differently under `null` and `icon_pack`, and the
 * templates' "looks complete with assets: []" property (see assets/null.py) would
 * quietly stop holding. Positioned out of flow, an unmatched keyword costs exactly
 * nothing.
 *
 * ## Colour emoji, restrained
 *
 * Noto colour glyphs are glossy and saturated; at full strength beside a
 * typographic value card they cheapen the frame. So the glyph renders below full
 * opacity with a slight desaturation, which keeps it legible as an object while
 * letting the theme stay dominant. The numbers remain the brightest thing on
 * screen, which is the correct visual hierarchy for an explainer.
 */

import React from 'react';
import {Img, interpolate, staticFile} from 'remotion';

import {findTrigger, useCueProgress} from './WordCue';
import type {Metrics} from '../theme';
import type {AssetRef, WordTrigger} from '../types';

/** Corner placement. Templates pass whichever corner their content leaves free. */
export type IconCorner = 'top-right' | 'bottom-right' | 'top-left';

const cornerStyle = (corner: IconCorner, inset: number): React.CSSProperties => {
	if (corner === 'top-left') return {top: inset, left: inset};
	if (corner === 'bottom-right') return {bottom: inset, right: inset};
	return {top: inset, right: inset};
};

/**
 * Pick the first drawable asset.
 *
 * `kind` is checked rather than assumed: the AssetRef union includes `none`, and a
 * provider is allowed to return one — that is how "I looked and found nothing"
 * differs from "I did not look". Neither should render a broken image.
 */
export const firstDrawable = (assets: AssetRef[] | undefined): AssetRef | null =>
	(assets ?? []).find(
		(a) => (a.kind === 'svg' || a.kind === 'image') && Boolean(a.path),
	) ?? null;

export const SceneIcon: React.FC<{
	assets: AssetRef[] | undefined;
	triggers: WordTrigger[];
	m: Metrics;
	/** Side length in vmin units. */
	size?: number;
	corner?: IconCorner;
}> = ({assets, triggers, m, size = 13, corner = 'top-right'}) => {
	const asset = firstDrawable(assets);
	// Hooks must run unconditionally, so the cue is resolved before the early
	// return. findTrigger(_, '') is undefined, and useCueProgress(undefined) is 1,
	// so the no-asset case is cheap and has no special branch.
	const trigger = findTrigger(triggers, asset?.cue_word ?? '');
	const progress = useCueProgress(trigger, {durationMs: 520, leadMs: 120});

	// Remotion's <Img> treats a failed load as a render error, which would turn a
	// single missing SVG into no video at all — the exact R2 failure the whole
	// null-provider design exists to avoid. `onError` downgrades it to "no icon".
	//
	// The Python provider already checks the file exists before emitting a ref, so
	// this should be unreachable on the script path. It is reachable via --spec: a
	// spec written on another machine, or before the pack was vendored, names a path
	// that may not be here. An artifact that renders everywhere except where a file
	// happens to be missing is not a portable artifact.
	const [failed, setFailed] = React.useState(false);

	if (!asset?.path || failed) return null;

	const box = m.space(size);
	// Drifts in as the word is spoken. A pure function of cue progress, so it stays
	// deterministic under the wall-clock ban (.eslintrc.cjs enforces that).
	const scale = interpolate(progress, [0, 1], [0.86, 1]);
	const drift = (1 - progress) * m.space(1.2);

	return (
		<div
			style={{
				position: 'absolute',
				...cornerStyle(corner, m.space(6)),
				width: box,
				height: box,
				// Capped below 1: the glyph must never out-shout the figure it labels.
				opacity: progress * 0.82,
				transform: `scale(${scale}) translateY(${drift}px)`,
				transformOrigin: corner === 'top-left' ? 'top left' : 'top right',
				pointerEvents: 'none',
				willChange: 'opacity, transform',
			}}
		>
			<Img
				src={staticFile(asset.path)}
				alt=""
				// Passing onError is what stops Remotion throwing on a failed load.
				onError={() => setFailed(true)}
				style={{
					width: '100%',
					height: '100%',
					objectFit: 'contain',
					// Pulls the saturation back toward the palette so a glossy emoji sits
					// with flat theme colours instead of on top of them.
					filter: 'saturate(0.86)',
				}}
			/>
		</div>
	);
};
