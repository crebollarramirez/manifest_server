import { z } from "zod";

/**
 * Zod schemas for every cad-agent response shape the frontend consumes.
 *
 * These are the ONLY source of backend-derived TypeScript types (z.infer).
 * Every transport response is parsed — never cast — so breaking contract
 * drift (missing/retyped fields, unknown enum values) fails loudly at the
 * boundary. Objects are non-strict: additive backend fields are tolerated;
 * removals and retypes are not.
 *
 * Shapes verified against supabase/functions/cad-agent/index.ts and the
 * migrations; deviations from CONTRACT.md are recorded there as Amendments.
 */

const uuid = z.uuid();
// timestamptz via PostgREST, e.g. "2026-07-28T12:34:56.789+00:00".
const timestamp = z.iso.datetime({ offset: true });

// ---------------------------------------------------------------------------
// Enums (Amendment A5: migrations are the single source of truth)
// ---------------------------------------------------------------------------

export const JOB_STATUSES = [
  "queued",
  "running",
  "completed",
  "failed",
  "cancelled",
] as const;
export const jobStatusSchema = z.enum(JOB_STATUSES);
export type JobStatus = z.infer<typeof jobStatusSchema>;

export const PART_TYPES = ["cad", "mesh"] as const;
export const partTypeSchema = z.enum(PART_TYPES);
export type PartType = z.infer<typeof partTypeSchema>;

/** edit_jobs.state — exactly 21 values (20260726000000_add_initial_cad_design.sql). */
export const EDIT_JOB_STATES = [
  "received",
  "ensuring_index",
  "resolving_target",
  "retrieving_context",
  "planning_edit",
  "validating_plan",
  "applying_edit",
  "planning_initial_design",
  "planning_initial_repair",
  "applying_initial_design",
  "validating_candidate",
  "classifying_error",
  "retrieving_repair_context",
  "planning_repair",
  "applying_repair",
  "committing",
  "reindexing",
  "queueing_export",
  "completed",
  "failed",
  "cancelled",
] as const;
export const editJobStateSchema = z.enum(EDIT_JOB_STATES);
export type EditJobState = z.infer<typeof editJobStateSchema>;

/** edit_job_events.event_type (20260727000000_add_cad_agent_progress_and_tools.sql). */
export const EDIT_JOB_EVENT_TYPES = [
  "job.queued",
  "job.started",
  "indexing.started",
  "indexing.completed",
  "context.started",
  "context.completed",
  "planning.started",
  "planning.completed",
  "tools.started",
  "tools.completed",
  "validation.started",
  "validation.passed",
  "validation.failed",
  "repair.started",
  "repair.completed",
  "commit.started",
  "commit.completed",
  "reindex.started",
  "reindex.completed",
  "export.queued",
  "export.warning",
  "job.completed",
  "job.failed",
] as const;
export const editJobEventTypeSchema = z.enum(EDIT_JOB_EVENT_TYPES);
export type EditJobEventType = z.infer<typeof editJobEventTypeSchema>;

export const GENERATION_JOB_TYPES = [
  "validate_cad",
  "export_cad",
  "export_mesh",
] as const;
export const generationJobTypeSchema = z.enum(GENERATION_JOB_TYPES);
export type GenerationJobType = z.infer<typeof generationJobTypeSchema>;

const sha256Hex = z.string().regex(/^[0-9a-f]{64}$/);

// ---------------------------------------------------------------------------
// Records
// ---------------------------------------------------------------------------

export const projectRecordSchema = z.object({
  id: uuid,
  project_name: z.string().min(1),
});
export type ProjectRecord = z.infer<typeof projectRecordSchema>;

export const partRecordSchema = z.object({
  id: uuid,
  project_id: uuid,
  part_name: z.string().min(1),
  part_type: partTypeSchema,
});
export type PartRecord = z.infer<typeof partRecordSchema>;

/** Every non-2xx handler response: { error } (index.ts:1516-1522). */
export const errorEnvelopeSchema = z.object({ error: z.string() });
export type ErrorEnvelope = z.infer<typeof errorEnvelopeSchema>;

// ---------------------------------------------------------------------------
// Action responses
// ---------------------------------------------------------------------------

export const createProjectResponseSchema = z.object({
  message: z.string(),
  status: z.literal("created"),
  project: projectRecordSchema,
});
export type CreateProjectResponse = z.infer<typeof createProjectResponseSchema>;

export const linkProjectResponseSchema = z.object({
  message: z.string(),
  status: z.literal("linked"),
  project: projectRecordSchema,
});
export type LinkProjectResponse = z.infer<typeof linkProjectResponseSchema>;

