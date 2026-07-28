import { z } from 'zod';

export const UuidSchema = z.string().uuid();
const Sha256Schema = z.string().regex(/^[0-9a-f]{64}$/);
const NonBlankSchema = z.string().trim().min(1);

export const ConversationMessageSchema = z
  .object({
    role: z.enum(['user', 'assistant']),
    content: NonBlankSchema.max(20_000),
  })
  .strict();

export const CadEditSubmissionSchema = z
  .object({
    project_id: UuidSchema,
    request_text: NonBlankSchema.max(20_000),
    part_id: UuidSchema.optional(),
    client_request_id: UuidSchema.optional(),
    messages: z.array(ConversationMessageSchema).max(8).optional(),
  })
  .strict()
  .superRefine((value, context) => {
    if (!value.messages?.length) return;
    const final = value.messages.at(-1);
    if (final?.role !== 'user' || final.content.trim() !== value.request_text.trim()) {
      context.addIssue({
        code: 'custom',
        path: ['messages'],
        message: 'Messages must end with the submitted request_text.',
      });
    }
  });

const TargetedOperationBase = z.object({
  target_id: NonBlankSchema,
  target_fingerprint: Sha256Schema,
});

export const WriteInitialModelSchema = z
  .object({
    tool: z.literal('write_initial_model'),
    model_body: NonBlankSchema,
  })
  .strict();

const ReplaceParameterField = TargetedOperationBase.extend({
  tool: z.literal('replace_parameter_field'),
  replacement_source: NonBlankSchema,
}).strict();

const UpdateCadPartMetadata = TargetedOperationBase.extend({
  tool: z.literal('update_cad_part_metadata'),
  role: NonBlankSchema,
  parameters: z.array(NonBlankSchema),
  depends_on: z.array(NonBlankSchema),
  search_keys: z.array(NonBlankSchema).min(1),
}).strict();

const ReplaceFunctionBody = TargetedOperationBase.extend({
  tool: z.literal('replace_function_body'),
  replacement_source: NonBlankSchema,
}).strict();

const ReplaceCadFeatureBody = z
  .object({
    tool: z.literal('replace_cad_feature_body'),
    semantic_id: NonBlankSchema,
    target_fingerprint: Sha256Schema,
    replacement_source: NonBlankSchema,
  })
  .strict();

const AddModelParameter = z
  .object({
    tool: z.literal('add_model_parameter'),
    name: NonBlankSchema,
    field_source: NonBlankSchema,
  })
  .strict();

const AddPrivateHelper = z
  .object({
    tool: z.literal('add_private_helper'),
    function_name: NonBlankSchema,
    function_source: NonBlankSchema,
  })
  .strict();

const AddCadFeature = z
  .object({
    tool: z.literal('add_cad_feature'),
    semantic_id: NonBlankSchema,
    function_name: NonBlankSchema,
    role: NonBlankSchema,
    parameters: z.array(NonBlankSchema),
    depends_on: z.array(NonBlankSchema),
    search_keys: z.array(NonBlankSchema).min(1),
    function_source: NonBlankSchema,
  })
  .strict();

const ReplaceBuildModelBody = TargetedOperationBase.extend({
  tool: z.literal('replace_build_model_body'),
  replacement_source: NonBlankSchema,
}).strict();

const DeleteModelParameter = TargetedOperationBase.extend({
  tool: z.literal('delete_model_parameter'),
}).strict();

const DeletePrivateHelper = TargetedOperationBase.extend({
  tool: z.literal('delete_private_helper'),
}).strict();

const DeleteCadFeature = TargetedOperationBase.extend({
  tool: z.literal('delete_cad_feature'),
}).strict();

export const ToolOperationSchema = z.discriminatedUnion('tool', [
  WriteInitialModelSchema,
  ReplaceParameterField,
  UpdateCadPartMetadata,
  ReplaceFunctionBody,
  ReplaceCadFeatureBody,
  AddModelParameter,
  AddPrivateHelper,
  AddCadFeature,
  ReplaceBuildModelBody,
  DeleteModelParameter,
  DeletePrivateHelper,
  DeleteCadFeature,
]);

const ToolPlanBase = z
  .object({
    summary: NonBlankSchema.max(500),
    target_part_id: UuidSchema,
    base_source_sha256: Sha256Schema,
    operations: z.array(ToolOperationSchema).min(1).max(12),
  });

export const ImpactReviewSchema = z
  .object({
    semantic_id: NonBlankSchema,
    decision: z.enum(['modified', 'verified_compatible']),
    reason: NonBlankSchema.max(500),
  })
  .strict();

export const ToolPlanV1Schema = ToolPlanBase.extend({
  schema_version: z.literal(1),
}).strict();

export const ToolPlanV2Schema = ToolPlanBase.extend({
  schema_version: z.literal(2),
  impact_review: z.array(ImpactReviewSchema).max(64),
}).strict();

export const InitialDesignToolPlanV2Schema = ToolPlanBase.extend({
  schema_version: z.literal(2),
  operations: z.array(WriteInitialModelSchema).length(1),
  impact_review: z.array(ImpactReviewSchema).max(0),
}).strict();

export const ToolPlanSchema = z.discriminatedUnion('schema_version', [
  ToolPlanV1Schema,
  ToolPlanV2Schema,
]);

export const SubscribeMessageSchema = z
  .object({
    job_id: UuidSchema,
    after_sequence: z.number().int().min(0).default(0),
  })
  .strict();

export const UnsubscribeMessageSchema = z
  .object({ job_id: UuidSchema })
  .strict();

export const AckMessageSchema = z
  .object({
    job_id: UuidSchema,
    sequence: z.number().int().min(0),
  })
  .strict();

export type CadEditSubmission = z.infer<typeof CadEditSubmissionSchema>;
export type ToolPlan = z.infer<typeof ToolPlanSchema>;
export type ToolPlanV2 = z.infer<typeof ToolPlanV2Schema>;
export type ToolOperation = z.infer<typeof ToolOperationSchema>;

export type EditJob = {
  id: string;
  project_id: string;
  requested_part_id: string | null;
  resolved_part_id: string | null;
  request_text: string;
  messages: Array<{ role: string; content: string }>;
  workflow_mode: 'edit' | 'initial_design';
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  state: string;
  attempt_count: number;
  max_attempts: number;
  accepted_source_sha256: string | null;
  original_storage_path: string | null;
  current_candidate_path: string | null;
  current_candidate_sha256: string | null;
  validation_job_id: string | null;
  index_job_id: string | null;
  export_job_id: string | null;
  resolved_targets: unknown[];
  history: unknown[];
  result: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  client_request_id: string | null;
  request_fingerprint: string | null;
  last_event_sequence: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type EditJobEvent = {
  id: string;
  edit_job_id: string;
  sequence: number;
  event_type: string;
  state: string;
  message: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export class WorkflowError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}
