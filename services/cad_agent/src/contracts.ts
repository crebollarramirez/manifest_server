import { z } from 'zod';

export const UuidSchema = z.string().uuid();
const NonBlankSchema = z.string().trim().min(1);
const RawRequestSchema = z
  .string()
  .max(20_000)
  .refine(
    (value) => value.trim().length > 0,
    'Request must contain non-whitespace characters.',
  );

export const ConversationMessageSchema = z
  .object({
    role: z.enum(['user', 'assistant']),
    content: RawRequestSchema,
  })
  .strict();

export const CadEditSubmissionSchema = z
  .object({
    project_id: UuidSchema,
    request_text: RawRequestSchema,
    part_id: UuidSchema.optional(),
    client_request_id: UuidSchema.optional(),
    messages: z.array(ConversationMessageSchema).max(8).optional(),
  })
  .strict()
  .superRefine((value, context) => {
    if (!value.messages?.length) return;
    const final = value.messages.at(-1);
    if (final?.role !== 'user' || final.content !== value.request_text) {
      context.addIssue({
        code: 'custom',
        path: ['messages'],
        message: 'Messages must end with the submitted request_text.',
      });
    }
  });

const ActionBase = z.object({ action: z.string() });

export const CadAgentActionSchema = z.discriminatedUnion('action', [
  ActionBase.extend({
    action: z.literal('create_project'),
    project_name: NonBlankSchema,
  }).strict(),
  ActionBase.extend({
    action: z.literal('create_part'),
    project_id: UuidSchema,
    part_name: NonBlankSchema,
    part_type: z.enum(['cad', 'mesh']),
  }).strict(),
  ActionBase.extend({
    action: z.literal('link_project'),
    project_id: UuidSchema,
  }).strict(),
  ActionBase.extend({
    action: z.literal('link_part'),
    project_id: UuidSchema,
    part_id: UuidSchema,
  }).strict(),
  ActionBase.extend({ action: z.literal('list_projects') }).strict(),
  ActionBase.extend({
    action: z.literal('list_parts'),
    project_id: UuidSchema,
  }).strict(),
  ActionBase.extend({
    action: z.literal('export_part'),
    part_id: UuidSchema,
  }).strict(),
  ActionBase.extend({
    action: z.literal('validate_part'),
    part_id: UuidSchema,
  }).strict(),
  ActionBase.extend({
    action: z.literal('index_project'),
    project_id: UuidSchema,
  }).strict(),
  ActionBase.extend({
    action: z.literal('test_index'),
    project_id: UuidSchema,
    request_text: NonBlankSchema.max(20_000),
  }).strict(),
  ActionBase.extend({
    action: z.literal('get_index_job'),
    project_id: UuidSchema,
    job_id: UuidSchema,
  }).strict(),
  ActionBase.extend({
    action: z.literal('get_edit_job'),
    job_id: UuidSchema,
    after_sequence: z.number().int().min(0).optional(),
  }).strict(),
  ActionBase.extend({
    action: z.literal('delete_project'),
    project_id: UuidSchema,
  }).strict(),
  ActionBase.extend({
    action: z.literal('delete_part'),
    project_id: UuidSchema,
    part_id: UuidSchema,
  }).strict(),
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
export type CadAgentAction = z.infer<typeof CadAgentActionSchema>;

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

export type ProjectRecord = {
  id: string;
  project_name: string;
};

export type PartType = 'cad' | 'mesh';

export type PartRecord = {
  id: string;
  project_id: string;
  part_name: string;
  part_type: PartType;
};

export class ActionError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export class WorkflowError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}
