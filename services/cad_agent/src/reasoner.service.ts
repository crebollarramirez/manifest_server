import { Injectable } from '@nestjs/common';
import OpenAI from 'openai';
import { zodTextFormat } from 'openai/helpers/zod';
import {
  EditJob,
  InitialDesignToolPlanV2Schema,
  ToolPlanV2,
  ToolPlanV2Schema,
  WorkflowError,
} from './contracts';
import { loadCadReasoningPrompt } from './prompt-loader';

export function shouldUseRepairPrompt(input: {
  validation?: Record<string, unknown>;
  planningFeedback?: unknown;
}): boolean {
  const feedback = input.planningFeedback;
  const hasPlanningFeedback =
    Boolean(feedback) &&
    typeof feedback === 'object' &&
    !Array.isArray(feedback) &&
    Object.keys(feedback as Record<string, unknown>).length > 0;
  return input.validation !== undefined || hasPlanningFeedback;
}

@Injectable()
export class ReasonerService {
  private readonly client: OpenAI;
  private readonly model: string;
  private readonly initialInstructions: string;
  private readonly initialRepairInstructions: string;
  private readonly editInstructions: string;
  private readonly editRepairInstructions: string;

  constructor() {
    const apiKey = process.env.OPENAI_API_KEY?.trim();
    if (!apiKey) throw new Error('OPENAI_API_KEY is required.');
    this.client = new OpenAI({ apiKey });
    this.model = process.env.OPENAI_MODEL?.trim() || 'gpt-5.4-mini';
    this.initialInstructions = loadCadReasoningPrompt({
      workflowMode: 'initial_design',
    });
    this.initialRepairInstructions = loadCadReasoningPrompt({
      workflowMode: 'initial_design',
      repair: true,
    });
    this.editInstructions = loadCadReasoningPrompt({ workflowMode: 'edit' });
    this.editRepairInstructions = loadCadReasoningPrompt({
      workflowMode: 'edit',
      repair: true,
    });
  }

  private async requestPlan(input: {
    job: EditJob;
    context: Record<string, unknown>;
    attempt: number;
    validation?: Record<string, unknown>;
  }, workflowMode: 'initial_design' | 'edit'): Promise<ToolPlanV2> {
    const isRepair = shouldUseRepairPrompt({
      validation: input.validation,
      planningFeedback: input.context.planning_feedback,
    });
    const responseSchema =
      workflowMode === 'initial_design'
        ? InitialDesignToolPlanV2Schema
        : ToolPlanV2Schema;
    const instructions =
      workflowMode === 'initial_design'
        ? isRepair
          ? this.initialRepairInstructions
          : this.initialInstructions
        : isRepair
          ? this.editRepairInstructions
          : this.editInstructions;
    const response = await this.client.responses.parse({
      model: this.model,
      instructions,
      input: [
        {
          role: 'user',
          content: JSON.stringify({
            workflow_mode: workflowMode,
            original_request: input.job.request_text,
            conversation: input.job.messages,
            attempt: input.attempt,
            source_context: input.context,
            latest_validation: input.validation ?? null,
            planning_feedback: input.context.planning_feedback ?? null,
          }),
        },
      ],
      text: {
        format: zodTextFormat(responseSchema, 'cad_tool_plan'),
      },
    });

    if (!response.output_parsed) {
      const output = response.output as unknown as Array<Record<string, unknown>>;
      const refusal = output
        .flatMap((item) => (Array.isArray(item.content) ? item.content : []))
        .find(
          (item): item is Record<string, unknown> =>
            Boolean(item) &&
            typeof item === 'object' &&
            (item as Record<string, unknown>).type === 'refusal',
        );
      if (refusal) {
        throw new WorkflowError('AI_REFUSAL', String(refusal.refusal ?? 'The model refused the request.'));
      }
      throw new WorkflowError('AI_RESPONSE_INVALID', 'OpenAI returned no parsed CAD tool plan.');
    }

    const plan = responseSchema.parse(response.output_parsed);
    const initialTools = plan.operations.filter((operation) => operation.tool === 'write_initial_model');
    if (workflowMode === 'initial_design') {
      if (plan.operations.length !== 1 || initialTools.length !== 1) {
        throw new WorkflowError(
          'INVALID_TOOL_PLAN',
          'Initial design requires exactly one write_initial_model operation.',
        );
      }
    } else if (initialTools.length) {
      throw new WorkflowError(
        'INVALID_TOOL_PLAN',
        'Established CAD parts cannot use whole-model replacement.',
      );
    }

    const expectedPartId = String(input.context.part_id ?? '');
    const expectedHash = String(input.context.base_source_sha256 ?? '');
    if (!expectedPartId || plan.target_part_id !== expectedPartId) {
      throw new WorkflowError('OUT_OF_SCOPE_TOOL_PLAN', 'The plan targets a part outside the resolved context.');
    }
    if (!expectedHash || plan.base_source_sha256 !== expectedHash) {
      throw new WorkflowError('STALE_TOOL_PLAN', 'The plan source hash does not match its context.');
    }
    return plan;
  }

  async createInitialDesignPlan(input: {
    job: EditJob;
    context: Record<string, unknown>;
    attempt: number;
    validation?: Record<string, unknown>;
  }): Promise<ToolPlanV2> {
    if (input.job.workflow_mode !== 'initial_design') {
      throw new WorkflowError(
        'WORKFLOW_MODE_MISMATCH',
        'Initialization planning requires an initial_design job.',
      );
    }
    return this.requestPlan(input, 'initial_design');
  }

  async createEditPlan(input: {
    job: EditJob;
    context: Record<string, unknown>;
    attempt: number;
    validation?: Record<string, unknown>;
  }): Promise<ToolPlanV2> {
    if (input.job.workflow_mode !== 'edit') {
      throw new WorkflowError(
        'WORKFLOW_MODE_MISMATCH',
        'Established edit planning requires an edit job.',
      );
    }
    return this.requestPlan(input, 'edit');
  }

  async createPlan(input: {
    job: EditJob;
    context: Record<string, unknown>;
    attempt: number;
    validation?: Record<string, unknown>;
  }): Promise<ToolPlanV2> {
    return input.job.workflow_mode === 'initial_design'
      ? this.createInitialDesignPlan(input)
      : this.createEditPlan(input);
  }
}
