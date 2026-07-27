import assert from 'node:assert/strict';
import test from 'node:test';

import type { ArchitectureNode } from './diagram-schema.ts';
import {
	getVisibleNodeInspectorSections,
	isNodeActivationKey,
	nodeResponsibility,
} from './node-details.ts';

const node: ArchitectureNode = {
	id: 'proof',
	label: 'Source Proof',
	description: 'Checks the source.',
	type: 'function',
	variant: 'gate',
	position: { column: 0, row: 0 },
	mobileOrder: 1,
	details: {
		responsibility: 'Prove source identity before execution.',
		trigger: ['A job is claimed.'],
		inputs: [{ name: 'model.py', description: 'Downloaded source.' }],
		steps: ['Hash the source.'],
		evidence: [{
			path: 'workers/cad_validator/validate_cad_job.py',
			symbol: 'validate_cad_job',
			kind: 'source',
		}],
	},
};

test('opens When it runs and Contract while omitting empty inspector sections', () => {
	assert.deepEqual(getVisibleNodeInspectorSections(node), [
		{ id: 'when', title: 'When it runs', defaultOpen: true },
		{ id: 'contract', title: 'Contract', defaultOpen: true },
		{ id: 'logic', title: 'Logic', defaultOpen: false },
		{ id: 'source', title: 'Source', defaultOpen: false },
	]);
});

test('falls back to the concise node description when no detailed responsibility exists', () => {
	assert.equal(nodeResponsibility(node), 'Prove source identity before execution.');
	assert.equal(nodeResponsibility({ ...node, details: undefined }), 'Checks the source.');
});

test('node keyboard activation accepts Enter and Space only', () => {
	assert.equal(isNodeActivationKey('Enter'), true);
	assert.equal(isNodeActivationKey(' '), true);
	assert.equal(isNodeActivationKey('Escape'), false);
	assert.equal(isNodeActivationKey('ArrowRight'), false);
});
