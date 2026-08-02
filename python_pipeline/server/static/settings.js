/*
 * Settings page: the R7 configurable surface, as controls.
 *
 * ## Why the form is built from the server's answer, not hardcoded here
 *
 * Which providers exist, which of them can run, and which fonts are on disk are all
 * facts about the checkout — not constants. `/api/env` probes them per request, so
 * vendoring an icon pack or dropping a key into `.env` changes this page on reload
 * with no code edit. Hardcoding the option lists would put the same list in two
 * files, and the copy in the browser is the one nobody updates.
 *
 * ## Why unavailable providers are shown disabled but unavailable fonts are omitted
 *
 * A provider seam is architecture worth seeing even when one end is unbuilt — that
 * `piper` appears greyed out with "module is a stub" tells a reviewer the interface is
 * real and the implementation is not. A greyed-out typeface tells them nothing, so
 * `font_options()` simply does not return it.
 *
 * ## Pending vs. saved
 *
 * Edits accumulate in localStorage as a flat {dotted.path: value} patch, containing
 * only fields that differ from config.yaml. `Run with these` leaves them there for
 * the timeline page to send; `Save to config.yaml` POSTs them and clears the patch,
 * because once the file says it, it is no longer an override.
 */

const COLOURS = [
	['theme.primary_color', 'Primary', 'accents, rules, emphasis'],
	['theme.secondary_color', 'Secondary', 'second series, sub-pixel green/blue'],
	['theme.background_color', 'Background', 'the canvas'],
	['theme.text_color', 'Text', 'primary type'],
	['theme.muted_color', 'Muted', 'labels and captions'],
];

const PRESETS = [
	['1920 × 1080', 1920, 1080, '16:9 default'],
	['1080 × 1920', 1080, 1920, '9:16 — prefer the profile'],
	['1080 × 1080', 1080, 1080, '1:1'],
];

const st = {
	env: null,
	saved: {},     // config.yaml as it is on disk
	pending: loadPending(),
};

const dom = {
	badge: document.getElementById('dirty-badge'),
	notice: document.getElementById('notice'),
	apply: document.getElementById('apply'),
	save: document.getElementById('save'),
	revert: document.getElementById('revert'),
	swatches: document.getElementById('swatches'),
	presets: document.getElementById('presets'),
	preview: document.getElementById('preview'),
	geometry: document.getElementById('geometry'),
	keys: document.getElementById('keys'),
	env: document.getElementById('env'),
};

// --- boot -----------------------------------------------------------------

async function boot() {
	try {
		const [env, config] = await Promise.all([api('/api/env'), api('/api/config')]);
		st.env = env;
		st.saved = config.values;
	} catch (error) {
		return notice('err', `Could not load settings: ${error.message}`);
	}

	fillProviders();
	fillFonts();
	buildSwatches();
	buildPresets();
	showKeys();

	for (const node of document.querySelectorAll('[data-field]')) {
		node.value = valueOf(node.id) ?? '';
		node.addEventListener('change', () => setField(node.id, node.value));
		node.addEventListener('input', () => setField(node.id, node.value, {quiet: true}));
	}
	refresh();
}

/** Effective value: pending override if there is one, else config.yaml. */
function valueOf(path) {
	return path in st.pending ? st.pending[path] : st.saved[path];
}

function setField(path, raw, {quiet} = {}) {
	const saved = st.saved[path];
	// Numbers arrive from inputs as strings; compare loosely so retyping the value
	// config.yaml already holds does not register as a pending change.
	const same = typeof saved === 'number' ? Number(raw) === saved : raw === saved;
	if (same) delete st.pending[path];
	else st.pending[path] = typeof saved === 'number' ? Number(raw) : raw;
	savePending(st.pending);
	refresh({quiet});
}

// --- option lists ---------------------------------------------------------

function fillProviders() {
	for (const [key, why] of [
		['llm', 'why-llm'],
		['tts', 'why-tts'],
		['aligner', 'why-aligner'],
		['assets', 'why-assets'],
	]) {
		const select = document.getElementById(`providers.${key}`);
		if (!select) continue;
		clear(select);
		const current = valueOf(`providers.${key}`);
		let currentIsListed = false;
		for (const option of st.env.providers[key] || []) {
			if (option.value === current) currentIsListed = true;
			select.append(
				el(
					'option',
					{
						value: option.value,
						disabled: !option.available && option.value !== current,
						title: option.reason || '',
					},
					option.available
						? `${option.label} — ${option.description}`
						: `${option.label} — unavailable: ${option.reason}`,
				),
			);
		}
		// config.yaml may name something the probe does not know about (any
		// `gemini-*` id works by vendor prefix). Keep it rather than silently
		// switching the user's provider by rendering a select that excludes it.
		if (current && !currentIsListed) {
			select.append(
				el('option', {value: current}, `${current} — from config.yaml`),
			);
		}
		if (key !== 'llm') {
			const node = document.getElementById(why);
			if (node) node.textContent = reasonFor(key, current);
		}
		select.addEventListener('change', () => {
			const node = document.getElementById(why);
			if (node && key !== 'llm') node.textContent = reasonFor(key, select.value);
		});
	}
}

