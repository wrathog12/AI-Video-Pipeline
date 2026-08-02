/*
 * Timeline page: launch a run, watch it, download what it made.
 *
 * ## Why the scene ribbon is proportional and not a list of equal boxes
 *
 * R6 asks for an artifact describing "segments, timings, visual elements". A list of
 * scene names shows the segments; only proportional widths show the timings, and the
 * thing worth *seeing* about a segmentation is that one scene is 20 s while its
 * neighbour is 5 s. Before the spec exists the widths are equal and the axis says so,
 * rather than faking a duration from the word count.
 *
 * ## Why the log is appended incrementally
 *
 * Progress arrives over SSE as individual lines, each carrying a monotonic seq. The
 * page appends; it never re-renders the log from scratch. Re-rendering would reset
 * scroll position on every line, which makes a 200-line render log unreadable.
 */

const state = {
	runId: null,
	stream: null,
	summary: null,
	spec: null,
	selectedScene: null,
};

const dom = {
	badge: document.getElementById('state-badge'),
	script: document.getElementById('script'),
	scriptText: document.getElementById('script-text'),
	spec: document.getElementById('spec'),
	profile: document.getElementById('profile'),
	dryRun: document.getElementById('dry-run'),
	noCache: document.getElementById('no-cache'),
	go: document.getElementById('go'),
	stop: document.getElementById('stop'),
	launchError: document.getElementById('launch-error'),
	settingsNote: document.getElementById('settings-note'),
	stages: document.getElementById('stages'),
	ribbon: document.getElementById('ribbon'),
	axis: document.getElementById('axis'),
	sceneDetail: document.getElementById('scene-detail'),
	timelineMeta: document.getElementById('timeline-meta'),
	log: document.getElementById('log'),
	follow: document.getElementById('follow'),
	warnings: document.getElementById('warnings'),
	downloads: document.getElementById('downloads'),
	outputHint: document.getElementById('output-hint'),
	player: document.getElementById('player'),
	history: document.getElementById('history'),
};

// --- boot -----------------------------------------------------------------

async function boot() {
	renderStages(null);
	try {
		const env = await api('/api/env');
		fillSelect(dom.script, env.scripts, {empty: '— none in scripts/ —'});
		fillSelect(dom.spec, env.specs, {empty: '— do not re-render a spec —', allowEmpty: true});
		fillSelect(dom.profile, env.profiles, {empty: '— default (16:9) —', allowEmpty: true});
	} catch (error) {
		showLaunchError(`Could not reach the server: ${error.message}`);
	}
	showPendingNote();
	await refreshHistory();

	// Reattach to a run that is already going, so reloading the page mid-render
	// does not orphan it.
	try {
		const {active} = await api('/api/runs');
		if (active) attach(active);
	} catch {
		/* history is optional */
	}
}

function fillSelect(select, values, {empty, allowEmpty} = {}) {
	clear(select);
	if (allowEmpty || !values || values.length === 0) {
		select.append(el('option', {value: ''}, empty || '—'));
	}
	for (const value of values || []) {
		select.append(el('option', {value}, value));
	}
}

function showPendingNote() {
	const pending = loadPending();
	const count = Object.keys(pending).length;
	dom.settingsNote.textContent = count
		? `${count} setting${count === 1 ? '' : 's'} overridden — see Settings`
		: 'Using config.yaml as committed';
}

function showLaunchError(message) {
	dom.launchError.hidden = false;
	dom.launchError.textContent = message;
}

// --- launching ------------------------------------------------------------

dom.go.addEventListener('click', async () => {
	dom.launchError.hidden = true;
	dom.go.disabled = true;
	const body = {
		script_name: dom.script.value || null,
		script_text: dom.scriptText.value || null,
		spec_name: dom.spec.value || null,
		profile: dom.profile.value || null,
		dry_run: dom.dryRun.checked,
		no_cache: dom.noCache.checked,
		overrides: loadPending(),
	};
	try {
		const summary = await api('/api/run', {method: 'POST', body: JSON.stringify(body)});
		resetView();
		attach(summary.run_id);
	} catch (error) {
		showLaunchError(error.message);
		dom.go.disabled = false;
	}
});

