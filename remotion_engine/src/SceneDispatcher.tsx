/**
 * Maps template_name -> component.
 *
 * Two rules, both aimed at R2 (an unseen script must not crash the render):
 *   1. An unknown template_name mounts Fallback. It never throws.
 *   2. A template that throws at runtime is caught by an error boundary and
 *      replaced with Fallback, so one bad scene cannot kill the whole video.
 */

import React from 'react';

import {ExpressionCard} from './templates/ExpressionCard';
import {Fallback} from './templates/Fallback';
import {DEFAULT_THEME} from './theme';
import type {SceneProps, TemplateName} from './types';

/**
 * Phase 0 ships ExpressionCard + Fallback. The remaining templates
 * (TitleCard, KeyValuePanel, BigNumber, ComparisonGrid, ProcessSteps) land in
 * Phase 3 and register here.
 */
const REGISTRY: Partial<Record<TemplateName, React.FC<SceneProps>>> = {
	ExpressionCard,
	Fallback,
};

class TemplateBoundary extends React.Component<
	{fallback: React.ReactNode; children: React.ReactNode},
	{failed: boolean}
> {
	constructor(props: {fallback: React.ReactNode; children: React.ReactNode}) {
		super(props);
		this.state = {failed: false};
	}

	static getDerivedStateFromError() {
		return {failed: true};
	}

	componentDidCatch(error: Error) {
		// Surfaces in the render log without aborting the render.
		// eslint-disable-next-line no-console
		console.error('[SceneDispatcher] template threw, using Fallback:', error.message);
	}

	render() {
		return this.state.failed ? this.props.fallback : this.props.children;
	}
}

export const SceneDispatcher: React.FC<Partial<SceneProps>> = (raw) => {
	// Defaults so a partially-specified --props payload still renders.
	const scene: SceneProps = {
		scene_id: raw.scene_id ?? 'scene_unknown',
		template_name: raw.template_name ?? 'Fallback',
		narration_text: raw.narration_text ?? '',
		props: raw.props ?? {},
		word_triggers: raw.word_triggers ?? [],
		theme: {...DEFAULT_THEME, ...(raw.theme ?? {})},
		orientation: raw.orientation ?? 'landscape',
		output_scale: raw.output_scale ?? 1,
	};

	const Template = REGISTRY[scene.template_name];

	if (!Template) {
		// eslint-disable-next-line no-console
		console.warn(
			`[SceneDispatcher] unknown template "${scene.template_name}", using Fallback`,
		);
		return <Fallback {...scene} />;
	}

	return (
		<TemplateBoundary fallback={<Fallback {...scene} />}>
			<Template {...scene} />
		</TemplateBoundary>
	);
};
