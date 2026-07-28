import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { ToolPlanSchema, WorkflowError, type EditJob } from '../src/contracts';
import {
  OrchestratorService,
  planImpactDebug,
  planOperationDebug,
  validationDiagnosticCodes,
} from '../src/orchestrator.service';

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const PART_ID = '22222222-2222-4222-8222-222222222222';
const JOB_ID = '33333333-3333-4333-8333-333333333333';
const CANDIDATE_PATH = `${PROJECT_ID}/candidates/cad/${PART_ID}/${JOB_ID}/attempt-1/model.py`;
const CANONICAL_PATH = `${PROJECT_ID}/parts/cad/${PART_ID}/model.py`;
const sha256 = (value: string) => createHash('sha256').update(value).digest('hex');

function editJob(overrides: Partial<EditJob> = {}): EditJob {
  return {
    id: JOB_ID,
    project_id: PROJECT_ID,
    requested_part_id: PART_ID,
    resolved_part_id: PART_ID,
    request_text: 'Add a mounting hole',
    messages: [{ role: 'user', content: 'Add a mounting hole' }],
    workflow_mode: 'edit',
    status: 'running',
    state: 'validating_candidate',
    attempt_count: 1,
    max_attempts: 3,
    accepted_source_sha256: sha256('accepted'),
    original_storage_path: `${PROJECT_ID}/original.py`,
    current_candidate_path: CANDIDATE_PATH,
    current_candidate_sha256: sha256('candidate'),
    validation_job_id: '44444444-4444-4444-8444-444444444444',
    index_job_id: null,
    export_job_id: null,
    resolved_targets: [],
    history: [],
    result: null,
    error_code: null,
    error_message: null,
    client_request_id: null,
    request_fingerprint: null,
    last_event_sequence: 1,
    created_at: new Date().toISOString(),
    started_at: new Date().toISOString(),
    completed_at: null,
    ...overrides,
  };
}

function plan(baseHash: string) {
  return ToolPlanSchema.parse({
    schema_version: 1,
    summary: 'Added a mounting hole.',
    target_part_id: PART_ID,
    base_source_sha256: baseHash,
    operations: [
      {
        tool: 'replace_build_model_body',
        target_id: `${PART_ID}:build_model_body:build_model`,
        target_fingerprint: 'a'.repeat(64),
        replacement_source: 'return make_body(params)',
      },
    ],
  });
}

function harness(job: EditJob, canonical = 'accepted') {
  const files = new Map([
    [CANONICAL_PATH, canonical],
    [CANDIDATE_PATH, 'candidate'],
  ]);
  const calls = { writes: 0, queueIndex: 0, queueExport: 0 };
  const repository = {
    canonicalPath: () => CANONICAL_PATH,
    patchEditJob: async (_id: string, values: Record<string, unknown>) =>
      ({ ...job, ...values }),
    heartbeatEditJob: async () => undefined,
    readText: async (path: string) => {
      const value = files.get(path);
      if (value === undefined) throw new Error(`missing ${path}`);
      return value;
    },
    writeText: async (path: string, value: string) => {
      calls.writes += 1;
      files.set(path, value);
    },
    queueIndex: async () => {
      calls.queueIndex += 1;
      return '55555555-5555-4555-8555-555555555555';
    },
    indexJob: async () => ({ status: 'completed' }),
    queueExport: async () => {
      calls.queueExport += 1;
      return '66666666-6666-4666-8666-666666666666';
    },
    editJob: async () => job,
  };
  const progress = { emit: async () => ({}) };
  const service = new OrchestratorService(
    repository as never,
    progress as never,
    {} as never,
  );
  const commit = (
    service as unknown as {
      commit: (
        jobValue: EditJob,
        planValue: ReturnType<typeof plan>,
        candidate: Record<string, unknown>,
        validation: {
          child: Record<string, unknown>;
          report: Record<string, unknown>;
        },
      ) => Promise<Record<string, unknown>>;
    }
  ).commit.bind(service);
  return { commit, files, calls };
}

function exactValidation() {
  const hash = sha256('candidate');
  return {
    child: {
      status: 'completed',
      source_storage_path: CANDIDATE_PATH,
      source_sha256: hash,
      edit_job_id: JOB_ID,
    },
    report: { status: 'passed', valid: true },
  };
}