dom.stop.addEventListener('click', async () => {
	if (!state.runId) return;
	dom.stop.disabled = true;
	try {
		await api(`/api/runs/${state.runId}/cancel`, {method: 'POST'});
	} catch (error) {
		showLaunchError(error.message);
	}
});

function resetView() {
	clear(dom.log);
	clear(dom.warnings);
	clear(dom.downloads);
	clear(dom.player);
	clear(dom.sceneDetail);
	state.spec = null;
	state.selectedScene = null;
	dom.outputHint.textContent = 'Nothing yet.';
}

// --- streaming ------------------------------------------------------------

function attach(runId) {
	if (state.stream) state.stream.close();
	state.runId = runId;
	dom.go.disabled = true;
	dom.stop.disabled = false;

	const stream = new EventSource(`/api/runs/${runId}/events`);
	state.stream = stream;

	stream.addEventListener('line', (event) => appendLine(JSON.parse(event.data)));
	stream.addEventListener('summary', (event) => applySummary(JSON.parse(event.data)));
	stream.addEventListener('done', () => {
		stream.close();
		state.stream = null;
		dom.go.disabled = false;
		dom.stop.disabled = true;
		// The spec lands late in the run, so fetch it once the process is finished
		// rather than polling for it throughout.
		loadSpec(runId);
		refreshHistory();
	});
	stream.onerror = () => {
		// EventSource retries on its own; only report if the run is over and the
		// connection is genuinely dead.
		if (stream.readyState === EventSource.CLOSED) {
			state.stream = null;
			dom.go.disabled = false;
			dom.stop.disabled = true;
		}
	};
}

function appendLine(entry) {
	const atBottom =
		dom.log.scrollTop + dom.log.clientHeight >= dom.log.scrollHeight - 24;
	dom.log.append(el('span', {class: `ln ${entry.level}`}, entry.text));
	if (dom.follow.checked && atBottom) dom.log.scrollTop = dom.log.scrollHeight;
}

function applySummary(summary) {
	state.summary = summary;
	dom.badge.className = `badge ${summary.state}`;
	dom.badge.textContent =
		summary.state === 'running' && summary.active_scene
			? `running · ${summary.active_scene}`
			: summary.state + (summary.duration_s ? ` · ${summary.duration_s}s` : '');

	renderStages(summary);
	renderRibbon(summary);
	renderWarnings(summary);
	renderDownloads(summary);
}

// --- stages ---------------------------------------------------------------

const FALLBACK_STAGES = [
	{key: 'segment', label: 'Segment', detail: 'deterministic, no LLM'},
	{key: 'annotate', label: 'Annotate', detail: "the LLM's only job"},
	{key: 'evaluate', label: 'Evaluate', detail: 'Python computes every value'},
	{key: 'assets', label: 'Assets', detail: 'keyword → vendored icon'},
	{key: 'audio', label: 'Speak + align', detail: 'TTS, then word timings'},
	{key: 'track', label: 'Audio track', detail: 'one frame-aligned PCM'},
	{key: 'render', label: 'Render', detail: 'silent scene MP4s'},
	{key: 'mux', label: 'Mux', detail: 'single ffmpeg pass'},
	{key: 'spec', label: 'Write IR', detail: 'scene_spec.json (R6)'},
];

function renderStages(summary) {
	const stages = (summary && summary.stages) || FALLBACK_STAGES;
	clear(dom.stages);
	for (const stage of stages) {
		dom.stages.append(
			el(
				'div',
				{class: 'stage', 'data-status': stage.status || 'pending'},
				el('div', {class: 'label'}, stage.label),
				el('div', {class: 'detail'}, stage.detail),
			),
		);
	}
}

// --- ribbon ---------------------------------------------------------------

async function loadSpec(runId) {
	try {
		state.spec = await api(`/api/runs/${runId}/spec`);
		renderRibbon(state.summary);
	} catch {
		// A failed run, or a run stopped before stage 8, has no spec. Not an error.
	}
}

