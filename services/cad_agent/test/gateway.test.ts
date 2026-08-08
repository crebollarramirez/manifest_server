import assert from 'node:assert/strict';
import test from 'node:test';
import { Subject } from 'rxjs';
import { CadEditsGateway } from '../src/cad-edits.gateway';
import type { EditJobEvent } from '../src/contracts';

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const PART_ID = '22222222-2222-4222-8222-222222222222';
const JOB_ID = '44444444-4444-4444-8444-444444444444';
const CLIENT_REQUEST_ID = '55555555-5555-4555-8555-555555555555';

function event(sequence: number): EditJobEvent {
  return {
    id: `${sequence}1111111-1111-4111-8111-111111111111`.slice(0, 36),
    edit_job_id: JOB_ID,
    sequence,
    event_type: sequence === 3 ? 'job.completed' : 'planning.started',
    state: sequence === 3 ? 'completed' : 'planning_edit',
    message: `event ${sequence}`,
    metadata: {},
    created_at: new Date().toISOString(),
  };
}

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: JOB_ID,
    project_id: PROJECT_ID,
    requested_part_id: null,
    workflow_mode: 'edit',
    resolved_part_id: null,
    resolved_targets: [],
    status: 'queued',
    state: 'received',
    attempt_count: 0,
    max_attempts: 3,
    validation_job_id: null,
    index_job_id: null,
    export_job_id: null,
    result: null,
    error_code: null,
    error_message: null,
    client_request_id: CLIENT_REQUEST_ID,
    last_event_sequence: 1,
    created_at: new Date().toISOString(),
    started_at: null,
    heartbeat_at: null,
    completed_at: null,
    ...overrides,
  };
}

function client(sent: Array<{ event: string; data: unknown }>) {
  return {
    OPEN: 1,
    readyState: 1,
    send: (value: string) => sent.push(JSON.parse(value)),
  };
}

test('CAD WebSocket submission queues a durable job and starts replay', async () => {
  const submitted: unknown[] = [];
  const watched: Array<[string, number]> = [];
  const repository = {
    editJob: async () => job(),
    events: async () => [event(1)],
  };
  const submissions = {
    submit: async (input: unknown) => {
      submitted.push(input);
      return {
        job: job(),
        client_request_id: CLIENT_REQUEST_ID,
        deduplicated: false,
      };
    },
  };
  const progress = {
    events: () => new Subject<EditJobEvent>().asObservable(),
    watch: (jobId: string, sequence: number) => watched.push([jobId, sequence]),
    unwatch: () => undefined,
  };
  const gateway = new CadEditsGateway(
    repository as never,
    submissions as never,
    progress as never,
    {} as never,
  );
  const sent: Array<{ event: string; data: unknown }> = [];

  await gateway.submit(client(sent) as never, {
    project_id: PROJECT_ID,
    request_text: 'Add a mounting hole.',
  });

  assert.deepEqual(submitted, [{
    project_id: PROJECT_ID,
    request_text: 'Add a mounting hole.',
  }]);
  assert.equal(sent[0].event, 'cad.edit.accepted');
  assert.equal((sent[0].data as Record<string, unknown>).job_id, JOB_ID);
  assert.equal(sent[1].event, 'cad.edit.snapshot');
  assert.deepEqual(watched, [[JOB_ID, 0]]);
});

test('mesh WebSocket submission remains routed to mesh generation', async () => {
  const generated: unknown[] = [];
  const gateway = new CadEditsGateway(
    {
      part: async () => ({
        id: PART_ID,
        project_id: PROJECT_ID,
        part_name: 'Dragon Body',
        part_type: 'mesh',
      }),
    } as never,
    { submit: async () => assert.fail('Mesh requests must not create CAD edit jobs.') } as never,
    {} as never,
    {
      generate: async (_part: unknown, messages: unknown) => {
        generated.push(messages);
        return JOB_ID;
      },
    } as never,
  );
  const sent: Array<{ event: string; data: unknown }> = [];

  await gateway.submit(client(sent) as never, {
    project_id: PROJECT_ID,
    part_id: PART_ID,
    request_text: 'Make the horns longer.',
    messages: [{ role: 'user', content: 'Make the horns longer.' }],
  });

  assert.equal(sent[0].event, 'cad.mesh.accepted');
  assert.equal((sent[0].data as Record<string, unknown>).job_id, JOB_ID);
  assert.deepEqual(generated, [[{ role: 'user', content: 'Make the horns longer.' }]]);
});

test('subscription replays persisted events, suppresses duplicates, and relays live progress', async () => {
  const stream = new Subject<EditJobEvent>();
  const watched: Array<[string, number]> = [];
  const unwatched: string[] = [];
  const repository = {
    editJob: async () => job({
      status: 'running',
      state: 'planning_edit',
      last_event_sequence: 2,
    }),
    events: async () => [event(1), event(2)],
  };
  const progress = {
    events: () => stream.asObservable(),
    watch: (jobId: string, sequence: number) => watched.push([jobId, sequence]),
    unwatch: (jobId: string) => unwatched.push(jobId),
  };
  const gateway = new CadEditsGateway(
    repository as never,
    {} as never,
    progress as never,
    {} as never,
  );
  gateway.afterInit();

  const sent: Array<{ event: string; data: unknown }> = [];
  const socket = client(sent);
  await gateway.subscribe(socket as never, { job_id: JOB_ID, after_sequence: 0 });
  assert.equal(sent[0].event, 'cad.edit.snapshot');
  assert.deepEqual(
    (sent[0].data as { events: EditJobEvent[] }).events.map((value) => value.sequence),
    [1, 2],
  );
  assert.deepEqual(watched, [[JOB_ID, 0]]);

  stream.next(event(2));
  assert.equal(sent.length, 1);
  stream.next(event(3));
  assert.equal(sent[1].event, 'cad.edit.progress');
  assert.equal((sent[1].data as EditJobEvent).sequence, 3);

  gateway.unsubscribe(socket as never, { job_id: JOB_ID });
  assert.deepEqual(unwatched, [JOB_ID]);
});

test('terminal snapshots are replayed without leaving a live poll watch', async () => {
  const watched: string[] = [];
  const unwatched: string[] = [];
  const gateway = new CadEditsGateway(
    {
      editJob: async () => job({
        status: 'completed',
        state: 'completed',
        last_event_sequence: 3,
      }),
      events: async () => [event(3)],
    } as never,
    {} as never,
    {
      events: () => new Subject<EditJobEvent>().asObservable(),
      watch: (jobId: string) => watched.push(jobId),
      unwatch: (jobId: string) => unwatched.push(jobId),
    } as never,
    {} as never,
  );
  const sent: Array<{ event: string; data: unknown }> = [];

  await gateway.subscribe(client(sent) as never, {
    job_id: JOB_ID,
    after_sequence: 2,
  });

  assert.equal(sent[0].event, 'cad.edit.snapshot');
  assert.deepEqual(watched, [JOB_ID]);
  assert.deepEqual(unwatched, [JOB_ID]);
});