/** CAD parts additionally report the auto-queued build_index job (index.ts:943-955). */
export const createPartResponseSchema = z.object({
  message: z.string(),
  status: z.literal("created"),
  part: partRecordSchema,
  index_job_id: uuid.nullable().optional(),
  index_status: z.string().optional(),
  index_job_type: z.literal("build_index").optional(),
  warnings: z.array(z.string()).optional(),
});
export type CreatePartResponse = z.infer<typeof createPartResponseSchema>;

export const linkPartResponseSchema = z.object({
  message: z.string(),
  status: z.literal("linked"),
  part: partRecordSchema,
});
export type LinkPartResponse = z.infer<typeof linkPartResponseSchema>;

export const listProjectsResponseSchema = z.object({
  message: z.string(),
  status: z.literal("listed"),
  projects: z.array(projectRecordSchema),
});
export type ListProjectsResponse = z.infer<typeof listProjectsResponseSchema>;

/** Amendment A4: unpaginated; silently capped at PostgREST max_rows = 1000. */
export const listPartsResponseSchema = z.object({
  message: z.string(),
  status: z.literal("listed"),
  project: projectRecordSchema,
  parts: z.array(partRecordSchema),
});
export type ListPartsResponse = z.infer<typeof listPartsResponseSchema>;

export const exportPartResponseSchema = z.object({
  message: z.string(),
  status: z.literal("queued"),
  job_type: z.enum(["export_cad", "export_mesh"]),
  project_id: uuid,
  part_id: uuid,
  job_id: uuid,
});
export type ExportPartResponse = z.infer<typeof exportPartResponseSchema>;

export const validatePartResponseSchema = z.object({
  message: z.string(),
  status: z.literal("validating"),
  job_type: z.literal("validate_cad"),
  project_id: uuid,
  part_id: uuid,
  job_id: uuid,
});
export type ValidatePartResponse = z.infer<typeof validatePartResponseSchema>;

export const indexProjectResponseSchema = z.object({
  message: z.string(),
  status: jobStatusSchema,
  job_type: z.literal("build_index"),
  project_id: uuid,
  job_id: uuid,
});
export type IndexProjectResponse = z.infer<typeof indexProjectResponseSchema>;

export const testIndexResponseSchema = z.object({
  message: z.string(),
  status: jobStatusSchema,
  job_type: z.literal("test_getter"),
  project_id: uuid,
  job_id: uuid,
});
export type TestIndexResponse = z.infer<typeof testIndexResponseSchema>;

export const indexJobSchema = z.object({
  id: uuid,
  project_id: uuid,
  type: z.enum(["build_index", "test_getter"]),
  request_text: z.string().nullable(),
  status: jobStatusSchema,
  result: z.unknown(),
  error_message: z.string().nullable(),
  created_at: timestamp,
  started_at: timestamp.nullable(),
  completed_at: timestamp.nullable(),
});
export type IndexJob = z.infer<typeof indexJobSchema>;

export const getIndexJobResponseSchema = z.object({
  message: z.string(),
  status: jobStatusSchema,
  job: indexJobSchema,
});
export type GetIndexJobResponse = z.infer<typeof getIndexJobResponseSchema>;

/** Sanitized history entries — bounded key allowlist (index.ts:1244-1266). */
export const editJobHistoryEntrySchema = z.object({
  recorded_at: z.string().optional(),
  event: z.string().optional(),
  attempt: z.number().int().optional(),
  part_id: uuid.optional(),
  semantic_ids: z.array(z.string()).optional(),
  confidence: z.number().optional(),
  reason: z.string().optional(),
  candidate_hash: z.string().optional(),
  changed_symbols: z.array(z.string()).optional(),
  validation_job_id: uuid.optional(),
  validation_status: z.string().optional(),
  validation_result: z.unknown().optional(),
});
export type EditJobHistoryEntry = z.infer<typeof editJobHistoryEntrySchema>;

/** get_edit_job job payload — exact select list at index.ts:1231. */
export const editJobSchema = z.object({
  id: uuid,
  project_id: uuid,
  requested_part_id: uuid.nullable(),
  workflow_mode: z.enum(["edit", "initial_design"]),
  resolved_part_id: uuid.nullable(),
  resolved_targets: z.array(z.unknown()),
  status: jobStatusSchema,
  state: editJobStateSchema,
  attempt_count: z.number().int().min(0).max(3),
  max_attempts: z.literal(3),
  validation_job_id: uuid.nullable(),
  index_job_id: uuid.nullable(),
  export_job_id: uuid.nullable(),
  history: z.array(editJobHistoryEntrySchema),
  result: z.unknown(),
  error_code: z.string().nullable(),
  error_message: z.string().nullable(),
  client_request_id: uuid.nullable(),
  last_event_sequence: z.number().int().min(0),
  created_at: timestamp,
  started_at: timestamp.nullable(),
  heartbeat_at: timestamp.nullable(),
  completed_at: timestamp.nullable(),
});
export type EditJob = z.infer<typeof editJobSchema>;

