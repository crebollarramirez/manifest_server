import {
  ConnectedSocket,
  MessageBody,
  OnGatewayDisconnect,
  OnGatewayInit,
  SubscribeMessage,
  WebSocketGateway,
} from '@nestjs/websockets';
import { Inject, OnModuleDestroy } from '@nestjs/common';
import type { Subscription } from 'rxjs';
import type WebSocket from 'ws';
import { CadAgentRepository } from './cad-agent.repository';
import {
  AckMessageSchema,
  CadEditSubmissionSchema,
  EditJobEvent,
  PartRecord,
  SubscribeMessageSchema,
  UnsubscribeMessageSchema,
  WorkflowError,
} from './contracts';
import { ProgressService } from './progress.service';
import { SubmissionService } from './submission.service';
import { publicJob } from './public-job';
import { MeshGenerationService } from './mesh-generation.service';

type SubscriptionState = {
  replaying: boolean;
  highWatermark: number;
  buffer: EditJobEvent[];
};

const TERMINAL_JOB_STATUSES = new Set(['completed', 'failed', 'cancelled']);
const TERMINAL_EVENT_TYPES = new Set(['job.completed', 'job.failed']);

function send(client: WebSocket, event: string, data: unknown) {
  if (client.readyState === client.OPEN) {
    client.send(JSON.stringify({ event, data }));
  }
}

function errorPayload(error: unknown) {
  return error instanceof WorkflowError
    ? { code: error.code, message: error.message }
    : { code: 'INTERNAL_ERROR', message: 'The CAD agent could not process the WebSocket request.' };
}