test('planning debug output lists every operation and target within the event limit', () => {
  const debugPlan = ToolPlanSchema.parse({
    schema_version: 1,
    summary: 'Add support legs.',
    target_part_id: PART_ID,
    base_source_sha256: 'a'.repeat(64),
    operations: [
      {
        tool: 'add_model_parameter',
        name: 'leg_height_mm',
        field_source: 'leg_height_mm: float = 3.0',
      },
      {
        tool: 'add_cad_feature',
        semantic_id: 'soap_holder_support_legs',
        function_name: 'build_soap_holder_support_legs',
        role: 'support_legs',
        parameters: ['leg_height_mm'],
        depends_on: ['soap_holder_body'],
        search_keys: ['legs'],
        function_source:
          'def build_soap_holder_support_legs(params):\n    return make_legs(params)',
      },
      {
        tool: 'replace_build_model_body',
        target_id: `${PART_ID}:build_model_body:build_model`,
        target_fingerprint: 'b'.repeat(64),
        replacement_source:
          'body = build_body(params)\nlegs = build_soap_holder_support_legs(params)\nreturn body.union(legs)',
      },
    ],
  });

  const debug = planOperationDebug(debugPlan);

  assert.equal(debug.operations.length, 3);
  assert.deepEqual(
    debug.operations.map(({ tool, target }) => ({ tool, target })),
    [
      { tool: 'add_model_parameter', target: 'leg_height_mm' },
      { tool: 'add_cad_feature', target: 'soap_holder_support_legs' },
      { tool: 'replace_build_model_body', target: 'build_model' },
    ],
  );
  assert.match(debug.message, /1:add_param\[leg_height_mm\]/);
  assert.match(debug.message, /2:add_feature\[soap_holder_sup…\]/);
  assert.match(debug.message, /3:replace_build\[build_model\]/);
  assert.ok(debug.message.length < 500);
});

test('planning debug exposes bounded dependency impact decisions', () => {
  const debugPlan = ToolPlanSchema.parse({
    schema_version: 2,
    summary: 'Make the holder square.',
    target_part_id: PART_ID,
    base_source_sha256: 'a'.repeat(64),
    operations: [
      {
        tool: 'replace_parameter_field',
        target_id: `${PART_ID}:model_parameter:holder_width_mm`,
        target_fingerprint: 'b'.repeat(64),
        replacement_source: 'holder_width_mm: float = 120.0',
      },
    ],
    impact_review: [
      {
        semantic_id: 'soap_holder_support_legs',
        decision: 'verified_compatible',
        reason: 'Leg offsets derive from holder_width_mm.',
      },
    ],
  });

  assert.deepEqual(planImpactDebug(debugPlan), [
    {
      semantic_id: 'soap_holder_support_legs',
      decision: 'verified_compatible',
      reason: 'Leg offsets derive from holder_width_mm.',
    },
  ]);
});

test('validation diagnostics expose bounded structured error codes', () => {
  assert.deepEqual(
    validationDiagnosticCodes({
      status: 'failed',
      error_code: 'VALIDATION_FAILED',
      errors: [
        { code: 'PARAMETER_METADATA_MISMATCH' },
        {
          details: {
            diagnostics: [
              { code: 'DEPENDENCY_DATAFLOW_MISMATCH' },
              { code: 'PARAMETER_METADATA_MISMATCH' },
            ],
          },
        },
      ],
    }),
    [
      'VALIDATION_FAILED',
      'PARAMETER_METADATA_MISMATCH',
      'DEPENDENCY_DATAFLOW_MISMATCH',
    ],
  );
});

test('planning debug output keeps all twelve operation slots visible', () => {
  const debugPlan = ToolPlanSchema.parse({
    schema_version: 1,
    summary: 'Inspect the maximum operation list.',
    target_part_id: PART_ID,
    base_source_sha256: 'a'.repeat(64),
    operations: Array.from({ length: 12 }, (_, index) => ({
      tool: 'delete_cad_feature',
      target_id: `${PART_ID}:cad_feature:long_semantic_feature_${index + 1}`,
      target_fingerprint: 'b'.repeat(64),
    })),
  });

  const debug = planOperationDebug(debugPlan);

  assert.equal(debug.operations.length, 12);
  assert.match(debug.message, /12:delete_feature/);
  assert.ok(debug.message.length < 500);
});

test('commit accepts only exact proof, writes canonical source, reindexes, and queues export', async () => {
  const job = editJob();
  const { commit, files, calls } = harness(job);
  const result = await commit(
    job,
    plan(job.accepted_source_sha256!),
    {
      candidate_path: CANDIDATE_PATH,
      candidate_sha256: sha256('candidate'),
      changed_symbols: ['build_model'],
    },
    exactValidation(),
  );

  assert.equal(files.get(CANONICAL_PATH), 'candidate');
  assert.equal(calls.writes, 1);
  assert.equal(calls.queueIndex, 1);
  assert.equal(calls.queueExport, 1);
  assert.equal(result.status, 'completed');
});

