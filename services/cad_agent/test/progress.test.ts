import assert from 'node:assert/strict';
import test from 'node:test';
import type { EditJobEvent } from '../src/contracts';
import { ProgressService } from '../src/progress.service';

const JOB_ID = '44444444-4444-4444-8444-444444444444';

function event(sequence: number, eventType = 'planning.started'): EditJobEvent {
  return {
    id: `${sequence}1111111-1111-4111-8111-111111111111`.slice(0, 36),
    edit_job_id: JOB_ID,
    sequence,
    event_type: eventType,
    state: eventType === 'job.completed' ? 'completed' : 'planning_edit',
    message: `event ${sequence}`,
    metadata: {},
    created_at: new Date().toISOString(),
  };
}

test('polls durable events written by workers and emits each sequence once', async () => {
  const available = [event(1), event(2)];
  const cursors: number[] = [];
  const repository = {
    eventsForJobs: async (jobCursors: Record<string, number>) => {
      const afterSequence = jobCursors[JOB_ID] ?? 0;
      cursors.push(afterSequence);
      return available.filter((item) => item.sequence > afterSequence);
    },
  };
  const service = new ProgressService(repository as never);
  const received: number[] = [];
  const subscription = service.events().subscribe((item) => received.push(item.sequence));

  service.watch(JOB_ID, 0);
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(received, [1, 2]);

  available.push(event(3));
  await service.poll();
  await service.poll();
  assert.deepEqual(received, [1, 2, 3]);
  assert.ok(cursors.includes(2));
  assert.ok(cursors.includes(3));

  service.unwatch(JOB_ID);
  available.push(event(4));
  await service.poll();
  assert.deepEqual(received, [1, 2, 3]);

  subscription.unsubscribe();
  service.onModuleDestroy();
});

test('reference counts multiple subscribers to the same durable job', async () => {
  let calls = 0;
  const service = new ProgressService({
    eventsForJobs: async () => {
      calls += 1;
      return [];
    },
  } as never);
  service.watch(JOB_ID, 4);
  service.watch(JOB_ID, 4);
  await new Promise<void>((resolve) => setImmediate(resolve));
  service.unwatch(JOB_ID);

  const beforeRemainingWatchPoll = calls;
  await service.poll();
  assert.equal(calls, beforeRemainingWatchPoll + 1);

  service.unwatch(JOB_ID);
  const beforeUnwatchedPoll = calls;
  await service.poll();
  assert.equal(calls, beforeUnwatchedPoll);

  service.onModuleDestroy();
});

test('stops polling a job after relaying its terminal event', async () => {
  let calls = 0;
  const service = new ProgressService({
    eventsForJobs: async () => {
      calls += 1;
      return [event(9, 'job.completed')];
    },
  } as never);
  const received: string[] = [];
  const subscription = service.events().subscribe((item) => received.push(item.event_type));

  service.watch(JOB_ID, 8);
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(received, ['job.completed']);

  const callsAfterTerminal = calls;
  await service.poll();
  assert.equal(calls, callsAfterTerminal);

  subscription.unsubscribe();
  service.onModuleDestroy();
});
