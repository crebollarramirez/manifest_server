import type { EditJob } from './contracts';

const PUBLIC_JOB_FIELDS = [
  'id',
  'project_id',
  'requested_part_id',
  'workflow_mode',
  'resolved_part_id',
  'resolved_targets',
  'status',
  'state',
  'attempt_count',
  'max_attempts',
  'validation_job_id',
  'index_job_id',
  'export_job_id',
  'result',
  'error_code',
  'error_message',
  'client_request_id',
  'last_event_sequence',
  'created_at',
  'started_at',
  'heartbeat_at',
  'completed_at',
] as const;

const PUBLIC_HISTORY_FIELDS = [
  'recorded_at',
  'event',
  'attempt',
  'part_id',
  'semantic_ids',
  'confidence',
  'reason',
  'candidate_hash',
  'changed_symbols',
  'validation_job_id',
  'validation_status',
  'validation_result',
] as const;

export function publicJob(job: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    PUBLIC_JOB_FIELDS
      .filter((key) => key in job)
      .map((key) => [key, job[key]]),
  );
}

export function publicActionJob(job: EditJob): Record<string, unknown> {
  const history = Array.isArray(job.history)
    ? job.history.map((raw) => {
      const event = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
      return Object.fromEntries(
        PUBLIC_HISTORY_FIELDS
          .filter((key) => event[key] !== undefined)
          .map((key) => [key, event[key]]),
      );
    })
    : [];
  return { ...publicJob(job as unknown as Record<string, unknown>), history };
}
