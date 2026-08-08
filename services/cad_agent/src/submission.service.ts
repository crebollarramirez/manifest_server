import { createHash, randomUUID } from 'node:crypto';
import { Inject, Injectable } from '@nestjs/common';
import { CadAgentRepository } from './cad-agent.repository';
import {
  CadEditSubmission,
  CadEditSubmissionSchema,
  EditJob,
  WorkflowError,
} from './contracts';

function fingerprint(value: Record<string, unknown>): string {
  return createHash('sha256').update(JSON.stringify(value)).digest('hex');
}

export type SubmissionResult = {
  job: EditJob;
  client_request_id: string;
  deduplicated: boolean;
};

@Injectable()
export class SubmissionService {
  constructor(@Inject(CadAgentRepository) private readonly repository: CadAgentRepository) {}

  async submit(raw: unknown): Promise<SubmissionResult> {
    const parsed = CadEditSubmissionSchema.safeParse(raw);
    if (!parsed.success) {
      throw new WorkflowError('INVALID_REQUEST', parsed.error.issues[0]?.message ?? 'Invalid CAD request.');
    }
    const request: CadEditSubmission = parsed.data;
    const clientRequestId = request.client_request_id ?? randomUUID();
    const messages = request.messages ?? [
      { role: 'user' as const, content: request.request_text.trim() },
    ];
    const requestFingerprint = fingerprint({
      project_id: request.project_id,
      part_id: request.part_id ?? null,
      request_text: request.request_text.trim(),
      messages,
    });

    const existing = await this.repository.editJobForClientRequest(clientRequestId);
    if (existing) {
      if (existing.request_fingerprint !== requestFingerprint) {
        throw new WorkflowError(
          'CLIENT_REQUEST_ID_CONFLICT',
          'The client request ID is already bound to a different CAD request.',
        );
      }
      return { job: existing, client_request_id: clientRequestId, deduplicated: true };
    }

    await this.repository.project(request.project_id);
    if (request.part_id) {
      const part = await this.repository.part(request.project_id, request.part_id);
      if (part.part_type !== 'cad') {
        throw new WorkflowError('INVALID_PART_TYPE', 'CAD edit requests may target only CAD parts.');
      }
    }

    const job = await this.repository.submitEditJob({
      projectId: request.project_id,
      requestText: request.request_text,
      messages,
      requestedPartId: request.part_id ?? null,
      // The Python CAD editor inspects the claimed source and is authoritative
      // for switching an exact blank marker to initial_design.
      workflowMode: 'edit',
      clientRequestId,
      requestFingerprint,
      resolvedTargets: [],
    });
    return { job, client_request_id: clientRequestId, deduplicated: false };
  }
}
