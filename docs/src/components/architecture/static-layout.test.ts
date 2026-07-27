import assert from 'node:assert/strict';
import test from 'node:test';

import { buildStaticEdges } from './static-layout.ts';
import type { ArchitectureDocument } from './diagram-schema.ts';

const document: ArchitectureDocument = {
	id: 'static-layout',
	title: 'Static layout',
	summary: 'Exercises the page-native connector renderer.',
	scope: 'service',
	nodes: [
		{ id: 'source', label: 'Source', description: 'Sends work.', type: 'api', position: { column: 0, row: 0 }, mobileOrder: 1 },
		{ id: 'target', label: 'Target', description: 'Receives work.', type: 'worker', position: { column: 1, row: 0 }, mobileOrder: 3 },
	],
	edges: [
		{ id: 'source-target', source: 'source', target: 'target', label: 'validated job', protocol: 'queue', direction: 'one-way', flow: 'asynchronous' },
	],
};

const nodeRects = new Map([
	['source', { left: 0, top: 0, width: 140, height: 120 }],
	['target', { left: 280, top: 0, width: 140, height: 120 }],
]);

test('returns one continuous routed path with its relationship contract', () => {
	const [edge] = buildStaticEdges(document, nodeRects, { left: 0, top: 0, width: 420, height: 220 }, false);
	assert.equal(edge.path, 'M 140.00 60.00 L 280.00 60.00');
	assert.equal(edge.edge.label, 'validated job');
	assert.equal(edge.kind, 'asynchronous');
});

test('does not alter path geometry for long or important labels', () => {
	const longLabelDocument: ArchitectureDocument = {
		...document,
		edges: [{ ...document.edges[0], label: 'build_index project job', importance: 'important' }],
	};
	const [edge] = buildStaticEdges(longLabelDocument, nodeRects, { left: 0, top: 0, width: 420, height: 220 }, false);
	assert.equal(edge.path, 'M 140.00 60.00 L 280.00 60.00');
	assert.equal(edge.edge.importance, 'important');
});

test('routes a vertical relationship directly between card edges', () => {
	const verticalNodes = new Map([
		['source', { left: 0, top: 0, width: 140, height: 120 }],
		['target', { left: 0, top: 220, width: 140, height: 120 }],
	]);
	const [edge] = buildStaticEdges(document, verticalNodes, { left: 0, top: 0, width: 220, height: 340 }, false);
	assert.equal(edge.path, 'M 70.00 120.00 L 70.00 220.00');
});

test('routes a same-row return above cards', () => {
	const reverseNodes = new Map([
		['source', { left: 280, top: 50, width: 140, height: 120 }],
		['target', { left: 0, top: 50, width: 140, height: 120 }],
	]);
	const [edge] = buildStaticEdges(document, reverseNodes, { left: 0, top: 0, width: 420, height: 270 }, false);
	assert.match(edge.path, /L 350\.00 32\.00/);
});

test('routes parallel request edges through separate desktop lanes', () => {
	const parallelDocument: ArchitectureDocument = {
		...document,
		edges: [
			{ ...document.edges[0], id: 'create-project', label: 'project request' },
			{ ...document.edges[0], id: 'index-project', label: 'index request' },
		],
	};
	const edges = buildStaticEdges(parallelDocument, nodeRects, { left: 0, top: 0, width: 420, height: 220 }, false);
	assert.equal(edges.length, 2);
	assert.notEqual(edges[0].path, edges[1].path);
});

test('routes non-adjacent lower targets through a reserved orthogonal lane', () => {
	const lowerNodes = new Map([
		['source', { left: 0, top: 0, width: 140, height: 120 }],
		['target', { left: 280, top: 220, width: 140, height: 120 }],
	]);
	const [edge] = buildStaticEdges(document, lowerNodes, { left: 0, top: 0, width: 420, height: 340 }, false);
	assert.match(edge.path, /L 70\.00 148\.00/);
});

test('reroutes static cross-links through a mobile exterior lane', () => {
	const mobileNodes = new Map([
		['source', { left: 48, top: 0, width: 220, height: 120 }],
		['target', { left: 48, top: 220, width: 220, height: 120 }],
	]);
	const [edge] = buildStaticEdges(document, mobileNodes, { left: 0, top: 0, width: 320, height: 340 }, true);
	assert.match(edge.path, /L 310\.00 60\.00/);
	assert.match(edge.path, /L 158\.00 220\.00$/);
});
