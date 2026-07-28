import assert from 'node:assert/strict';
import test from 'node:test';
import { SubmissionService } from '../src/submission.service';
import { WorkflowError } from '../src/contracts';

const PROJECT_ID = '22222222-2222-4222-8222-222222222222';
const PART_ID = '11111111-1111-4111-8111-111111111111';
const CLIENT_ID = '33333333-3333-4333-8333-333333333333';

function queuedJob(overrides: Record<string, unknown> = {}) {
  return {
    id: '44444444-4444-4444-8444-444444444444',
    project_id: PROJECT_ID,
    requested_part_id: null,
    resolved_part_id: null,
    request_text: 'Add a mounting hole',
    messages: [{ role: 'user', content: 'Add a mounting hole' }],
    workflow_mode: 'edit',
    status: 'queued',
    state: 'received',
    attempt_count: 0,
    max_attempts: 3,
    accepted_source_sha256: null,
    original_storage_path: null,
    current_candidate_path: null,
    current_candidate_sha256: null,
    validation_job_id: null,
    index_job_id: null,
    export_job_id: null,
    resolved_targets: [],
    history: [],
    result: null,
    error_code: null,
    error_message: null,
    client_request_id: CLIENT_ID,
    request_fingerprint: null,
    last_event_sequence: 1,
    created_at: new Date().toISOString(),
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

test('same client request returns the existing durable job', async () => {
  let submittedFingerprint = '';
  const repository = {
    editJobForClientRequest: async () => null,
    project: async () => ({ id: PROJECT_ID }),
    submitEditJob: async (input: Record<string, unknown>) => {
      submittedFingerprint = String(input.requestFingerprint);
      return queuedJob({ request_fingerprint: submittedFingerprint });
    },
  };
  const service = new SubmissionService(repository as never);
  const first = await service.submit({
    project_id: PROJECT_ID,
    request_text: 'Add a mounting hole',
    client_request_id: CLIENT_ID,
  });
  assert.equal(first.deduplicated, false);

  repository.editJobForClientRequest = async () =>
    queuedJob({ request_fingerprint: submittedFingerprint });
  const duplicate = await service.submit({
    project_id: PROJECT_ID,
    request_text: 'Add a mounting hole',
    client_request_id: CLIENT_ID,
  });
  assert.equal(duplicate.deduplicated, true);
  assert.equal(duplicate.job.id, first.job.id);
});

test('same client request ID with a different payload is rejected', async () => {
  const repository = {
    editJobForClientRequest: async () =>
      queuedJob({ request_fingerprint: 'a'.repeat(64) }),
  };
  const service = new SubmissionService(repository as never);
  await assert.rejects(
    service.submit({
      project_id: PROJECT_ID,
      request_text: 'A different request',
      client_request_id: CLIENT_ID,
    }),
    (error: unknown) =>
      error instanceof WorkflowError && error.code === 'CLIENT_REQUEST_ID_CONFLICT',
  );
});

test('a linked exact blank CAD part enters initial design while established source stays edit mode', async () => {
  let source = 'from cadquery_runtime import cad_part, cq, dataclass\n';
  const submitted: Array<Record<string, unknown>> = [];
  const repository = {
    editJobForClientRequest: async () => null,
    project: async () => ({ id: PROJECT_ID }),
    part: async () => ({
      id: PART_ID,
      project_id: PROJECT_ID,
      part_name: 'Bracket',
      part_type: 'cad',
    }),
    canonicalPath: () => 'model.py',
    readText: async () => source,
    submitEditJob: async (input: Record<string, unknown>) => {
      submitted.push(input);
      return queuedJob({
        requested_part_id: PART_ID,
        workflow_mode: input.workflowMode,
      });
    },
  };
  const service = new SubmissionService(repository as never);
  await service.submit({
    project_id: PROJECT_ID,
    part_id: PART_ID,
    request_text: 'Create a soap holder',
  });
  assert.equal(submitted[0].workflowMode, 'initial_design');
  assert.equal(submitted[0].requestedPartId, PART_ID);
  assert.equal((submitted[0].resolvedTargets as unknown[]).length, 1);

  source += '\n@dataclass(frozen=True)\nclass ModelParams:\n    width: float = 2\n';
  await service.submit({
    project_id: PROJECT_ID,
    part_id: PART_ID,
    request_text: 'Make it wider',
  });
  assert.equal(submitted[1].workflowMode, 'edit');
  assert.deepEqual(submitted[1].resolvedTargets, []);
});
