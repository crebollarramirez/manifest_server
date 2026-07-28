import { Inject, Injectable } from '@nestjs/common';
import { Subject } from 'rxjs';
import { EditJobEvent } from './contracts';
import { CadAgentRepository } from './cad-agent.repository';

const ALLOWED_METADATA = new Set([
  'workflow_mode',
  'part_id',
  'attempt',
  'max_attempts',
  'changed_symbols',
  'validation_job_id',
  'index_job_id',
  'export_job_id',
  'warning',
  'error_code',
  'tool_job_id',
  'operations',
  'impact_review',
  'repair_source',
  'previous_tool_job_id',
  'candidate_sha256',
  'diagnostic_codes',
]);

@Injectable()
export class ProgressService {
  private readonly stream = new Subject<EditJobEvent>();

  constructor(@Inject(CadAgentRepository) private readonly repository: CadAgentRepository) {}

  events() {
    return this.stream.asObservable();
  }

  async emit(
    jobId: string,
    eventType: string,
    state: string,
    message: string,
    metadata: Record<string, unknown> = {},
  ): Promise<EditJobEvent> {
    const safeMetadata = Object.fromEntries(
      Object.entries(metadata).filter(([key]) => ALLOWED_METADATA.has(key)),
    );
    const event = await this.repository.appendEvent(
      jobId,
      eventType,
      state,
      message.slice(0, 500),
      safeMetadata,
    );
    this.stream.next(event);
    return event;
  }
}