function renderRibbon(summary) {
	clear(dom.ribbon);
	clear(dom.axis);

	// Prefer the spec: it is the only source with real timings. Fall back to the
	// scene list parsed from the log, which has ids and templates but no durations.
	const hasSpec = Boolean(state.spec && state.spec.scenes && state.spec.scenes.length);
	const scenes = hasSpec ? state.spec.scenes : (summary && summary.scenes) || [];

	if (scenes.length === 0) {
		dom.ribbon.append(
			el('div', {class: 'muted small', style: 'align-self:center'}, 'No scenes yet.'),
		);
		dom.timelineMeta.textContent = '';
		return;
	}

	const iconOf = new Map(
		((summary && summary.scenes) || []).map((s) => [s.scene_id, s.icon]),
	);
	const total = scenes.reduce((sum, s) => sum + (s.duration_ms || 0), 0);
	// A spec exists, but a --dry-run spec has every duration at 0: timings come from
	// the TTS, which a dry run never calls. So proportionality depends on the numbers
	// being there, not on the spec being there — the earlier version of this check
	// conflated the two and reported "timings from the IR" over a row of zeroes.
	const timed = total > 0;

	for (const scene of scenes) {
		const duration = scene.duration_ms || 0;
		// flex-grow proportional to duration; equal when durations are unknown.
		const grow = timed ? Math.max(duration / total, 0.02) : 1;
		const icon = hasSpec
			? scene.assets && scene.assets.length > 0
				? scene.assets[0].id
				: null
			: iconOf.get(scene.scene_id);
		const active = summary && summary.active_scene === scene.scene_id;
		const block = el(
			'div',
			{
				class: `scene${state.selectedScene === scene.scene_id ? ' sel' : ''}`,
				style: `flex-grow:${grow}; ${active ? 'border-color:var(--accent-2);' : ''}`,
				title: timed
					? `${scene.scene_id} · ${scene.template_name} · ${fmtMs(duration)}`
					: `${scene.scene_id} · ${scene.template_name}`,
				onclick: () => selectScene(scene.scene_id),
			},
			el('div', {class: 'id'}, scene.scene_id.replace('scene_', '#')),
			el('div', {class: 'tpl'}, (icon ? '● ' : '') + scene.template_name),
		);
		dom.ribbon.append(block);
	}

	if (timed) {
		dom.axis.append(el('span', {}, '0:00'), el('span', {}, fmtMs(total)));
		dom.timelineMeta.textContent = `${scenes.length} scenes · ${fmtMs(total)} · timings from the IR`;
	} else {
		dom.timelineMeta.textContent = hasSpec
			? `${scenes.length} scenes · equal widths: a dry run writes no timings, because durations come from the TTS`
			: `${scenes.length} scenes · durations unknown until the IR is written`;
	}

	if (state.selectedScene) selectScene(state.selectedScene, {keep: true});
}

function selectScene(sceneId, {keep} = {}) {
	state.selectedScene = sceneId;
	if (!keep) {
		for (const node of dom.ribbon.querySelectorAll('.scene')) node.classList.remove('sel');
	}
	clear(dom.sceneDetail);

	const scene =
		state.spec && state.spec.scenes
			? state.spec.scenes.find((s) => s.scene_id === sceneId)
			: null;
	if (!scene) {
		dom.sceneDetail.append(
			el(
				'p',
				{class: 'muted small'},
				'Per-scene detail comes from the IR, which is written at the end of a run.',
			),
		);
		return;
	}

	const values = collectValues(scene.props);
	const table = el(
		'table',
		{class: 'scenes'},
		el(
			'tr',
			{},
			el('th', {}, 'On screen'),
			el('th', {}, 'From expression'),
			el('th', {}, 'Cue word'),
		),
		...(values.length
			? values.map((v) =>
					el(
						'tr',
						{},
						el(
							'td',
							{},
							el(
								'strong',
								{},
								(v.resolved === null || v.resolved === undefined
									? '—'
									: String(v.resolved)) + (v.unit ? ` ${v.unit}` : ''),
							),
							v.label ? el('div', {class: 'muted small'}, v.label) : null,
						),
						el('td', {class: 'num'}, v.expr || '— (literal text)'),
						el('td', {class: 'num'}, v.cue_word || '—'),
					),
				)
			: [el('tr', {}, el('td', {colspan: 3, class: 'muted'}, 'No values on this scene.'))]),
	);

	dom.sceneDetail.append(
		el(
			'div',
			{class: 'kv', style: 'margin-bottom:14px'},
			el('dt', {}, 'scene'),
			el('dd', {}, `${scene.scene_id} · ${scene.template_name}`),
			el('dt', {}, 'starts'),
			el('dd', {}, `${fmtMs(scene.start_ms)} · runs ${fmtMs(scene.duration_ms)}`),
			el('dt', {}, 'icon'),
			el(
				'dd',
				{},
				scene.assets && scene.assets.length
					? `${scene.assets[0].id} (cued on "${scene.assets[0].cue_word}")`
					: 'none',
			),
			el('dt', {}, 'triggers'),
			el('dd', {}, `${(scene.word_triggers || []).length} words aligned`),
		),
		el('p', {class: 'note'}, scene.narration_text),
		table,
	);
}

