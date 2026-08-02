/**
 * Maps template_name -> component.
 *
 * Two rules, both aimed at R2 (an unseen script must not crash the render):
 *   1. An unknown template_name mounts Fallback. It never throws.
 *   2. A template that throws at runtime is caught by an error boundary and
 *      replaced with Fallback, so one bad scene cannot kill the whole video.
 */

import React from 'react';

import {ensureFontsLoaded} from './fonts';
import {BigNumber} from './templates/BigNumber';
import {ComparisonGrid} from './templates/ComparisonGrid';
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
 * ComparisonGrid is now included, but not as the table the original spec
 * described — the flat contract still has no notion of columns. It renders the
 * flat `items[]` as proportional bars, which needs no new annotation shape. See
 * its own docstring for why bars are the better reading of a comparison anyway.
 */
const REGISTRY: Partial<Record<TemplateName, React.FC<SceneProps>>> = {
	TitleCard,
	KeyValuePanel,
	ExpressionCard,
	BigNumber,
	ComparisonGrid,
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
	// Before the first paint, and outside an effect: an effect runs after React has
	// already committed, by which time Remotion may have captured frame 0 with
	// fallback typography. delayRender inside this call is what holds the capture.
	ensureFontsLoaded();

	// Defaults so a partially-specified --props payload still renders.
	const scene: SceneProps = {
		scene_id: raw.scene_id ?? 'scene_unknown',
		template_name: raw.template_name ?? 'Fallback',
		narration_text: raw.narration_text ?? '',
		props: raw.props ?? {},
		assets: raw.assets ?? [],
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