function reasonFor(group, value) {
	const option = (st.env.providers[group] || []).find((o) => o.value === value);
	if (!option) return '';
	return option.available ? option.reason : `unavailable — ${option.reason}`;
}

function fillFonts() {
	const select = document.getElementById('theme.font_family');
	clear(select);
	const current = valueOf('theme.font_family');
	const fonts = st.env.fonts || [];
	for (const font of fonts) {
		select.append(
			el(
				'option',
				{value: font.value, disabled: !font.available},
				font.available ? font.label : `${font.label} — ${font.reason}`,
			),
		);
	}
	if (current && !fonts.some((f) => f.value === current)) {
		select.append(el('option', {value: current}, `${current} — not vendored`));
	}
	const why = document.getElementById('why-font');
	why.textContent = fonts.length
		? `${fonts.length} vendored: ${fonts.map((f) => f.value).join(', ')}. Loaded via delayRender so frame 0 never captures a fallback.`
		: 'None vendored — run `python -m python_pipeline.vendor_fonts`.';
}

function buildSwatches() {
	clear(dom.swatches);
	for (const [path, label, blurb] of COLOURS) {
		// No `data-field`: these carry their own listener below, and the generic
		// binding would also fire with the browser's lowercase `#e63946`, marking
		// every colour dirty the moment the page loaded.
		const input = el('input', {
			type: 'color',
			id: path,
			value: String(valueOf(path) || '#000000').toLowerCase(),
		});
		const hex = el('div', {class: 'hex'}, String(valueOf(path) || ''));
		input.addEventListener('input', () => {
			// <input type=color> yields lowercase; config.yaml holds uppercase and the
			// coercer uppercases too, so normalise here or every colour reads dirty.
			const value = input.value.toUpperCase();
			hex.textContent = value;
			setField(path, value, {quiet: true});
		});
		dom.swatches.append(
			el(
				'label',
				{class: 'swatch'},
				input,
				el(
					'div',
					{class: 'meta'},
					el('div', {class: 'name'}, label),
					hex,
					el('div', {class: 'muted small'}, blurb),
				),
			),
		);
	}
}

function buildPresets() {
	clear(dom.presets);
	for (const [label, width, height, blurb] of PRESETS) {
		dom.presets.append(
			el(
				'button',
				{
					title: blurb,
					onclick: () => {
						setField('project.width', width);
						setField('project.height', height);
						document.getElementById('project.width').value = width;
						document.getElementById('project.height').value = height;
					},
				},
				label,
			),
		);
	}
}

function showKeys() {
	clear(dom.keys);
	const names = st.env.env_keys_loaded || [];
	if (names.length === 0) {
		dom.keys.append(
			el(
				'span',
				{class: 'muted small'},
				'No keys loaded from .env. The heuristic annotator and edge-tts both run without one.',
			),
		);
	}
	for (const name of names) {
		dom.keys.append(el('span', {class: 'badge succeeded'}, `${name} present`));
	}
	dom.env.textContent = `${st.env.icons_vendored} icons vendored · config: ${st.env.config_path}`;
}

// --- live derived views ---------------------------------------------------

function refresh({quiet} = {}) {
	const count = Object.keys(st.pending).length;
	dom.badge.className = `badge ${count ? 'running' : 'off'}`;
	dom.badge.textContent = count
		? `${count} pending change${count === 1 ? '' : 's'}`
		: 'no pending changes';
	if (!quiet) dom.notice.hidden = true;
	paintPreview();
	paintGeometry();
}