/**
 * Everything a template puts on screen, wherever it happens to keep it.
 *
 * Two shapes appear in `props`: computed Values (`{resolved, expr, cue_word, …}`) and
 * plain strings a template renders literally (`title`, `subtitle`). Both are on
 * screen, so both belong in the table — showing only the Values would make TitleCard
 * report "no values" while displaying two lines of text, which reads as a bug in the
 * scene rather than in this function.
 */
function collectValues(props) {
	const out = [];
	const walk = (node, key) => {
		if (Array.isArray(node)) {
			node.forEach((child) => walk(child, key));
			return;
		}
		if (typeof node === 'string') {
			out.push({resolved: node, label: key, expr: null, cue_word: null});
			return;
		}
		if (!node || typeof node !== 'object') return;
		if ('resolved' in node || 'expr' in node) {
			out.push(node);
			return;
		}
		for (const [childKey, child] of Object.entries(node)) walk(child, childKey);
	};
	walk(props, null);
	return out;
}

// --- warnings, downloads, history ----------------------------------------

function renderWarnings(summary) {
	clear(dom.warnings);
	for (const warning of summary.warnings || []) {
		dom.warnings.append(el('div', {class: 'warnbox'}, warning));
	}
	if (summary.error) {
		dom.warnings.append(el('div', {class: 'errbox'}, summary.error));
	}
}

function renderDownloads(summary) {
	clear(dom.downloads);
	const artifacts = summary.artifacts || [];
	if (artifacts.length === 0) {
		dom.outputHint.textContent =
			summary.state === 'running' ? 'Artifacts appear as they are written.' : 'Nothing yet.';
		return;
	}
	dom.outputHint.textContent =
		'Each button serves the file from this run’s directory under .cache/runs/.';
	for (const artifact of artifacts) {
		dom.downloads.append(
			el(
				'a',
				{href: `/api/runs/${summary.run_id}/download/${artifact.kind}`, download: ''},
				el('div', {class: 'n'}, labelFor(artifact.kind)),
				el('div', {class: 'd'}, artifact.description),
				el('div', {class: 'sz'}, `${artifact.name} · ${fmtBytes(artifact.bytes)}`),
			),
		);
	}

	// Inline preview, only once a video exists.
	clear(dom.player);
	if (artifacts.some((a) => a.kind === 'video')) {
		dom.player.append(
			el('video', {controls: true, src: `/api/runs/${summary.run_id}/video`}),
		);
	}
}

function labelFor(kind) {
	return (
		{
			video: '⬇ Video (MP4)',
			spec: '⬇ Scene spec (JSON)',
			config: '⬇ Config used',
			log: '⬇ Engine log',
		}[kind] || `⬇ ${kind}`
	);
}

async function refreshHistory() {
	try {
		const {runs} = await api('/api/runs');
		clear(dom.history);
		if (!runs || runs.length === 0) {
			dom.history.append(el('p', {class: 'muted small'}, 'No runs yet this session.'));
			return;
		}
		for (const run of runs) {
			dom.history.append(
				el(
					'a',
					{href: '#', onclick: (event) => {
						event.preventDefault();
						resetView();
						attach(run.run_id);
					}},
					el('div', {class: 'n'}, `${run.run_id} · ${run.script_name}`),
					el(
						'div',
						{class: 'd'},
						`${run.state}${run.duration_s ? ` in ${run.duration_s}s` : ''}` +
							`${run.scene_count ? ` · ${run.scene_count} scenes` : ''}`,
					),
					el('div', {class: 'sz'}, `${(run.artifacts || []).length} artifacts`),
				),
			);
		}
	} catch {
		/* non-fatal */
	}
}

boot();
