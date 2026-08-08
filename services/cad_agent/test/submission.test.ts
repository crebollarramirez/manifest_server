import assert from 'node:assert/strict';
import test from 'node:test';
import { SubmissionService } from '../src/submission.service';
import { WorkflowError } from '../src/contracts';
import { CadAgentRepository } from '../src/cad-agent.repository';

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

test('a linked CAD part is validated and queued without inspecting its source', async () => {
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
    readText: async () => assert.fail('Nest must not inspect CAD source during submission.'),
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
  assert.equal(submitted[0].workflowMode, 'edit');
  assert.equal(submitted[0].requestedPartId, PART_ID);
  assert.deepEqual(submitted[0].resolvedTargets, []);
});

test('a linked mesh part cannot be queued as a CAD edit', async () => {
  const repository = {
    editJobForClientRequest: async () => null,
    project: async () => ({ id: PROJECT_ID }),
    part: async () => ({
      id: PART_ID,
      project_id: PROJECT_ID,
      part_name: 'Dragon',
      part_type: 'mesh',
    }),
  };
  const service = new SubmissionService(repository as never);

  await assert.rejects(
    service.submit({
      project_id: PROJECT_ID,
      part_id: PART_ID,
      request_text: 'Make it wider',
    }),
    (error: unknown) =>
      error instanceof WorkflowError && error.code === 'INVALID_PART_TYPE',
  );
});

test('part reservation conflicts retain their stable public error code', async () => {
  const repository = Object.create(CadAgentRepository.prototype) as CadAgentRepository;
  Object.assign(repository, {
    client: {
      rpc: async () => ({
        data: null,
        error: {
          message: 'PART_EDIT_IN_PROGRESS: another edit reserves this part.',
        },
      }),
    },
  });

  await assert.rejects(
    repository.submitEditJob({
      projectId: PROJECT_ID,
      requestText: 'Make it taller',
      messages: [{ role: 'user', content: 'Make it taller' }],
      requestedPartId: PART_ID,
      workflowMode: 'edit',
      clientRequestId: CLIENT_ID,
      requestFingerprint: 'a'.repeat(64),
      resolvedTargets: [],
    }),
    (error: unknown) =>
      error instanceof WorkflowError && error.code === 'PART_EDIT_IN_PROGRESS',
  );
});