test('commit rejects mismatched validation proof before canonical mutation', async () => {
  const job = editJob();
  const { commit, calls } = harness(job);
  const validation = exactValidation();
  validation.child.source_sha256 = 'b'.repeat(64);

  await assert.rejects(
    commit(
      job,
      plan(job.accepted_source_sha256!),
      {
        candidate_path: CANDIDATE_PATH,
        candidate_sha256: sha256('candidate'),
      },
      validation,
    ),
    (error: unknown) =>
      error instanceof WorkflowError && error.code === 'VALIDATION_PROOF_MISMATCH',
  );
  assert.equal(calls.writes, 0);
});

test('commit rejects a stale accepted source instead of overwriting it', async () => {
  const job = editJob();
  const { commit, calls } = harness(job, 'concurrent accepted source');

  await assert.rejects(
    commit(
      job,
      plan(job.accepted_source_sha256!),
      {
        candidate_path: CANDIDATE_PATH,
        candidate_sha256: sha256('candidate'),
      },
      exactValidation(),
    ),
    (error: unknown) => error instanceof WorkflowError && error.code === 'SOURCE_CHANGED',
  );
  assert.equal(calls.writes, 0);
  assert.equal(calls.queueIndex, 0);
  assert.equal(calls.queueExport, 0);
});

test('feature provenance target mismatch replans with semantic replacement', async () => {
  let job = editJob({
    state: 'received',
    attempt_count: 0,
    accepted_source_sha256: null,
    original_storage_path: null,
    current_candidate_path: null,
    current_candidate_sha256: null,
    validation_job_id: null,
  });
  const accepted = 'accepted';
  const candidate = 'candidate';
  const candidateHash = sha256(candidate);
  const originalPath = `${PROJECT_ID}/candidates/cad/${PART_ID}/${JOB_ID}/original/model.py`;
  const files = new Map([[CANONICAL_PATH, accepted]]);
  const toolJobs = new Map<string, Record<string, unknown>>();
  const reasonerContexts: Array<Record<string, unknown>> = [];
  const validationAttempts: number[] = [];
  let planCalls = 0;
  const repository = {
    patchEditJob: async (_id: string, values: Record<string, unknown>) => {
      job = { ...job, ...values } as EditJob;
      return job;
    },
    heartbeatEditJob: async () => undefined,
    queueIndex: async () => '55555555-5555-4555-8555-555555555555',
    indexJob: async () => ({ status: 'completed' }),
    editJob: async () => job,
    canonicalPath: () => CANONICAL_PATH,
    originalPath: () => originalPath,
    readText: async (path: string) => {
      const value = files.get(path);
      if (value === undefined) throw new WorkflowError('SOURCE_MISSING', `Missing ${path}`);
      return value;
    },
    writeText: async (path: string, value: string) => {
      files.set(path, value);
    },
    toolJobFor: async (_jobId: string, attempt: number, kind: string) =>
      toolJobs.get(`${attempt}:${kind}`) ?? null,
    toolJob: async (id: string) =>
      [...toolJobs.values()].find((item) => item.id === id)!,
    queueToolJob: async (input: {
      attempt: number;
      kind: string;
      payload: Record<string, unknown>;
    }) => {
      const key = `${input.attempt}:${input.kind}`;
      if (input.kind === 'prepare_context') {
        const child = {
          id: 'context-tool',
          status: 'completed',
          input: input.payload,
          result: {
            part_id: PART_ID,
            part_name: 'soap holder',
            storage_path: CANONICAL_PATH,
            base_source_sha256: sha256(accepted),
            semantic_ids: ['soap_holder_body'],
            existing_features: [{ semantic_id: 'soap_holder_body' }],
          },
        };
        toolJobs.set(key, child);
        return child;
      }
      const planInput = input.payload.plan as ReturnType<typeof plan>;
      if (input.attempt === 1) {
        const child = {
          id: 'failed-tool',
          status: 'failed',
          input: input.payload,
          error_code: 'FEATURE_REPLACEMENT_TARGET_INVALID',
          error_message:
            'replace_function_body cannot target a cad_feature provenance block.',
        };
        toolJobs.set(key, child);
        return child;
      }
      files.set(CANDIDATE_PATH, candidate);
      const child = {
        id: 'successful-tool',
        status: 'completed',
        input: { ...input.payload, plan: planInput },
        result: {
          candidate_path: CANDIDATE_PATH,
          candidate_sha256: candidateHash,
          changed_symbols: ['soap_holder_support_legs'],
        },
      };
      toolJobs.set(key, child);
      return child;
    },
    queueValidation: async ({ attempt }: { attempt: number }) => {
      if (attempt > job.attempt_count + 1) {
        throw new Error('Candidate validation attempt is out of sequence.');
      }
      validationAttempts.push(attempt);
      job = { ...job, attempt_count: attempt, validation_job_id: 'validation' };
      return 'validation';
    },
    generationJob: async () => ({
      status: 'completed',
      source_storage_path: CANDIDATE_PATH,
      source_sha256: candidateHash,
      edit_job_id: JOB_ID,
      result: { status: 'passed', valid: true },
    }),
    queueExport: async () => '66666666-6666-4666-8666-666666666666',
  };
  const progress = { emit: async () => ({}) };
  const reasoner = {
    createPlan: async ({ context }: { context: Record<string, unknown> }) => {
      reasonerContexts.push(context);
      planCalls += 1;
      if (planCalls === 1) {
        return ToolPlanSchema.parse({
          schema_version: 1,
          summary: 'Incorrectly target the feature provenance block.',
          target_part_id: PART_ID,
          base_source_sha256: sha256(accepted),
          operations: [{
            tool: 'replace_function_body',
            target_id: `${PART_ID}:cad_feature:soap_holder_body`,
            target_fingerprint: 'a'.repeat(64),
            replacement_source: 'return body',
          }],
        });
      }
      return plan(sha256(accepted));
    },
  };
  const service = new OrchestratorService(
    repository as never,
    progress as never,
    reasoner as never,
  );
  const process = (
    service as unknown as {
      process: (jobValue: EditJob) => Promise<Record<string, unknown>>;
    }
  ).process.bind(service);

  const result = await process(job);

  assert.equal(result.status, 'completed');
  assert.equal(planCalls, 2);
  assert.deepEqual(validationAttempts, [2]);
  assert.equal(
    (reasonerContexts[1].planning_feedback as Record<string, unknown>).suggested_operation,
    'replace_cad_feature_body',
  );
  assert.equal(files.get(CANONICAL_PATH), candidate);
});

