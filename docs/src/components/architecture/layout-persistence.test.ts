import assert from 'node:assert/strict';
import test from 'node:test';

import type { ArchitectureDocument } from './diagram-schema.ts';
import {
	ARCHITECTURE_LAYOUT_STORAGE_PREFIX,
	createArchitectureLayoutSignature,
	getArchitectureLayoutStorageKey,
	restoreArchitectureLayout,
	serializeArchitectureLayout,
} from './layout-persistence.ts';

const document: ArchitectureDocument = {
	id: 'persistence',
	title: 'Persistence',
	summary: 'Exercises browser-local node positions.',
	scope: 'service',
	nodes: [
		{
			id: 'worker',
			label: 'Worker',
			description: 'Claims work.',
			type: 'worker',
			position: { column: 1, row: 0 },
			mobileOrder: 2,
		},
		{
			id: 'jobs',
			label: 'Index Jobs',
			description: 'Stores durable jobs.',
			type: 'database',
			position: { column: 0, row: 0 },
			mobileOrder: 1,
		},
	],
	edges: [
		{ id: 'claim', source: 'jobs', target: 'worker' },
	],
};

const positions = {
	jobs: { x: 18.5, y: -42 },
	worker: { x: 312, y: 64.25 },
};

test('builds the versioned local-storage key', () => {
	assert.equal(
		getArchitectureLayoutStorageKey(document.id),
		`${ARCHITECTURE_LAYOUT_STORAGE_PREFIX}persistence`,
	);
});

test('serializes and restores valid positions for the exact node set', () => {
	const serialized = serializeArchitectureLayout(document, positions);
	assert.deepEqual(restoreArchitectureLayout(document, serialized), positions);

	const parsed = JSON.parse(serialized);
	assert.deepEqual(Object.keys(parsed), ['version', 'signature', 'positions']);
	assert.deepEqual(Object.keys(parsed.positions), ['jobs', 'worker']);
});

test('builds the same signature regardless of document node order', () => {
	const reordered: ArchitectureDocument = {
		...document,
		nodes: [...document.nodes].reverse(),
	};
	assert.equal(
		createArchitectureLayoutSignature(document),
		createArchitectureLayoutSignature(reordered),
	);
});

test('rejects a saved layout when authored grid positions change', () => {
	const serialized = serializeArchitectureLayout(document, positions);
	const changed: ArchitectureDocument = {
		...document,
		nodes: document.nodes.map((node) => node.id === 'worker'
			? { ...node, position: { column: 2, row: 0 } }
			: node),
	};

	assert.equal(restoreArchitectureLayout(changed, serialized), null);
});

test('rejects malformed JSON and non-finite coordinates', () => {
	assert.equal(restoreArchitectureLayout(document, '{invalid'), null);
	assert.equal(
		restoreArchitectureLayout(document, JSON.stringify({
			version: 1,
			signature: createArchitectureLayoutSignature(document),
			positions: {
				jobs: { x: 0, y: 0 },
				worker: { x: 1e400, y: 0 },
			},
		})),
		null,
	);
});

test('rejects missing and extra node positions', () => {
	const signature = createArchitectureLayoutSignature(document);
	assert.equal(
		restoreArchitectureLayout(document, JSON.stringify({
			version: 1,
			signature,
			positions: { jobs: positions.jobs },
		})),
		null,
	);
	assert.equal(
		restoreArchitectureLayout(document, JSON.stringify({
			version: 1,
			signature,
			positions: { ...positions, obsolete: { x: 0, y: 0 } },
		})),
		null,
	);
});

test('refuses to serialize incomplete or extra position sets', () => {
	assert.throws(
		() => serializeArchitectureLayout(document, { jobs: positions.jobs }),
		/exact document node set/,
	);
	assert.throws(
		() => serializeArchitectureLayout(document, {
			...positions,
			obsolete: { x: 0, y: 0 },
		}),
		/exact document node set/,
	);
});

test('boundaries never enter or invalidate persisted component positions', () => {
	const grouped: ArchitectureDocument = {
		...document,
		boundaries: [{
			id: 'worker-boundary',
			label: 'Worker',
			kind: 'worker',
			nodeIds: ['worker'],
		}],
	};
	const serialized = serializeArchitectureLayout(grouped, positions);
	assert.deepEqual(restoreArchitectureLayout(grouped, serialized), positions);
	assert.equal(Object.hasOwn(JSON.parse(serialized).positions, 'worker-boundary'), false);
});
