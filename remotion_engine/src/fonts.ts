/**
 * Registers the vendored typefaces so `theme.font_family` actually selects one.
 *
 * ## Why this file exists
 *
 * `theme.font_family` has been config since Phase 0, but nothing ever loaded a
 * font: `remotion_engine/fonts/` held only `.gitkeep`, so the CSS stack in
 * `rootStyle` fell straight through `Inter` to `system-ui`. Every frame rendered
 * before this used headless Chromium's default face while config, the theme hash
 * and the docs all said otherwise. A missing family degrades to readable text
 * rather than erroring, which is exactly why it went unnoticed.
 *
 * ## Why @font-face and not @remotion/google-fonts
 *
 * `@remotion/google-fonts` fetches at render time, making the frame a function of
 * Google's CDN — the determinism hazard `context.md` §6 already rules out for
 * fonts. These files are vendored into `public/fonts/` by
 * `python -m python_pipeline.vendor_fonts` and committed, so the bytes are pinned
 * and auditable via `manifest.json`.
 *
 * ## Why the families are hardcoded here
 *
 * This list must match `python_pipeline/vendor_fonts.CATALOG`, and that duplication
 * is deliberate: the alternative is reading the manifest at module scope, which
 * makes font registration depend on an fs read inside the bundle. A family listed
 * here whose file is absent simply fails to load and falls back — the same
 * degradation as before, no worse. The dashboard only offers families that are
 * present on disk (`vendor_fonts.available_families`), so the two lists cannot
 * drift in the direction that matters.
 */

import {continueRender, delayRender, staticFile} from 'remotion';

interface VendoredFont {
	family: string;
	file: string;
	/** Range the variable font's weight axis actually covers. */
	weight: string;
}

export const VENDORED_FONTS: VendoredFont[] = [
	{family: 'Inter', file: 'Inter[opsz,wght].ttf', weight: '100 900'},
	{family: 'Source Serif 4', file: 'SourceSerif4[opsz,wght].ttf', weight: '200 900'},
	{family: 'JetBrains Mono', file: 'JetBrainsMono[wght].ttf', weight: '100 800'},
];

let started = false;

/**
 * Load every vendored family and hold the render until they are ready.
 *
 * `delayRender` is the load-bearing part. Remotion screenshots a frame as soon as
 * React has painted; a font that resolves a few milliseconds later would land in
 * some frames and not others, producing a video whose typography changes partway
 * through and whose two runs differ — an R3 failure that looks like a rendering
 * glitch. Blocking until `font.load()` settles makes the timing irrelevant.
 *
 * A failed load calls `continueRender` anyway. Refusing to render because a
 * decorative resource is missing would be the R2 mistake the SceneIcon `onError`
 * path already avoids: fallback typography is a worse frame, no frame is no video.
 */
export const ensureFontsLoaded = (): void => {
	if (started || typeof document === 'undefined') {
		return;
	}
	started = true;

	for (const font of VENDORED_FONTS) {
		const handle = delayRender(`load font ${font.family}`);
		const face = new FontFace(font.family, `url(${staticFile(`fonts/${font.file}`)})`, {
			weight: font.weight,
			style: 'normal',
		});
		face
			.load()
			.then((loaded) => {
				document.fonts.add(loaded);
				continueRender(handle);
			})
			.catch((error: unknown) => {
				// eslint-disable-next-line no-console
				console.warn(
					`[fonts] ${font.family} failed to load, falling back:`,
					error instanceof Error ? error.message : error,
				);
				continueRender(handle);
			});
	}
};
