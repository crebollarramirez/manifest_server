import assert from 'node:assert/strict';
import test from 'node:test';
import { Position } from '@xyflow/react';

import type { ArchitectureDocument } from './diagram-schema.ts';
import {
	assignInteractiveEdgeRoutes,
	buildInitialNodePositions,
	chooseNearestHandles,
	getDirectionMarker,
	type InteractiveNodeGeometry,
} from './interactive-routing.ts';

const document: ArchitectureDocument = {
	id: 'interactive-routing',
	title: 'Interactive routing',
	summary: 'Exercises live relationship routing.',
	scope: 'service',
	nodes: [
		{
			id: 'source',
			label: 'Source',
			description: 'Sends work.',
			type: 'queue',
			position: { column: 0, row: 0 },
			mobileOrder: 1,
		},
		{
			id: 'target',
			label: 'Target',
			description: 'Receives work.',
			type: 'worker',
			position: { column: 2, row: 1 },
			mobileOrder: 2,
		},
	],
	edges: [
		{
			id: 'source-target',
			source: 'source',
			target: 'target',
			direction: 'one-way',
		},
	],
};

const source: InteractiveNodeGeometry = {
	id: 'source',
	position: { x: 0, y: 0 },
};

test('derives initial positions from the authored grid and shared dimensions', () => {
	assert.deepEqual(buildInitialNodePositions(document), {
		source: { x: 54, y: 54 },
		target: { x: 554, y: 244 },
	});
});

test('changes nearest cardinal handles when a node moves around its peer', () => {
	const right = chooseNearestHandles(source, {
		id: 'target',
		position: { x: 300, y: 0 },
	});
	assert.equal(right.sourcePosition, Position.Right);
	assert.equal(right.targetPosition, Position.Left);
	assert.equal(right.sourceHandleId, 'source-right');
	assert.equal(right.targetHandleId, 'target-left');

	const above = chooseNearestHandles(source, {
		id: 'target',
		position: { x: 0, y: -300 },
	});
	assert.equal(above.sourcePosition, Position.Top);
	assert.equal(above.targetPosition, Position.Bottom);
	assert.equal(above.sourceHandleId, 'source-top');
	assert.equal(above.targetHandleId, 'target-bottom');
});

test('returns request-direction markers for one-way and request/response exchanges', () => {
	const center = { x: 100, y: 100 };
	assert.equal(getDirectionMarker(center, { x: 300, y: 100 }), '→');
	assert.equal(getDirectionMarker(center, { x: -100, y: 100 }), '←');
	assert.equal(getDirectionMarker(center, { x: 100, y: -100 }), '↑');
	assert.equal(getDirectionMarker(center, { x: 100, y: 300 }), '↓');
	assert.equal(getDirectionMarker(center, { x: 300, y: 100 }, 'two-way'), '→');
	assert.equal(getDirectionMarker(center, { x: 100, y: 300 }, 'two-way'), '↓');
});

test('puts reverse and secondary relationships on alternating exterior routes', () => {
	const nodes: InteractiveNodeGeometry[] = [
		source,
		{ id: 'target', position: { x: 300, y: 0 } },
	];
	const routes = assignInteractiveEdgeRoutes([
		{ id: 'forward', source: 'source', target: 'target' },
		{ id: 'reverse', source: 'target', target: 'source' },
		{ id: 'secondary', source: 'source', target: 'target' },
	], nodes);

	assert.deepEqual(
		{
			slot: routes.get('forward')?.routeSlot,
			source: routes.get('forward')?.sourcePosition,
			target: routes.get('forward')?.targetPosition,
		},
		{ slot: 0, source: Position.Right, target: Position.Left },
	);
	assert.deepEqual(
		{
			slot: routes.get('reverse')?.routeSlot,
			source: routes.get('reverse')?.sourcePosition,
			target: routes.get('reverse')?.targetPosition,
		},
		{ slot: 1, source: Position.Bottom, target: Position.Bottom },
	);
	assert.deepEqual(
		{
			slot: routes.get('secondary')?.routeSlot,
			source: routes.get('secondary')?.sourcePosition,
			target: routes.get('secondary')?.targetPosition,
		},
		{ slot: -1, source: Position.Top, target: Position.Top },
	);
});

test('updates direct routing when live node positions change', () => {
	const horizontal = assignInteractiveEdgeRoutes(document.edges, [
		source,
		{ id: 'target', position: { x: 300, y: 0 } },
	]).get('source-target');
	const vertical = assignInteractiveEdgeRoutes(document.edges, [
		source,
		{ id: 'target', position: { x: 0, y: 300 } },
	]).get('source-target');

	assert.equal(horizontal?.sourceHandleId, 'source-right');
	assert.equal(horizontal?.marker, '→');
	assert.equal(vertical?.sourceHandleId, 'source-bottom');
	assert.equal(vertical?.marker, '↓');
});
