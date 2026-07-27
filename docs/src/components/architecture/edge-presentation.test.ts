import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
	getArchitectureDirectionMarker,
	getArchitectureEdgeRenderContract,
} from './edge-presentation.ts';

test('preserves protocol line styles and focus emphasis in the render contract', () => {
	const asynchronous = getArchitectureEdgeRenderContract(
		{
			id: 'queue',
			source: 'jobs',
			target: 'worker',
			protocol: 'queue',
			flow: 'asynchronous',
		},
		{ emphasized: true },
	);
	const data = getArchitectureEdgeRenderContract({
		id: 'storage-write',
		source: 'worker',
		target: 'storage',
		protocol: 'storage',
	});

	assert.equal(asynchronous.kind, 'asynchronous');
	assert.match(asynchronous.baseClass, /architecture-edge--asynchronous/);
	assert.match(asynchronous.baseClass, /architecture-edge--emphasized/);
	assert.equal(data.kind, 'data');
	assert.match(data.baseClass, /architecture-edge--data/);
});

test('one-way edges have one forward stream and only a target arrowhead', () => {
	const contract = getArchitectureEdgeRenderContract({
		id: 'one-way',
		source: 'source',
		target: 'target',
		direction: 'one-way',
	});

	assert.equal(contract.hasSourceArrow, false);
	assert.equal(contract.hasTargetArrow, true);
	assert.match(contract.forwardMotionClass, /motion--forward/);
	assert.equal(contract.reverseMotionClass, null);
});

test('two-way exchanges keep one request arrow and one request-direction motion stream', () => {
	const contract = getArchitectureEdgeRenderContract(
		{
			id: 'two-way',
			source: 'source',
			target: 'target',
			direction: 'two-way',
		},
		{ dimmed: true },
	);

	assert.equal(contract.hasSourceArrow, false);
	assert.equal(contract.hasTargetArrow, true);
	assert.match(contract.forwardMotionClass, /motion--forward/);
	assert.equal(contract.reverseMotionClass, null);
	assert.match(contract.baseClass, /architecture-edge--dimmed/);
});

test('live endpoint direction selects horizontal and vertical detail markers', () => {
	assert.equal(
		getArchitectureDirectionMarker({
			sourceX: 0,
			sourceY: 0,
			targetX: -100,
			targetY: 0,
			direction: 'one-way',
		}),
		'←',
	);
	assert.equal(
		getArchitectureDirectionMarker({
			sourceX: 0,
			sourceY: 0,
			targetX: 0,
			targetY: 100,
			direction: 'two-way',
		}),
		'↓',
	);
});

test('reduced-motion CSS removes particle streams without hiding base edges', () => {
	const stylesheet = readFileSync(
		new URL('../../styles/architecture.css', import.meta.url),
		'utf8',
	);
	const reducedMotion = stylesheet.slice(
		stylesheet.lastIndexOf('@media (prefers-reduced-motion: reduce)'),
	);

	assert.match(reducedMotion, /\.architecture-edge__motion/);
	assert.match(reducedMotion, /display:\s*none/);
	assert.doesNotMatch(reducedMotion, /\.architecture-edge\s*\{/);
});
