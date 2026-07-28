import assert from 'node:assert/strict';
import test from 'node:test';
import { Subject } from 'rxjs';
import { CadEditsGateway } from '../src/cad-edits.gateway';
import type { EditJobEvent } from '../src/contracts';

const JOB_ID = '44444444-4444-4444-8444-444444444444';

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

test('subscription replays persisted events and then emits live progress', async () => {
  const stream = new Subject<EditJobEvent>();
  const repository = {
    editJob: async () => ({
      id: JOB_ID,
      status: 'running',
      state: 'planning_edit',
      last_event_sequence: 2,
    }),
    events: async () => [event(1), event(2)],
  };
  const progress = { events: () => stream.asObservable() };
  const gateway = new CadEditsGateway(
    repository as never,
    {} as never,
    progress as never,
  );
  gateway.afterInit();

  const sent: Array<{ event: string; data: unknown }> = [];
  const client = {
    OPEN: 1,
    readyState: 1,
    send: (value: string) => sent.push(JSON.parse(value)),
  };
  await gateway.subscribe(client as never, {
    job_id: JOB_ID,
    after_sequence: 0,
  });
  assert.equal(sent[0].event, 'cad.edit.snapshot');
  assert.deepEqual(
    (sent[0].data as { events: EditJobEvent[] }).events.map((value) => value.sequence),
    [1, 2],
  );

  stream.next(event(3));
  assert.equal(sent[1].event, 'cad.edit.progress');
  assert.equal((sent[1].data as EditJobEvent).sequence, 3);
});
