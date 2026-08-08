import { Inject, Injectable, Logger, OnModuleDestroy } from '@nestjs/common';
import { Subject } from 'rxjs';
import { EditJobEvent } from './contracts';
import { CadAgentRepository } from './cad-agent.repository';

type WatchedJob = { cursor: number; references: number };
const MAX_POLL_BATCH_SIZE = 100;
const TERMINAL_EVENT_TYPES = new Set(['job.completed', 'job.failed']);

@Injectable()
export class ProgressService implements OnModuleDestroy {
  private readonly logger = new Logger(ProgressService.name);
  private readonly stream = new Subject<EditJobEvent>();
  private readonly watchedJobs = new Map<string, WatchedJob>();
  private readonly pollMilliseconds: number;
  private readonly pollTimer: NodeJS.Timeout;
  private polling = false;

  constructor(@Inject(CadAgentRepository) private readonly repository: CadAgentRepository) {
    const configured = Number(process.env.CAD_AGENT_EVENT_POLL_INTERVAL_MS ?? 500);
    this.pollMilliseconds = Number.isFinite(configured) && configured >= 100
      ? configured
      : 500;
    this.pollTimer = setInterval(() => void this.poll(), this.pollMilliseconds);
    this.pollTimer.unref();
  }

  onModuleDestroy() {
    clearInterval(this.pollTimer);
    this.stream.complete();
  }

  events() {
    return this.stream.asObservable();
  }

  watch(jobId: string, afterSequence: number) {
    const watched = this.watchedJobs.get(jobId);
    if (watched) {
      watched.references += 1;
      return;
    }
    this.watchedJobs.set(jobId, { cursor: afterSequence, references: 1 });
    void this.poll();
  }

  unwatch(jobId: string) {
    const watched = this.watchedJobs.get(jobId);
    if (!watched) return;
    watched.references -= 1;
    if (watched.references <= 0) this.watchedJobs.delete(jobId);
  }

  async poll(): Promise<void> {
    if (this.polling || this.watchedJobs.size === 0) return;
    this.polling = true;
    try {
      const watchedAtStart = [...this.watchedJobs.entries()];
      const batches = Array.from(
        { length: Math.ceil(watchedAtStart.length / MAX_POLL_BATCH_SIZE) },
        (_, index) => watchedAtStart.slice(
          index * MAX_POLL_BATCH_SIZE,
          (index + 1) * MAX_POLL_BATCH_SIZE,
        ),
      );
      const events = (await Promise.all(
        batches.map((batch) => this.repository.eventsForJobs(
          Object.fromEntries(
            batch.map(([jobId, watched]) => [jobId, watched.cursor]),
          ),
        )),
      )).flat();
      for (const event of events) {
        const watched = this.watchedJobs.get(event.edit_job_id);
        if (!watched || event.sequence <= watched.cursor) continue;
        watched.cursor = event.sequence;
        this.stream.next(event);
        if (TERMINAL_EVENT_TYPES.has(event.event_type)) {
          this.watchedJobs.delete(event.edit_job_id);
        }
      }
    } catch (error) {
      this.logger.warn(
        `Could not relay CAD edit progress: ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    } finally {
      this.polling = false;
    }
  }
}
