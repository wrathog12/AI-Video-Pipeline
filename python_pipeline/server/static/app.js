/*
 * Shared helpers for both pages.
 *
 * No framework and no build step, which is a deliberate trade: the dashboard is two
 * pages and a progress stream, and a bundler would add a second dependency tree plus
 * a `dist/` that must be rebuilt before the tool works — a new way for the repo to be
 * broken on a clean clone, in service of a UI this small.
 *
 * The cost is that DOM building is manual. `el()` keeps that honest, and every
 * insertion of engine-provided text goes through textContent rather than innerHTML:
 * a script name, a log line or a template name is untrusted as far as this page is
 * concerned, and a narration script containing markup should render as characters.
 */

/** Element factory: el('div', {class: 'x'}, 'text', childNode, ...). */
function el(tag, attrs, ...children) {
	const node = document.createElement(tag);
	for (const [key, value] of Object.entries(attrs || {})) {
		if (value === null || value === undefined || value === false) continue;
		if (key === 'class') node.className = value;
		else if (key === 'text') node.textContent = value;
		else if (key.startsWith('on') && typeof value === 'function') {
			node.addEventListener(key.slice(2), value);
		} else node.setAttribute(key, value === true ? '' : String(value));
	}
	for (const child of children.flat()) {
		if (child === null || child === undefined || child === false) continue;
		node.append(child instanceof Node ? child : document.createTextNode(String(child)));
	}
	return node;
}

function clear(node) {
	while (node.firstChild) node.removeChild(node.firstChild);
	return node;
}

async function api(path, options) {
	const response = await fetch(path, {
		headers: {'Content-Type': 'application/json'},
		...options,
	});
	const text = await response.text();
	let body = null;
	try {
		body = text ? JSON.parse(text) : null;
	} catch {
		body = {detail: text};
	}
	if (!response.ok) {
		// FastAPI validation errors arrive as a list; flatten so the page can show
		// every problem at once rather than only the first.
		const detail = body && body.detail;
		const message = Array.isArray(detail)
			? detail.map((d) => (typeof d === 'string' ? d : d.msg || JSON.stringify(d))).join('; ')
			: detail || response.statusText;
		throw new Error(message);
	}
	return body;
}

/** 8200 -> "8.2s"; 138267 -> "2:18". */
function fmtMs(ms) {
	if (ms === null || ms === undefined) return '—';
	const total = ms / 1000;
	if (total < 60) return `${total.toFixed(1)}s`;
	const minutes = Math.floor(total / 60);
	const seconds = Math.round(total % 60);
	return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function fmtBytes(n) {
	if (!n) return '0 B';
	if (n < 1024) return `${n} B`;
	if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
	return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

/**
 * Settings the timeline page will send with a run.
 *
 * Held in localStorage rather than on the server so the settings page and the
 * timeline page can be open at once without one clobbering the other's in-flight
 * edits, and so a dashboard restart does not silently revert a pending change. The
 * server still validates every field — this is a convenience, not a trust boundary.
 */
const PENDING_KEY = 'aivideo.pendingOverrides';

function loadPending() {
	try {
		return JSON.parse(localStorage.getItem(PENDING_KEY) || '{}');
	} catch {
		return {};
	}
}

function savePending(overrides) {
	if (!overrides || Object.keys(overrides).length === 0) {
		localStorage.removeItem(PENDING_KEY);
	} else {
		localStorage.setItem(PENDING_KEY, JSON.stringify(overrides));
	}
}
