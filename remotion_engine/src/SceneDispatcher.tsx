/**
 * Maps template_name -> component.
 *
 * Two rules, both aimed at R2 (an unseen script must not crash the render):
 *   1. An unknown template_name mounts Fallback. It never throws.
 *   2. A template that throws at runtime is caught by an error boundary and
 *      replaced with Fallback, so one bad scene cannot kill the whole video.
 */

import React from 'react';

import {BigNumber} from './templates/BigNumber';
import {ExpressionCard} from './templates/ExpressionCard';
import {Fallback} from './templates/Fallback';
import {KeyValuePanel} from './templates/KeyValuePanel';
import {ProcessSteps} from './templates/ProcessSteps';
import {TitleCard} from './templates/TitleCard';
import {DEFAULT_THEME} from './theme';
import type {SceneProps, TemplateName} from './types';

/**
 * Keep this in step with IMPLEMENTED_TEMPLATES in python_pipeline/annotate.py.
 * The annotator is told which templates exist, and an out-of-set choice is
 * downgraded to Fallback there — so a mismatch shows up as a Python-side warning
 * rather than as a silently generic scene.
 *
 * ComparisonGrid is intentionally absent: the flat annotation contract has no
 * notion of columns, so a grid would have to invent structure the annotator
 * never described.
 */
const REGISTRY: Partial<Record<TemplateName, React.FC<SceneProps>>> = {
	TitleCard,
	KeyValuePanel,
	ExpressionCard,
	BigNumber,
	ProcessSteps,
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