@WebSocketGateway({ path: '/v1/cad-edits/ws' })
export class CadEditsGateway
  implements OnGatewayInit, OnGatewayDisconnect, OnModuleDestroy
{
  private readonly clients = new Map<WebSocket, Map<string, SubscriptionState>>();
  private progressSubscription?: Subscription;

  constructor(
    @Inject(CadAgentRepository) private readonly repository: CadAgentRepository,
    @Inject(SubmissionService) private readonly submissions: SubmissionService,
    @Inject(ProgressService) private readonly progress: ProgressService,
    @Inject(MeshGenerationService) private readonly mesh: MeshGenerationService,
  ) {}

  afterInit() {
    this.progressSubscription = this.progress.events().subscribe((event) => {
      for (const [client, subscriptions] of this.clients) {
        const state = subscriptions.get(event.edit_job_id);
        if (!state) continue;
        if (event.sequence <= state.highWatermark) continue;
        if (state.replaying) {
          state.buffer.push(event);
        } else {
          send(client, 'cad.edit.progress', event);
          state.highWatermark = event.sequence;
        }
        if (TERMINAL_EVENT_TYPES.has(event.event_type)) {
          subscriptions.delete(event.edit_job_id);
          this.progress.unwatch(event.edit_job_id);
        }
      }
    });
  }

  handleDisconnect(client: WebSocket) {
    const subscriptions = this.clients.get(client);
    if (subscriptions) {
      for (const jobId of subscriptions.keys()) this.progress.unwatch(jobId);
    }
    this.clients.delete(client);
  }

  onModuleDestroy() {
    this.progressSubscription?.unsubscribe();
    for (const subscriptions of this.clients.values()) {
      for (const jobId of subscriptions.keys()) this.progress.unwatch(jobId);
    }
    this.clients.clear();
  }

  private subscriptions(client: WebSocket) {
    let subscriptions = this.clients.get(client);
    if (!subscriptions) {
      subscriptions = new Map();
      this.clients.set(client, subscriptions);
    }
    return subscriptions;
  }

  private async replay(client: WebSocket, jobId: string, afterSequence: number) {
    const subscriptions = this.subscriptions(client);
    const state: SubscriptionState = {
      replaying: true,
      highWatermark: 0,
      buffer: [],
    };
    if (subscriptions.has(jobId)) this.progress.unwatch(jobId);
    subscriptions.set(jobId, state);
    this.progress.watch(jobId, afterSequence);

    try {
      const job = await this.repository.editJob(jobId);
      state.highWatermark = job.last_event_sequence;
      const events = await this.repository.events(jobId, afterSequence, state.highWatermark);
      send(client, 'cad.edit.snapshot', {
        job: publicJob(job as unknown as Record<string, unknown>),
        events,
      });

      const buffered = state.buffer
        .filter((event) => event.sequence > state.highWatermark)
        .sort((left, right) => left.sequence - right.sequence);
      state.replaying = false;
      state.buffer = [];
      for (const event of buffered) {
        send(client, 'cad.edit.progress', event);
        state.highWatermark = event.sequence;
      }
      if (TERMINAL_JOB_STATUSES.has(job.status)) {
        subscriptions.delete(jobId);
        this.progress.unwatch(jobId);
      }
    } catch (error) {
      subscriptions.delete(jobId);
      this.progress.unwatch(jobId);
      throw error;
    }
  }

  @SubscribeMessage('cad.edit.submit')
  async submit(@ConnectedSocket() client: WebSocket, @MessageBody() body: unknown) {
    try {
      const parsed = CadEditSubmissionSchema.safeParse(body);
      if (!parsed.success) {
        throw new WorkflowError(
          'INVALID_REQUEST',
          parsed.error.issues[0]?.message ?? 'Invalid CAD request.',
        );
      }
      if (parsed.data.part_id) {
        const part = await this.repository.part(
          parsed.data.project_id,
          parsed.data.part_id,
        ) as PartRecord;
        if (part.part_type === 'mesh') {
          const messages = parsed.data.messages ?? [
            { role: 'user' as const, content: parsed.data.request_text },
          ];
          const jobId = await this.mesh.generate(part, messages);
          send(client, 'cad.mesh.accepted', {
            status: 'queued',
            message: `Updated mesh part "${part.part_name}" and queued its export.`,
            job_type: 'export_mesh',
            project_id: parsed.data.project_id,
            part_id: part.id,
            job_id: jobId,
          });
          return;
        }
      }
      const result = await this.submissions.submit(parsed.data);
      send(client, 'cad.edit.accepted', {
        job_id: result.job.id,
        status: result.job.status,
        state: result.job.state,
        client_request_id: result.client_request_id,
        deduplicated: result.deduplicated,
      });
      await this.replay(client, result.job.id, 0);
    } catch (error) {
      send(client, 'cad.edit.error', errorPayload(error));
    }
  }

  @SubscribeMessage('cad.edit.subscribe')
  async subscribe(@ConnectedSocket() client: WebSocket, @MessageBody() body: unknown) {
    const parsed = SubscribeMessageSchema.safeParse(body);
    if (!parsed.success) {
      send(client, 'cad.edit.error', {
        code: 'INVALID_SUBSCRIPTION',
        message: parsed.error.issues[0]?.message ?? 'Invalid subscription.',
      });
      return;
    }
    try {
      await this.replay(client, parsed.data.job_id, parsed.data.after_sequence);
    } catch (error) {
      send(client, 'cad.edit.error', errorPayload(error));
    }
  }

  @SubscribeMessage('cad.edit.unsubscribe')
  unsubscribe(@ConnectedSocket() client: WebSocket, @MessageBody() body: unknown) {
    const parsed = UnsubscribeMessageSchema.safeParse(body);
    if (!parsed.success) {
      send(client, 'cad.edit.error', {
        code: 'INVALID_SUBSCRIPTION',
        message: parsed.error.issues[0]?.message ?? 'Invalid unsubscription.',
      });
      return;
    }
    const subscriptions = this.subscriptions(client);
    if (subscriptions.delete(parsed.data.job_id)) {
      this.progress.unwatch(parsed.data.job_id);
    }
    send(client, 'cad.edit.unsubscribed', parsed.data);
  }

  @SubscribeMessage('cad.edit.ack')
  acknowledge(@ConnectedSocket() client: WebSocket, @MessageBody() body: unknown) {
    const parsed = AckMessageSchema.safeParse(body);
    if (!parsed.success) {
      send(client, 'cad.edit.error', {
        code: 'INVALID_ACKNOWLEDGEMENT',
        message: parsed.error.issues[0]?.message ?? 'Invalid acknowledgement.',
      });
      return;
    }
    send(client, 'cad.edit.acknowledged', parsed.data);
  }
}
