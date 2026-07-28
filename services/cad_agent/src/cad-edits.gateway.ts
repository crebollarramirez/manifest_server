import {
  ConnectedSocket,
  MessageBody,
  OnGatewayDisconnect,
  OnGatewayInit,
  SubscribeMessage,
  WebSocketGateway,
} from '@nestjs/websockets';
import { Inject } from '@nestjs/common';
import type { Subscription } from 'rxjs';
import type WebSocket from 'ws';
import { CadAgentRepository } from './cad-agent.repository';
import {
  AckMessageSchema,
  EditJobEvent,
  SubscribeMessageSchema,
  UnsubscribeMessageSchema,
  WorkflowError,
} from './contracts';
import { ProgressService } from './progress.service';
import { SubmissionService } from './submission.service';
import { publicJob } from './cad-edits.controller';

type SubscriptionState = {
  replaying: boolean;
  highWatermark: number;
  buffer: EditJobEvent[];
};

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
  implements OnGatewayInit, OnGatewayDisconnect
{
  private readonly clients = new Map<WebSocket, Map<string, SubscriptionState>>();
  private progressSubscription?: Subscription;

  constructor(
    @Inject(CadAgentRepository) private readonly repository: CadAgentRepository,
    @Inject(SubmissionService) private readonly submissions: SubmissionService,
    @Inject(ProgressService) private readonly progress: ProgressService,
  ) {}

  afterInit() {
    this.progressSubscription = this.progress.events().subscribe((event) => {
      for (const [client, subscriptions] of this.clients) {
        const state = subscriptions.get(event.edit_job_id);
        if (!state) continue;
        if (state.replaying) {
          state.buffer.push(event);
        } else {
          send(client, 'cad.edit.progress', event);
        }
      }
    });
  }

  handleDisconnect(client: WebSocket) {
    this.clients.delete(client);
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
    subscriptions.set(jobId, state);

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
    for (const event of buffered) send(client, 'cad.edit.progress', event);
  }

  @SubscribeMessage('cad.edit.submit')
  async submit(@ConnectedSocket() client: WebSocket, @MessageBody() body: unknown) {
    try {
      const result = await this.submissions.submit(body);
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
    this.subscriptions(client).delete(parsed.data.job_id);
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