export const editJobEventSchema = z.object({
  id: uuid,
  edit_job_id: uuid,
  sequence: z.number().int().positive(),
  event_type: editJobEventTypeSchema,
  state: editJobStateSchema,
  message: z.string().min(1).max(500),
  metadata: z.record(z.string(), z.unknown()),
  created_at: timestamp,
});
export type EditJobEvent = z.infer<typeof editJobEventSchema>;

export const getEditJobResponseSchema = z.object({
  message: z.string(),
  status: jobStatusSchema,
  job: editJobSchema,
  events: z.array(editJobEventSchema),
});
export type GetEditJobResponse = z.infer<typeof getEditJobResponseSchema>;

// --- chat: discriminated on job_type -------------------------------------

/**
 * Amendment A1: part_id echoes the SENT part id (null only for project-scoped
 * chats). Authoritative identity for project-scoped edits is
 * get_edit_job.job.resolved_part_id — encoded in the actions layer, nowhere else.
 */
export const chatEditCadResponseSchema = z.object({
  message: z.string(),
  status: jobStatusSchema,
  job_type: z.literal("edit_cad"),
  project_id: uuid,
  part_id: uuid.nullable(),
  job_id: uuid,
  client_request_id: uuid,
});
export type ChatEditCadResponse = z.infer<typeof chatEditCadResponseSchema>;

export const chatInitialCadDesignResponseSchema = z.object({
  message: z.string(),
  status: jobStatusSchema,
  job_type: z.literal("initial_cad_design"),
  project_id: uuid,
  part_id: uuid,
  job_id: uuid,
  client_request_id: uuid,
});
export type ChatInitialCadDesignResponse = z.infer<
  typeof chatInitialCadDesignResponseSchema
>;

/** Amendment A2: no client_request_id; mesh chat is not idempotent — never auto-retry. */
export const chatMeshResponseSchema = z.object({
  message: z.string(),
  status: z.literal("queued"),
  job_type: z.literal("export_mesh"),
  project_id: uuid,
  part_id: uuid,
  job_id: uuid,
});
export type ChatMeshResponse = z.infer<typeof chatMeshResponseSchema>;

export const chatResponseSchema = z.discriminatedUnion("job_type", [
  chatEditCadResponseSchema,
  chatInitialCadDesignResponseSchema,
  chatMeshResponseSchema,
]);
export type ChatResponse = z.infer<typeof chatResponseSchema>;

export const deleteProjectResponseSchema = z.object({
  message: z.string(),
  status: z.literal("deleted"),
  project: projectRecordSchema,
});
export type DeleteProjectResponse = z.infer<typeof deleteProjectResponseSchema>;

export const deletePartResponseSchema = z.object({
  message: z.string(),
  status: z.literal("deleted"),
  part: partRecordSchema,
});
export type DeletePartResponse = z.infer<typeof deletePartResponseSchema>;

// --- get_export_job: CONTRACT.md §4 — spec'd, NOT yet live in cad-agent ----

export const exportJobSchema = z.object({
  id: uuid,
  project_id: uuid,
  part_id: uuid,
  type: generationJobTypeSchema,
  status: jobStatusSchema,
  source_sha256: sha256Hex.nullable(),
  error_message: z.string().nullable(),
  result: z.unknown(),
  created_at: timestamp,
});
export type ExportJob = z.infer<typeof exportJobSchema>;

export const ARTIFACT_FILES = ["model.stl", "model.glb", "model.step"] as const;
export const exportArtifactSchema = z.object({
  file: z.enum(ARTIFACT_FILES),
  // Signed URL. In-memory only: never logged, never persisted (security concerns).
  url: z.string().min(1),
});
export type ExportArtifact = z.infer<typeof exportArtifactSchema>;

export const getExportJobResponseSchema = z.object({
  message: z.string().optional(),
  status: jobStatusSchema,
  job: exportJobSchema,
  artifacts: z.array(exportArtifactSchema).optional(),
});
export type GetExportJobResponse = z.infer<typeof getExportJobResponseSchema>;