test('validation repair passes the exact previous plan and candidate proof', async () => {
  const job = editJob();
  const previousPlan = plan(job.accepted_source_sha256!);
  const queuedPayloads: Record<string, unknown>[] = [];
  const progressMetadata: Record<string, unknown>[] = [];
  const repository = {
    patchEditJob: async (_id: string, values: Record<string, unknown>) =>
      ({ ...job, ...values }),
    heartbeatEditJob: async () => undefined,
    queueToolJob: async (input: { payload: Record<string, unknown> }) => {
      queuedPayloads.push(input.payload);
      return {
        id: 'repair-context-tool',
        status: 'completed',
        result: {
          part_id: PART_ID,
          base_source_sha256: sha256('candidate'),
          previous_plan: input.payload.previous_plan,
          repair_source: input.payload.repair_source,
          validation: input.payload.validation,
        },
      };
    },
  };
  const progress = {
    emit: async (
      _jobId: string,
      _eventType: string,
      _state: string,
      _message: string,
      metadata: Record<string, unknown>,
    ) => {
      progressMetadata.push(metadata);
      return {};
    },
  };
  const service = new OrchestratorService(
    repository as never,
    progress as never,
    {} as never,
  );
  const repairContext = (
    service as unknown as {
      repairContext: (
        jobValue: EditJob,
        candidate: Record<string, unknown>,
        planValue: ReturnType<typeof plan>,
        toolJobId: string,
        validation: Record<string, unknown>,
        nextAttempt: number,
      ) => Promise<Record<string, unknown>>;
    }
  ).repairContext.bind(service);
  const validation = {
    status: 'failed',
    errors: [{ code: 'PARAMETER_METADATA_MISMATCH' }],
  };

  const context = await repairContext(
    job,
    {
      candidate_path: CANDIDATE_PATH,
      candidate_sha256: sha256('candidate'),
    },
    previousPlan,
    'previous-tool-job',
    validation,
    2,
  );

  assert.deepEqual(queuedPayloads[0]?.previous_plan, previousPlan);
  assert.equal(queuedPayloads[0]?.repair_source, 'validation');
  assert.deepEqual(queuedPayloads[0]?.validation, validation);
  assert.deepEqual(context.previous_plan, previousPlan);
  assert.equal(context.previous_tool_job_id, 'previous-tool-job');
  assert.deepEqual(context.diagnostic_codes, [
    'PARAMETER_METADATA_MISMATCH',
  ]);
  assert.ok(
    progressMetadata.some(
      (metadata) => metadata.repair_source === 'validation',
    ),
  );
});

test('a reclaimed post-commit workflow reuses its persisted index child', async () => {
  const job = editJob({
    state: 'reindexing',
    index_job_id: '55555555-5555-4555-8555-555555555555',
  });
  const { commit, calls } = harness(job, 'candidate');
  await commit(
    job,
    plan(job.accepted_source_sha256!),
    {
      candidate_path: CANDIDATE_PATH,
      candidate_sha256: sha256('candidate'),
    },
    exactValidation(),
  );

  assert.equal(calls.writes, 0);
  assert.equal(calls.queueIndex, 0);
  assert.equal(calls.queueExport, 1);
});