function paintPreview() {
	const font = valueOf('theme.font_family');
	const base = Number(valueOf('theme.type_scale_base_vmin')) || 3.2;
	const bg = valueOf('theme.background_color');
	const primary = valueOf('theme.primary_color');
	const text = valueOf('theme.text_color');
	const muted = valueOf('theme.muted_color');
	const secondary = valueOf('theme.secondary_color');

	dom.preview.style.background = bg;
	dom.preview.style.fontFamily = `"${font}", system-ui, sans-serif`;
	// The engine sizes type in vmin of a 1920x1080 canvas. This block is a few
	// hundred px wide, so vmin here would be wrong by an order of magnitude —
	// scale the same ratios into px against the 1080 short edge instead.
	const px = (multiple) => `${(base * multiple * 1080) / 100 / 3.4}px`;
	dom.preview.querySelector('.rule').style.background = primary;
	Object.assign(dom.preview.querySelector('.h').style, {color: text, fontSize: px(2.4)});
	Object.assign(dom.preview.querySelectorAll('.s')[0].style, {
		color: muted,
		fontSize: px(1.1),
	});
	Object.assign(dom.preview.querySelector('.v').style, {
		color: primary,
		fontSize: px(4.2),
	});
	Object.assign(dom.preview.querySelectorAll('.s')[1].style, {
		color: secondary,
		fontSize: px(1.0),
	});
}

function paintGeometry() {
	const width = Number(valueOf('project.width'));
	const height = Number(valueOf('project.height'));
	const scale = Number(valueOf('project.output_scale'));
	const fps = Number(valueOf('project.fps'));
	const outW = Math.round(width * scale);
	const outH = Math.round(height * scale);
	const shortEdge = Math.min(outW, outH);
	const base = Number(valueOf('theme.type_scale_base_vmin')) || 3.2;
	const floor = Number(valueOf('theme.min_font_px')) || 22;
	// One body-copy size, in the units the renderer uses, after scaling. This is the
	// number that decides whether the smallest label on screen is legible.
	const bodyPx = (base * 1.0 * shortEdge) / 100;

	clear(dom.geometry);
	const add = (k, v) => dom.geometry.append(el('dt', {}, k), el('dd', {}, v));
	add('canvas', `${width} × ${height}`);
	add('delivered', `${outW} × ${outH} @ ${fps}fps`);
	add('aspect', ratio(width, height));
	add(
		'body copy',
		`${bodyPx.toFixed(1)}px → ${Math.max(bodyPx, floor).toFixed(1)}px after the ${floor}px floor`,
	);
	document.getElementById('why-scale').textContent =
		`Applied at render time. ${scale} × ${width} = ${outW}px wide.` +
		(bodyPx < floor
			? ` At this scale body copy would fall to ${bodyPx.toFixed(1)}px, so the ${floor}px floor is doing the work.`
			: '');
}

function ratio(width, height) {
	const gcd = (a, b) => (b ? gcd(b, a % b) : a);
	const d = gcd(width, height) || 1;
	return `${width / d}:${height / d}`;
}

function notice(kind, message) {
	dom.notice.hidden = false;
	dom.notice.className = kind === 'err' ? 'errbox' : 'warnbox';
	clear(dom.notice);
	for (const line of [].concat(message)) {
		dom.notice.append(el('div', {}, line));
	}
}

// --- actions --------------------------------------------------------------

dom.apply.addEventListener('click', () => {
	savePending(st.pending);
	const count = Object.keys(st.pending).length;
	notice(
		'warn',
		count
			? `${count} override${count === 1 ? '' : 's'} staged. The next run writes them to its own config.yaml; the committed file is untouched.`
			: 'Nothing to override — the next run uses config.yaml as committed.',
	);
});

dom.save.addEventListener('click', async () => {
	if (Object.keys(st.pending).length === 0) {
		return notice('warn', 'No changes to save.');
	}
	dom.save.disabled = true;
	try {
		const result = await api('/api/config', {
			method: 'POST',
			body: JSON.stringify({overrides: st.pending}),
		});
		// Saved values are no longer overrides. Fold them into `saved` and clear the
		// patch, or the timeline page would keep sending a per-run override that is
		// now identical to the file.
		Object.assign(st.saved, st.pending);
		st.pending = {};
		savePending(st.pending);
		notice('warn', [`Written to config.yaml:`, ...(result.changed || [])]);
		refresh({quiet: true});
	} catch (error) {
		notice('err', `Rejected, nothing written: ${error.message}`);
	} finally {
		dom.save.disabled = false;
	}
});

dom.revert.addEventListener('click', () => {
	st.pending = {};
	savePending(st.pending);
	for (const node of document.querySelectorAll('[data-field]')) {
		node.value = st.saved[node.id] ?? '';
	}
	buildSwatches();
	refresh();
	notice('warn', 'Pending changes discarded. Fields show config.yaml as committed.');
});

boot();
