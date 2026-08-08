import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import test from 'node:test';
import { HttpException } from '@nestjs/common';
import { CadActionsController } from '../src/cad-actions.controller';
import { CadActionsService } from '../src/cad-actions.service';
import { ActionError, EditJob, WorkflowError } from '../src/contracts';
import {
  composeMeshModelSource,
  generatedMeshModelBody,
} from '../src/mesh-generation.service';

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const PART_ID = '22222222-2222-4222-8222-222222222222';
const JOB_ID = '33333333-3333-4333-8333-333333333333';
const CLIENT_ID = '44444444-4444-4444-8444-444444444444';
const project = { id: PROJECT_ID, project_name: 'Desk Mount' };
const part = {
  id: PART_ID,
  project_id: PROJECT_ID,
  part_name: 'Left Bracket',
  part_type: 'cad' as const,
};

function editJob(overrides: Partial<EditJob> = {}): EditJob {
  return {
    id: JOB_ID,
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
    created_at: '2026-08-02T00:00:00Z',
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

function actionService(options: {
  repository?: Record<string, unknown>;
  mesh?: Record<string, unknown>;
} = {}): CadActionsService {
  const repository = {
    project: async () => project,
    part: async () => part,
    projects: async () => [project],
    parts: async () => [part],
    hasCadParts: async () => true,
    ...options.repository,
  };
  const mesh = {
    starterSource: () => 'mesh starter',
    generate: async () => JOB_ID,
    ...options.mesh,
  };
  return new CadActionsService(repository as never, mesh as never);
}

test('action contract links projects and parts by durable ID and rejects legacy names', async () => {
  const service = actionService();
  const linkedProject = await service.execute({ action: 'link_project', project_id: PROJECT_ID });
  const linkedPart = await service.execute({
    action: 'link_part',
    project_id: PROJECT_ID,
    part_id: PART_ID,
  });

  assert.match(String(linkedProject.message), new RegExp(PROJECT_ID));
  assert.match(String(linkedPart.message), new RegExp(PART_ID));
  await assert.rejects(
    service.execute({ action: 'link_part', project_id: PROJECT_ID, part_name: 'Left Bracket' }),
    (error: unknown) => error instanceof ActionError && error.status === 400,
  );
});

test('project and part listings expose names and durable IDs in stable response objects', async () => {
  const service = actionService();
  const projects = await service.execute({ action: 'list_projects' });
  const parts = await service.execute({ action: 'list_parts', project_id: PROJECT_ID });

  assert.match(String(projects.message), new RegExp(`Desk Mount id=${PROJECT_ID}`));
  assert.deepEqual(projects.projects, [project]);
  assert.match(String(parts.message), new RegExp(`Left Bracket \\[cad\\] id=${PART_ID}`));
  assert.deepEqual(parts.parts, [part]);
});

test('project creation and deletion use durable IDs and the full deletion guard', async () => {
  const calls: string[] = [];
  const service = actionService({
    repository: {
      createProject: async () => project,
      hasRunningIndexJobs: async () => false,
      hasRunningEditJobs: async () => false,
      hasRunningGenerationJobs: async () => false,
      cancelQueuedIndexJobs: async () => { calls.push('cancel-index'); },
      cancelQueuedEditJobs: async () => { calls.push('cancel-edits'); },
      cancelQueuedGenerationJobs: async () => { calls.push('cancel-generation'); },
      deleteStoragePrefix: async (prefix: string) => { calls.push(`storage:${prefix}`); },
      deleteProject: async (id: string) => { calls.push(`project:${id}`); },
    },
  });

  const created = await service.execute({ action: 'create_project', project_name: 'Desk Mount' });
  const deleted = await service.execute({ action: 'delete_project', project_id: PROJECT_ID });

  assert.match(String(created.message), new RegExp(PROJECT_ID));
  assert.match(String(deleted.message), new RegExp(PROJECT_ID));
  assert.deepEqual(calls, [
    'cancel-index',
    'cancel-edits',
    'cancel-generation',
    `storage:${PROJECT_ID}`,
    `project:${PROJECT_ID}`,
  ]);
});

test('CAD part creation initializes both files and queues best-effort indexing', async () => {
  const uploads: Array<Record<string, unknown>> = [];
  const service = actionService({
    repository: {
      createPart: async () => part,
      uploadText: async (path: string, content: string, contentType: string, upsert: boolean) => {
        uploads.push({ path, content, contentType, upsert });
      },
      queueStandaloneIndexJob: async () => ({ id: JOB_ID, status: 'queued' }),
    },
  });

  const result = await service.execute({
    action: 'create_part',
    project_id: PROJECT_ID,
    part_name: part.part_name,
    part_type: 'cad',
  });

  assert.equal(uploads.length, 2);
  assert.equal(
    uploads[0].content,
    'from cadquery_runtime import cad_part, cq, dataclass\n'
      + '\n'
      + '@dataclass(frozen=True)\n'
      + 'class ModelParams:\n'
      + '    pass\n'
      + '\n'
      + '\n'
      + 'def build_model(params: ModelParams):\n'
      + '    return cq.Workplane("XY")\n',
  );
  assert.equal(uploads[0].upsert, false);
  assert.equal(uploads[1].content, '{}\n');
  assert.equal(result.index_job_id, JOB_ID);
  assert.deepEqual(result.warnings, []);
});

test('part creation rolls back storage and catalog row when initialization fails', async () => {
  const calls: string[] = [];
  const service = actionService({
    repository: {
      createPart: async () => part,
      uploadText: async () => { throw new Error('storage unavailable'); },
      deleteStoragePrefix: async () => { calls.push('storage'); },
      deletePart: async () => { calls.push('row'); },
    },
  });

  await assert.rejects(
    service.execute({
      action: 'create_part',
      project_id: PROJECT_ID,
      part_name: part.part_name,
      part_type: 'cad',
    }),
    /storage unavailable/,
  );
  assert.deepEqual(calls, ['storage', 'row']);
});

test('CAD creation succeeds with an explicit warning when automatic indexing fails', async () => {
  const service = actionService({
    repository: {
      createPart: async () => part,
      uploadText: async () => undefined,
      queueStandaloneIndexJob: async () => { throw new Error('index queue unavailable'); },
    },
  });

  const result = await service.execute({
    action: 'create_part',
    project_id: PROJECT_ID,
    part_name: part.part_name,
    part_type: 'cad',
  });

  assert.equal(result.index_job_id, null);
  assert.equal(result.index_status, 'not_queued');
  assert.match(String((result.warnings as string[])[0]), /Automatic indexing/);
});

test('manual validation, export, indexing, and status actions preserve job contracts', async () => {
  const source = 'accepted source';
  const queued: Array<Record<string, unknown>> = [];
  const service = actionService({
    repository: {
      partById: async () => part,
      readText: async () => source,
      queueGenerationJob: async (_part: unknown, type: string, hash: string | null) => {
        queued.push({ type, hash });
        return JOB_ID;
      },
      queueStandaloneIndexJob: async (_projectId: string, type: string, requestText: string | null) => {
        queued.push({ type, requestText });
        return { id: JOB_ID, status: 'queued' };
      },
      indexJobInProject: async () => ({ id: JOB_ID, status: 'completed' }),
      editJob: async () => editJob({ history: [{ event: 'planned', secret: 'hidden' }] }),
      events: async () => [{ sequence: 2 }],
    },
  });

  await service.execute({ action: 'export_part', part_id: PART_ID });
  await service.execute({ action: 'validate_part', part_id: PART_ID });
  await service.execute({ action: 'index_project', project_id: PROJECT_ID });
  await service.execute({
    action: 'test_index',
    project_id: PROJECT_ID,
    request_text: 'find holes',
  });
  const indexStatus = await service.execute({
    action: 'get_index_job',
    project_id: PROJECT_ID,
    job_id: JOB_ID,
  });
  const editStatus = await service.execute({
    action: 'get_edit_job',
    job_id: JOB_ID,
    after_sequence: 1,
  });

  const expectedHash = createHash('sha256').update(source).digest('hex');
  assert.deepEqual(queued[0], { type: 'export_cad', hash: expectedHash });
  assert.deepEqual(queued[1], { type: 'validate_cad', hash: expectedHash });
  assert.equal((indexStatus.job as Record<string, unknown>).status, 'completed');
  assert.deepEqual(editStatus.events, [{ sequence: 2 }]);
  assert.deepEqual((editStatus.job as Record<string, unknown>).history, [{ event: 'planned' }]);
});

test('part deletion cancels queued jobs, repeats race checks, and removes every prefix', async () => {
  const calls: string[] = [];
  const service = actionService({
    repository: {
      hasRunningEditJobs: async () => false,
      hasRunningGenerationJobs: async () => false,
      cancelQueuedEditJobs: async () => { calls.push('cancel-edits'); },
      cancelQueuedGenerationJobs: async () => { calls.push('cancel-generation'); },
      deleteStoragePrefix: async (prefix: string) => { calls.push(prefix); },
      deletePart: async () => { calls.push('delete-row'); },
    },
  });

  await service.execute({ action: 'delete_part', project_id: PROJECT_ID, part_id: PART_ID });

  assert.deepEqual(calls.slice(0, 2), ['cancel-edits', 'cancel-generation']);
  assert.ok(calls.some((value) => value.endsWith(`/parts/cad/${PART_ID}`)));
  assert.ok(calls.some((value) => value.endsWith(`/exports/${PART_ID}`)));
  assert.ok(calls.some((value) => value.endsWith(`/candidates/cad/${PART_ID}`)));
  assert.equal(calls.at(-1), 'delete-row');
});

test('deletion stops before cancellation when applicable work is running', async () => {
  let cancelled = false;
  const service = actionService({
    repository: {
      hasRunningEditJobs: async () => true,
      cancelQueuedEditJobs: async () => { cancelled = true; },
    },
  });
  await assert.rejects(
    service.execute({ action: 'delete_part', project_id: PROJECT_ID, part_id: PART_ID }),
    (error: unknown) => error instanceof ActionError && error.status === 409,
  );
  assert.equal(cancelled, false);
});

test('HTTP action endpoint rejects agent conversations', async () => {
  const service = actionService();
  await assert.rejects(
    service.execute({
      action: 'chat',
      project_id: PROJECT_ID,
      client_request_id: CLIENT_ID,
      messages: [{ role: 'user', content: 'Add a mounting hole' }],
    }),
    (error: unknown) => error instanceof ActionError && error.status === 400,
  );
});

test('action controller preserves the compatibility error envelope and status', async () => {
  const success = new CadActionsController({
    execute: async () => ({ status: 'listed', projects: [project] }),
  } as never);
  assert.deepEqual(await success.execute({ action: 'list_projects' }), {
    status: 'listed',
    projects: [project],
  });

  const controller = new CadActionsController({
    execute: async () => { throw new ActionError(409, 'busy'); },
  } as never);

  await assert.rejects(
    controller.execute({ action: 'list_projects' }),
    (error: unknown) => {
      assert.ok(error instanceof HttpException);
      assert.equal(error.getStatus(), 409);
      assert.deepEqual(error.getResponse(), { error: 'busy' });
      return true;
    },
  );

  const missing = new CadActionsController({
    execute: async () => { throw new WorkflowError('PROJECT_NOT_FOUND', 'missing'); },
  } as never);
  await assert.rejects(
    missing.execute({ action: 'list_projects' }),
    (error: unknown) => error instanceof HttpException && error.getStatus() === 404,
  );
});

test('mesh response helpers keep the runtime import system-owned and reject status output', () => {
  const source = composeMeshModelSource(
    'from blender_runtime import bpy, bmesh, dataclass, Vector, Matrix, Euler, mesh_part, mm, get_or_create_collection, link_object\n\nprint("mesh")',
  );
  assert.equal(source.match(/from blender_runtime/g)?.length, 1);
  assert.equal(generatedMeshModelBody('{"generated_code":"print(1)"}'), 'print(1)');
  assert.throws(() => generatedMeshModelBody('{"generated_code":"OK"}'), /status value/);
});
