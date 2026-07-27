import assert from 'node:assert/strict';
import test from 'node:test';

import { getConnectionDetails } from './connection-details.ts';
import type { ArchitectureEdge } from './diagram-schema.ts';

test('formats authored connection metadata for the shared side panel', () => {
	const edge: ArchitectureEdge = {
		id: 'claim-job',
		source: 'jobs',
		target: 'worker',
		label: 'Claim queued index job',
		protocol: 'database',
		flow: 'asynchronous',
		direction: 'two-way',
		request: {
			label: 'claim index job',
			type: 'claim_next_index_job RPC',
			exampleBody: '{"worker_id":"indexer-1"}',
		},
		response: {
			label: 'leased index job',
			type: 'index_jobs row',
			exampleBody: '{"id":"index-job-123"}',
		},
		details: {
			summary: 'The worker atomically claims one queued row.',
			preconditions: ['A queued row exists.'],
			operation: 'claim_next_index_job',
			durability: 'The row is marked running before return.',
			failureBehavior: 'No row produces no handoff.',
			evidence: [{
				path: 'supabase/migrations/claim_index_job.sql',
				kind: 'migration',
			}],
		},
	};

	assert.deepEqual(getConnectionDetails(edge), {
		title: 'Claim queued index job',
		summary: 'The worker atomically claims one queued row.',
		preconditions: ['A queued row exists.'],
		operation: 'claim_next_index_job',
		payload: undefined,
		durability: 'The row is marked running before return.',
		failureBehavior: 'No row produces no handoff.',
		trustBoundary: undefined,
		evidence: [{
			path: 'supabase/migrations/claim_index_job.sql',
			kind: 'migration',
		}],
		protocol: 'Database',
		flow: 'Asynchronous',
		direction: 'Two-way',
	});
});

test('provides readable defaults when optional metadata is omitted', () => {
	const edge: ArchitectureEdge = {
		id: 'internal-step',
		source: 'one',
		target: 'two',
	};

	assert.deepEqual(getConnectionDetails(edge), {
		title: 'Internal connection',
		summary: undefined,
		preconditions: [],
		operation: undefined,
		payload: undefined,
		durability: undefined,
		failureBehavior: undefined,
		trustBoundary: undefined,
		evidence: [],
		protocol: 'Internal',
		flow: 'Synchronous',
		direction: 'One-way',
	});
});
