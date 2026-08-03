import { createHash, randomUUID } from 'node:crypto';
import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { CadAgentRepository } from './cad-agent.repository';
import { EditJob, ToolPlan, ToolPlanSchema, WorkflowError } from './contracts';
import { ProgressService } from './progress.service';
import { ReasonerService } from './reasoner.service';

const TERMINAL = new Set(['completed', 'failed', 'cancelled']);
const REPAIRABLE_TOOL_PREFLIGHT = new Map<string, string>([
  ['FEATURE_NOT_FOUND_FOR_REPLACEMENT', 'add_cad_feature'],
  ['FEATURE_ALREADY_EXISTS_FOR_CREATE', 'replace_cad_feature_body'],
  ['FEATURE_REPLACEMENT_TARGET_INVALID', 'replace_cad_feature_body'],
  ['NEW_FEATURE_NOT_ASSEMBLED', 'replace_build_model_body'],
  ['PARAMETER_REPLACEMENT_TARGET_INVALID', 'replace_parameter_field'],
  ['IMPACT_REVIEW_INCOMPLETE', 'review_dependency_impact'],
  ['IMPACT_REVIEW_INVALID', 'review_dependency_impact'],
]);
const sleep = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const sha256 = (content: string) => createHash('sha256').update(content).digest('hex');
const DEBUG_TOOL_NAMES: Record<string, string> = {
  write_initial_model: 'initialize',
  replace_parameter_field: 'replace_param',
  update_cad_part_metadata: 'update_metadata',
  replace_cad_feature_body: 'replace_feature',
  replace_function_body: 'replace_function',
  add_model_parameter: 'add_param',
  add_private_helper: 'add_helper',
  add_cad_feature: 'add_feature',
  replace_build_model_body: 'replace_build',
  delete_model_parameter: 'delete_param',
  delete_private_helper: 'delete_helper',
  delete_cad_feature: 'delete_feature',
  confirm_no_change: 'no_change',
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function validationDiagnosticCodes(
  report: Record<string, unknown>,
): string[] {
  const codes = new Set<string>();
  const visit = (value: unknown, depth: number) => {
    if (depth > 8 || value === null || value === undefined) return;
    if (Array.isArray(value)) {
      for (const item of value.slice(0, 64)) visit(item, depth + 1);
      return;
    }
    if (typeof value !== 'object') return;
    const item = value as Record<string, unknown>;
    for (const key of ['code', 'error_code']) {
      const code = item[key];
      if (typeof code === 'string' && code.trim()) codes.add(code.trim());
    }
    for (const child of Object.values(item)) visit(child, depth + 1);
  };
  visit(report, 0);
  return [...codes].slice(0, 32);
}

export function planOperationDebug(plan: ToolPlan): {
  message: string;
  operations: Array<{ index: number; tool: string; target: string | null }>;
} {
  const operations = plan.operations.map((operation, offset) => {
    const value = operation as unknown as Record<string, unknown>;
    const firstEvidence = Array.isArray(value.evidence)
      ? record(value.evidence[0])
      : {};
    const rawTarget = String(
      value.semantic_id ??
        value.name ??
        value.function_name ??
        value.target_id ??
        firstEvidence.semantic_id ??
        '',
    );
    const target = rawTarget
      ? rawTarget.includes(':')
        ? rawTarget.split(':').at(-1) || rawTarget
        : rawTarget
      : null;
    return { index: offset + 1, tool: operation.tool, target };
  });
  const compact = operations.map(({ index, tool, target }) => {
    const label = DEBUG_TOOL_NAMES[tool] ?? tool;
    const boundedTarget =
      target && target.length > 16 ? `${target.slice(0, 15)}…` : target;
    return `${index}:${label}${boundedTarget ? `[${boundedTarget}]` : ''}`;
  });
  return {
    message: `Operations (${operations.length}): ${compact.join(', ')}`,
    operations,
  };
}

export function planImpactDebug(
  plan: ToolPlan,
): Array<{ semantic_id: string; decision: string; reason: string }> {
  if (plan.schema_version !== 2) return [];
  return plan.impact_review.map((review) => ({
    semantic_id: review.semantic_id,
    decision: review.decision,
    reason: review.reason.slice(0, 200),
  }));
}

@Injectable()
export class OrchestratorService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(OrchestratorService.name);
  private readonly workerId = process.env.CAD_AGENT_WORKER_ID?.trim() || `cad-agent-${randomUUID()}`;
  private readonly pollMilliseconds = Number(process.env.CAD_AGENT_POLL_INTERVAL_MS ?? 2_000);
  private readonly dependencyPollMilliseconds = Number(
    process.env.CAD_AGENT_DEPENDENCY_POLL_INTERVAL_MS ?? 500,
  );
  private readonly dependencyTimeoutMilliseconds =
    Number(process.env.CAD_AGENT_DEPENDENCY_TIMEOUT_SECONDS ?? 300) * 1_000;
  private readonly leaseSeconds = Number(process.env.CAD_AGENT_LEASE_SECONDS ?? 300);
  private stopping = false;
  private loopPromise?: Promise<void>;

  constructor(
    @Inject(CadAgentRepository) private readonly repository: CadAgentRepository,
    @Inject(ProgressService) private readonly progress: ProgressService,
    @Inject(ReasonerService) private readonly reasoner: ReasonerService,
  ) {}

  onModuleInit() {
    if (process.env.CAD_AGENT_DISABLE_WORKER === 'true') return;
    this.loopPromise = this.runLoop();
  }

  async onModuleDestroy() {
    this.stopping = true;
    await this.loopPromise;
  }

  private async runLoop() {
    this.logger.log(`CAD agent worker_id=${this.workerId}`);
    while (!this.stopping) {
      try {
        const job = await this.repository.claimNextEditJob(this.workerId, this.leaseSeconds);
        if (!job) {
          await sleep(this.pollMilliseconds);
          continue;
        }
        await this.runJob(job);
      } catch (error) {
        this.logger.error(error instanceof Error ? error.stack : String(error));
        await sleep(this.pollMilliseconds);
      }
    }
  }

  private async heartbeat(jobId: string) {
    await this.repository.heartbeatEditJob(jobId, this.workerId, this.leaseSeconds);
  }

  private async withHeartbeat<T>(jobId: string, task: () => Promise<T>): Promise<T> {
    let heartbeatError: unknown;
    const interval = setInterval(() => {
      void this.heartbeat(jobId).catch((error) => {
        heartbeatError = error;
      });
    }, Math.max(1_000, Math.floor((this.leaseSeconds * 1_000) / 3)));
    try {
      const result = await task();
      if (heartbeatError) throw heartbeatError;
      await this.heartbeat(jobId);
      return result;
    } finally {
      clearInterval(interval);
    }
  }

  private async transition(
    jobId: string,
    state: string,
    eventType: string,
    message: string,
    metadata: Record<string, unknown> = {},
    values: Record<string, unknown> = {},
  ): Promise<EditJob> {
    const job = await this.repository.patchEditJob(
      jobId,
      { state, ...values },
      this.workerId,
    );
    await this.progress.emit(jobId, eventType, state, message, metadata);
    await this.heartbeat(jobId);
    return job;
  }

  private async waitFor(
    jobId: string,
    label: string,
    loader: () => Promise<Record<string, unknown>>,
  ): Promise<Record<string, unknown>> {
    const deadline = Date.now() + this.dependencyTimeoutMilliseconds;
    while (Date.now() < deadline) {
      const child = await loader();
      if (TERMINAL.has(String(child.status))) return child;
      await this.heartbeat(jobId);
      await sleep(this.dependencyPollMilliseconds);
    }
    throw new WorkflowError('DEPENDENCY_TIMEOUT', `${label} did not finish before its timeout.`);
  }

  private async ensureIndex(job: EditJob): Promise<void> {
    await this.transition(
      job.id,
      'ensuring_index',
      'indexing.started',
      'Ensuring a fresh project index.',
    );
    const indexId = await this.repository.queueIndex(job.id, 'ensuring_index');
    const indexJob = await this.waitFor(job.id, 'Index build', () => this.repository.indexJob(indexId));
    if (indexJob.status !== 'completed') {
      throw new WorkflowError(
        'INDEX_BUILD_FAILED',
        String(indexJob.error_message ?? 'The project index could not be built.'),
        { index_job_id: indexId },
      );
    }
    await this.progress.emit(job.id, 'indexing.completed', 'ensuring_index', 'Project index is ready.', {
      index_job_id: indexId,
    });
  }

  private async waitForTool(
    job: EditJob,
    child: Record<string, unknown>,
    label: string,
  ): Promise<Record<string, unknown>> {
    const finished = TERMINAL.has(String(child.status))
      ? child
      : await this.waitFor(job.id, label, () => this.repository.toolJob(String(child.id)));
    if (finished.status !== 'completed') {
      throw new WorkflowError(
        String(finished.error_code ?? 'CAD_TOOL_FAILED'),
        String(finished.error_message ?? `${label} failed.`),
        { tool_job_id: finished.id },
      );
    }
    return record(finished.result);
  }

  private async prepareContext(job: EditJob): Promise<Record<string, unknown>> {
    await this.transition(
      job.id,
      'retrieving_context',
      'context.started',
      'Preparing bounded CAD source context.',
    );
    const input = {
      request_text: job.request_text,
      messages: job.messages,
      requested_part_id: job.requested_part_id,
      workflow_mode: job.workflow_mode,
    };
    const child = await this.repository.queueToolJob({
      editJobId: job.id,
      projectId: job.project_id,
      partId: job.requested_part_id,
      attempt: 0,
      kind: 'prepare_context',
      payload: input,
    });
    const context = await this.waitForTool(job, child, 'CAD context preparation');
    const partId = String(context.part_id ?? '');
    const partName = String(context.part_name ?? '');
    const baseHash = String(context.base_source_sha256 ?? '');
    const storagePath = String(context.storage_path ?? '');
    if (!partId || !partName || !/^[0-9a-f]{64}$/.test(baseHash) || !storagePath) {
      throw new WorkflowError('INVALID_TOOL_RESULT', 'CAD context preparation returned incomplete scope.');
    }
    if (job.requested_part_id && partId !== job.requested_part_id) {
      throw new WorkflowError('OUT_OF_SCOPE_TOOL_RESULT', 'CAD context changed the linked part target.');
    }
    const semanticIds = Array.isArray(context.semantic_ids) ? context.semantic_ids : [];
    const resolvedTargets = [
      {
        part_id: partId,
        part_name: partName,
        semantic_ids: semanticIds,
        confidence: job.requested_part_id ? 1 : Number(context.confidence ?? 0),
        reason: String(context.reason ?? 'CAD tool context resolved the target.'),
        candidates: Array.isArray(context.candidates) ? context.candidates : [],
      },
    ];
    await this.repository.patchEditJob(job.id, {
      resolved_part_id: partId,
      resolved_targets: resolvedTargets,
      accepted_source_sha256: baseHash,
      original_storage_path: this.repository.originalPath(job.project_id, partId, job.id),
    }, this.workerId);
    await this.progress.emit(
      job.id,
      'context.completed',
      'retrieving_context',
      `Prepared source context for ${partName}.`,
      { part_id: partId, tool_job_id: child.id },
    );
    return context;
  }

  private async backupAcceptedSource(job: EditJob, context: Record<string, unknown>): Promise<void> {
    const storagePath = String(context.storage_path);
    const expectedHash = String(context.base_source_sha256);
    const originalPath = this.repository.originalPath(
      job.project_id,
      String(context.part_id),
      job.id,
    );
    const canonical = await this.repository.readText(storagePath);
    if (sha256(canonical) !== expectedHash) {
      throw new WorkflowError('SOURCE_CHANGED', 'Accepted source changed while context was prepared.');
    }
    try {
      const persisted = await this.repository.readText(originalPath);
      if (sha256(persisted) !== expectedHash) {
        throw new WorkflowError('ORIGINAL_BACKUP_MISMATCH', 'Persisted original source has the wrong hash.');
      }
    } catch (error) {
      if (error instanceof WorkflowError && error.code !== 'SOURCE_MISSING') throw error;
      await this.repository.writeText(originalPath, canonical);
      const verification = await this.repository.readText(originalPath);
      if (sha256(verification) !== expectedHash) {
        throw new WorkflowError('ORIGINAL_BACKUP_MISMATCH', 'Original source backup could not be verified.');
      }
    }
  }

  private async planAndApply(
    job: EditJob,
    context: Record<string, unknown>,
    attempt: number,
    validation?: Record<string, unknown>,
  ): Promise<{
    plan: ToolPlan;
    result: Record<string, unknown>;
    toolJobId: string;
  }> {
    let existing = await this.repository.toolJobFor(job.id, attempt, 'apply_plan');
    let plan: ToolPlan;
    const planningFeedback = record(context.planning_feedback);
    const isRepair = Boolean(validation || Object.keys(planningFeedback).length);
    const planState = isRepair
      ? 'planning_repair'
      : job.workflow_mode === 'initial_design'
        ? 'planning_initial_design'
        : 'planning_edit';
    const applyState = job.workflow_mode === 'initial_design'
      ? 'applying_initial_design'
      : isRepair
        ? 'applying_repair'
        : 'applying_edit';
    if (existing) {
      plan = ToolPlanSchema.parse(record(existing.input).plan);
    } else {
      await this.transition(
        job.id,
        planState,
        isRepair ? 'repair.started' : 'planning.started',
        validation
          ? `Planning validation repair ${attempt}.`
          : Object.keys(planningFeedback).length
            ? `Replanning CAD tools after preflight failure ${attempt}.`
            : 'Planning bounded CAD changes.',
        {
          attempt,
          max_attempts: job.max_attempts,
          repair_source: validation
            ? 'validation'
            : Object.keys(planningFeedback).length
              ? 'tool_preflight'
              : undefined,
          previous_tool_job_id:
            context.previous_tool_job_id ?? planningFeedback.failed_tool_job_id,
          candidate_sha256: context.candidate_sha256,
          diagnostic_codes:
            context.diagnostic_codes ??
            (validation ? validationDiagnosticCodes(validation) : []),
        },
      );
      plan = await this.withHeartbeat(job.id, () =>
        this.reasoner.createPlan({ job, context, attempt, validation }),
      );
      const planDebug = planOperationDebug(plan);
      const impactDebug = planImpactDebug(plan);
      await this.progress.emit(
        job.id,
        isRepair ? 'repair.completed' : 'planning.completed',
        planState,
        `${planDebug.message}. ${plan.summary}`,
        {
          attempt,
          operations: planDebug.operations,
          impact_review: impactDebug,
        },
      );
      existing = await this.repository.queueToolJob({
        editJobId: job.id,
        projectId: job.project_id,
        partId: plan.target_part_id,
        attempt,
        kind: 'apply_plan',
        payload: {
          schema_version: 1,
          workflow_mode: job.workflow_mode,
          base_storage_path: context.storage_path,
          plan,
        },
      });
    }

    const planDebug = planOperationDebug(plan);
    const impactDebug = planImpactDebug(plan);
    await this.transition(
      job.id,
      applyState,
      'tools.started',
      `Applying CAD tool plan attempt ${attempt}. ${planDebug.message}`,
      {
        attempt,
        tool_job_id: existing.id,
        operations: planDebug.operations,
        impact_review: impactDebug,
      },
    );
    const result = await this.waitForTool(job, existing, 'CAD tool execution');
    const confirmedNoChange =
      result.outcome === 'no_change' &&
      plan.operations.length === 1 &&
      plan.operations[0]?.tool === 'confirm_no_change' &&
      result.base_source_sha256 === plan.base_source_sha256 &&
      Array.isArray(result.evidence) &&
      result.evidence.length > 0;
    const validCandidate =
      /^[0-9a-f]{64}$/.test(String(result.candidate_sha256 ?? '')) &&
      String(result.candidate_path ?? '').endsWith('/model.py');
    if (
      (result.outcome === 'no_change' && !confirmedNoChange) ||
      (result.outcome !== 'no_change' && !validCandidate)
    ) {
      throw new WorkflowError('INVALID_TOOL_RESULT', 'CAD tool execution returned no valid candidate proof.');
    }
    await this.progress.emit(
      job.id,
      'tools.completed',
      applyState,
      confirmedNoChange
        ? 'Confirmed that the accepted CAD source already satisfies the request.'
        : `Applied ${plan.operations.length} CAD operation${plan.operations.length === 1 ? '' : 's'}.`,
      {
        attempt,
        tool_job_id: existing.id,
        outcome: result.outcome ?? 'changed',
        changed_symbols: result.changed_symbols,
        normalization_notes: result.normalization_notes ?? [],
      },
    );
    return { plan, result, toolJobId: String(existing.id) };
  }

  private async repairContext(
    job: EditJob,
    previous: Record<string, unknown>,
    previousPlan: ToolPlan,
    previousToolJobId: string,
    validation: Record<string, unknown>,
    nextAttempt: number,
  ): Promise<Record<string, unknown>> {
    const diagnosticCodes = validationDiagnosticCodes(validation);
    await this.transition(
      job.id,
      'retrieving_repair_context',
      'context.started',
      'Preparing the latest failed candidate for repair.',
      {
        attempt: nextAttempt,
        repair_source: 'validation',
        previous_tool_job_id: previousToolJobId,
        candidate_sha256: previous.candidate_sha256,
        diagnostic_codes: diagnosticCodes,
      },
    );
    const child = await this.repository.queueToolJob({
      editJobId: job.id,
      projectId: job.project_id,
      partId: String(job.resolved_part_id),
      attempt: nextAttempt,
      kind: 'prepare_repair_context',
      payload: {
        candidate_path: previous.candidate_path,
        candidate_sha256: previous.candidate_sha256,
        previous_plan: previousPlan,
        repair_source: 'validation',
        validation,
      },
    });
    const context = await this.waitForTool(job, child, 'CAD repair context preparation');
    await this.progress.emit(job.id, 'context.completed', 'retrieving_repair_context', 'Repair context is ready.', {
      attempt: nextAttempt,
      tool_job_id: child.id,
      repair_source: 'validation',
      previous_tool_job_id: previousToolJobId,
      candidate_sha256: previous.candidate_sha256,
      diagnostic_codes: diagnosticCodes,
    });
    return {
      ...context,
      repair_source: 'validation',
      previous_tool_job_id: previousToolJobId,
      candidate_sha256: previous.candidate_sha256,
      diagnostic_codes: diagnosticCodes,
    };
  }

  private async validateCandidate(
    job: EditJob,
    candidate: Record<string, unknown>,
    attempt: number,
  ): Promise<{ child: Record<string, unknown>; report: Record<string, unknown> }> {
    await this.transition(
      job.id,
      'validating_candidate',
      'validation.started',
      `Validating candidate attempt ${attempt}.`,
      { attempt },
    );
    const validationId = await this.repository.queueValidation({
      editJobId: job.id,
      candidatePath: String(candidate.candidate_path),
      candidateHash: String(candidate.candidate_sha256),
      attempt,
    });
    const child = await this.waitFor(job.id, 'CAD validation', () =>
      this.repository.generationJob(validationId),
    );
    const report = record(child.result);
    const passed =
      child.status === 'completed' &&
      report.status === 'passed' &&
      report.valid === true &&
      child.source_storage_path === candidate.candidate_path &&
      child.source_sha256 === candidate.candidate_sha256 &&
      child.edit_job_id === job.id;
    await this.progress.emit(
      job.id,
      passed ? 'validation.passed' : 'validation.failed',
      'validating_candidate',
      passed ? `Candidate attempt ${attempt} passed validation.` : `Candidate attempt ${attempt} failed validation.`,
      {
        attempt,
        validation_job_id: validationId,
        candidate_sha256: candidate.candidate_sha256,
        diagnostic_codes: passed ? [] : validationDiagnosticCodes(report),
      },
    );
    return { child, report };
  }

  private async commit(
    job: EditJob,
    plan: ToolPlan,
    candidate: Record<string, unknown>,
    validation: { child: Record<string, unknown>; report: Record<string, unknown> },
  ): Promise<Record<string, unknown>> {
    const resumeState = job.state;
    const partId = String(job.resolved_part_id);
    const canonicalPath = this.repository.canonicalPath(job.project_id, partId);
    const candidatePath = String(candidate.candidate_path);
    const candidateHash = String(candidate.candidate_sha256);
    if (
      validation.child.status !== 'completed' ||
      validation.report.status !== 'passed' ||
      validation.report.valid !== true ||
      validation.child.source_storage_path !== candidatePath ||
      validation.child.source_sha256 !== candidateHash ||
      validation.child.edit_job_id !== job.id
    ) {
      throw new WorkflowError('VALIDATION_PROOF_MISMATCH', 'Validation does not prove the current candidate.');
    }

    await this.transition(job.id, 'committing', 'commit.started', 'Committing the validated candidate.');
    const canonical = await this.repository.readText(canonicalPath);
    const canonicalHash = sha256(canonical);
    if (canonicalHash === job.accepted_source_sha256) {
      const candidateContent = await this.repository.readText(candidatePath);
      if (sha256(candidateContent) !== candidateHash) {
        throw new WorkflowError('CANDIDATE_HASH_MISMATCH', 'Candidate content changed after validation.');
      }
      await this.repository.writeText(canonicalPath, candidateContent);
      if (sha256(await this.repository.readText(canonicalPath)) !== candidateHash) {
        throw new WorkflowError('STORAGE_WRITE_MISMATCH', 'Committed source could not be hash-verified.');
      }
    } else if (canonicalHash !== candidateHash) {
      throw new WorkflowError('SOURCE_CHANGED', 'Accepted source changed before commit.');
    }
    await this.progress.emit(job.id, 'commit.completed', 'committing', 'Validated CAD source committed.');

    let indexId: string;
    let indexJob: Record<string, unknown>;
    if (
      ['reindexing', 'queueing_export'].includes(resumeState) &&
      job.index_job_id
    ) {
      indexId = job.index_job_id;
      indexJob = await this.repository.indexJob(indexId);
      if (!TERMINAL.has(String(indexJob.status))) {
        indexJob = await this.waitFor(job.id, 'Post-commit index build', () =>
          this.repository.indexJob(indexId),
        );
      }
    } else {
      await this.transition(job.id, 'reindexing', 'reindex.started', 'Reindexing committed CAD source.');
      indexId = await this.repository.queueIndex(job.id, 'reindexing');
      indexJob = await this.waitFor(job.id, 'Post-commit index build', () =>
        this.repository.indexJob(indexId),
      );
    }
    if (indexJob.status !== 'completed') {
      await this.rollback(job, candidateHash);
      throw new WorkflowError('REINDEX_FAILED', String(indexJob.error_message ?? 'Post-commit reindex failed.'), {
        index_job_id: indexId,
      });
    }
    await this.progress.emit(job.id, 'reindex.completed', 'reindexing', 'Committed source indexed.', {
      index_job_id: indexId,
    });

    const warnings: string[] = [];
    let exportId: string | null = job.export_job_id;
    try {
      if (!exportId) {
        await this.repository.patchEditJob(
          job.id,
          { state: 'queueing_export' },
          this.workerId,
        );
        exportId = await this.repository.queueExport(job.id, candidateHash);
        await this.progress.emit(job.id, 'export.queued', 'queueing_export', 'CAD export queued.', {
          export_job_id: exportId,
        });
      }
    } catch (error) {
      const warning = `CAD export could not be queued; run /export manually: ${
        error instanceof Error ? error.message : String(error)
      }`;
      warnings.push(warning);
      await this.progress.emit(job.id, 'export.warning', 'queueing_export', warning, { warning });
    }

    return {
      schema_version: 1,
      status: 'completed',
      message: plan.summary,
      attempts: Number((await this.repository.editJob(job.id)).attempt_count),
      resolved_target: {
        part_id: partId,
        semantic_ids: Array.isArray(candidate.semantic_ids) ? candidate.semantic_ids : [],
      },
      changed_files: [canonicalPath],
      changed_symbols: candidate.changed_symbols ?? [],
      source_sha256: candidateHash,
      validation_result: validation.report,
      index_job_id: indexId,
      export_job_id: exportId,
      warnings,
    };
  }

  private async rollback(job: EditJob, candidateHash: string): Promise<boolean> {
    const partId = String(job.resolved_part_id);
    const canonicalPath = this.repository.canonicalPath(job.project_id, partId);
    const canonical = await this.repository.readText(canonicalPath);
    if (sha256(canonical) !== candidateHash) return false;
    const originalPath = this.repository.originalPath(job.project_id, partId, job.id);
    const original = await this.repository.readText(originalPath);
    if (sha256(original) !== job.accepted_source_sha256) {
      throw new WorkflowError('ROLLBACK_SOURCE_MISMATCH', 'Original source backup no longer matches.');
    }
    await this.repository.writeText(canonicalPath, original);
    return true;
  }

  private cleanupPaths(job: EditJob): string[] {
    if (!job.resolved_part_id) return [];
    const prefix = `${job.project_id}/candidates/cad/${job.resolved_part_id}/${job.id}`;
    return [
      `${prefix}/original/model.py`,
      ...Array.from({ length: job.max_attempts }, (_, index) => `${prefix}/attempt-${index + 1}/model.py`),
    ];
  }

  private async process(job: EditJob): Promise<Record<string, unknown>> {
    await this.progress.emit(job.id, 'job.started', job.state, 'CAD edit processing started.', {
      workflow_mode: job.workflow_mode,
      part_id: job.requested_part_id,
    });
    if (
      ['committing', 'reindexing', 'queueing_export'].includes(job.state) &&
      job.validation_job_id &&
      job.current_candidate_path &&
      job.current_candidate_sha256 &&
      job.attempt_count > 0
    ) {
      const toolJob = await this.repository.toolJobFor(
        job.id,
        job.attempt_count,
        'apply_plan',
      );
      if (!toolJob || toolJob.status !== 'completed') {
        throw new WorkflowError(
          'EDIT_STATE_CORRUPT',
          'Post-commit resume is missing its completed CAD tool job.',
        );
      }
      const plan = ToolPlanSchema.parse(record(toolJob.input).plan);
      const candidate = record(toolJob.result);
      const validationChild = await this.repository.generationJob(job.validation_job_id);
      return this.commit(job, plan, candidate, {
        child: validationChild,
        report: record(validationChild.result),
      });
    }
    await this.ensureIndex(job);
    job = await this.repository.editJob(job.id);
    const initialContext = await this.prepareContext(job);
    job = await this.repository.editJob(job.id);
    await this.backupAcceptedSource(job, initialContext);

    let context = initialContext;
    let latestCandidate: Record<string, unknown> | undefined;
    let latestPlan: ToolPlan | undefined;
    const firstAttempt = job.validation_job_id
      ? Math.max(1, job.attempt_count)
      : Math.max(1, job.attempt_count + 1);
    for (let attempt = firstAttempt; attempt <= job.max_attempts; attempt += 1) {
      let applied: {
        plan: ToolPlan;
        result: Record<string, unknown>;
        toolJobId: string;
      };
      try {
        const validationFeedback = context.validation
          ? record(context.validation)
          : undefined;
        applied = await this.planAndApply(job, context, attempt, validationFeedback);
      } catch (error) {
        if (!(error instanceof WorkflowError) || !REPAIRABLE_TOOL_PREFLIGHT.has(error.code)) {
          throw error;
        }
        job = await this.repository.patchEditJob(
          job.id,
          { attempt_count: Math.max(job.attempt_count, attempt) },
          this.workerId,
        );
        if (attempt >= job.max_attempts) {
          throw new WorkflowError(
            'MAX_REPAIR_ATTEMPTS',
            'The CAD edit reached its three-attempt limit while repairing tool selection.',
            { last_error_code: error.code, last_error_message: error.message },
          );
        }
        const toolJobId = String(error.details.tool_job_id ?? '');
        const failedTool = toolJobId ? await this.repository.toolJob(toolJobId) : null;
        const failedPlan = failedTool
          ? record(record(failedTool.input).plan)
          : {};
        context = {
          ...initialContext,
          planning_feedback: {
            source: 'tool_preflight',
            error_code: error.code,
            message: error.message,
            suggested_operation: REPAIRABLE_TOOL_PREFLIGHT.get(error.code),
            details: error.details,
            failed_plan: failedPlan,
            failed_tool_job_id: toolJobId || null,
          },
        };
        continue;
      }
      latestPlan = applied.plan;
      latestCandidate = applied.result;
      if (latestCandidate.outcome === 'no_change') {
        job = await this.repository.patchEditJob(
          job.id,
          { attempt_count: Math.max(job.attempt_count, attempt) },
          this.workerId,
        );
        return {
          schema_version: 1,
          status: 'completed',
          outcome: 'no_change',
          message: latestPlan.summary,
          attempts: job.attempt_count,
          resolved_target: {
            part_id: String(job.resolved_part_id),
            semantic_ids: Array.isArray(latestCandidate.semantic_ids)
              ? latestCandidate.semantic_ids
              : [],
          },
          changed_files: [],
          changed_symbols: [],
          source_sha256: latestCandidate.base_source_sha256,
          validation_result: null,
          index_job_id: null,
          export_job_id: null,
          evidence: latestCandidate.evidence,
          warnings: [],
        };
      }
      const validation = await this.validateCandidate(job, latestCandidate, attempt);
      job = await this.repository.editJob(job.id);
      if (
        validation.child.status === 'completed' &&
        validation.report.status === 'passed' &&
        validation.report.valid === true
      ) {
        return this.commit(job, latestPlan, latestCandidate, validation);
      }
      if (validation.report.repairable_hint !== true) {
        throw new WorkflowError(
          'VALIDATION_NOT_REPAIRABLE',
          String(validation.child.error_message ?? 'Candidate validation cannot be repaired.'),
          { validation_result: validation.report },
        );
      }
      if (attempt >= job.max_attempts) {
        throw new WorkflowError('MAX_REPAIR_ATTEMPTS', 'The CAD edit reached its three-attempt limit.', {
          validation_result: validation.report,
        });
      }
      context = await this.repairContext(
        job,
        latestCandidate,
        latestPlan,
        applied.toolJobId,
        validation.report,
        attempt + 1,
      );
      context.validation = validation.report;
    }
    throw new WorkflowError('MAX_REPAIR_ATTEMPTS', 'The CAD edit reached its validation limit.');
  }

  private async fail(job: EditJob, error: unknown): Promise<Record<string, unknown>> {
    const failure =
      error instanceof WorkflowError
        ? error
        : new WorkflowError('WORKFLOW_INTERNAL_ERROR', 'The CAD agent failed unexpectedly.');
    const latest = await this.repository.editJob(job.id);
    let sourceRestored = false;
    let recoveryError: Record<string, unknown> | null = null;
    if (
      latest.resolved_part_id &&
      latest.current_candidate_sha256 &&
      ['committing', 'reindexing'].includes(latest.state)
    ) {
      try {
        sourceRestored = await this.rollback(latest, latest.current_candidate_sha256);
      } catch (rollbackError) {
        recoveryError = {
          code: rollbackError instanceof WorkflowError ? rollbackError.code : 'ROLLBACK_FAILED',
          message: rollbackError instanceof Error ? rollbackError.message : String(rollbackError),
        };
      }
    }
    const result = {
      schema_version: 1,
      status: 'failed',
      message: failure.message,
      error_code: failure.code,
      state: latest.state,
      attempts: latest.attempt_count,
      resolved_targets: latest.resolved_targets,
      source_restored: sourceRestored,
      details: failure.details,
      recovery_error: recoveryError,
      warnings: [] as string[],
    };
    await this.repository.patchEditJob(job.id, {
      status: 'failed',
      state: 'failed',
      result,
      error_code: failure.code,
      error_message: failure.message.slice(0, 4_000),
      lease_expires_at: null,
      completed_at: new Date().toISOString(),
    }, this.workerId);
    await this.progress.emit(job.id, 'job.failed', 'failed', failure.message, {
      error_code: failure.code,
    });
    return result;
  }

  async runJob(job: EditJob): Promise<Record<string, unknown>> {
    try {
      const result = await this.process(job);
      await this.repository.patchEditJob(job.id, {
        status: 'completed',
        state: 'completed',
        result,
        error_code: null,
        error_message: null,
        lease_expires_at: null,
        completed_at: new Date().toISOString(),
      }, this.workerId);
      await this.progress.emit(job.id, 'job.completed', 'completed', String(result.message), {
        part_id: job.resolved_part_id,
        changed_symbols: result.changed_symbols,
        index_job_id: result.index_job_id,
        export_job_id: result.export_job_id,
      });
      const latest = await this.repository.editJob(job.id);
      const cleanupWarning = await this.repository.removePaths(this.cleanupPaths(latest));
      if (cleanupWarning) {
        const warnings = Array.isArray(record(latest.result).warnings)
          ? [...(record(latest.result).warnings as unknown[]), cleanupWarning]
          : [cleanupWarning];
        await this.repository.patchEditJob(
          job.id,
          { result: { ...record(latest.result), warnings } },
          this.workerId,
        );
      }
      return result;
    } catch (error) {
      this.logger.error(`cad-agent[${job.id}] ${error instanceof Error ? error.stack : String(error)}`);
      const result = await this.fail(job, error);
      const latest = await this.repository.editJob(job.id);
      if (latest.resolved_part_id) await this.repository.removePaths(this.cleanupPaths(latest));
      return result;
    }
  }
}
