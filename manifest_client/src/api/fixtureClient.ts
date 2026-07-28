import type { CadAgentClient, ChatRequest } from "./client";
import { ApiError } from "./errors";
import {
  chatResponseSchema,
  createPartResponseSchema,
  createProjectResponseSchema,
  deletePartResponseSchema,
  deleteProjectResponseSchema,
  editJobStateSchema,
  exportPartResponseSchema,
  getEditJobResponseSchema,
  getExportJobResponseSchema,
  getIndexJobResponseSchema,
  indexProjectResponseSchema,
  linkPartResponseSchema,
  linkProjectResponseSchema,
  listPartsResponseSchema,
  listProjectsResponseSchema,
  testIndexResponseSchema,
  validatePartResponseSchema,
  type ChatResponse,
  type EditJobEventType,
  type EditJobState,
  type GenerationJobType,
  type JobStatus,
  type PartRecord,
  type PartType,
  type ProjectRecord,
} from "./schemas";
import {
  FIXTURE_BLANK_PART_ID,
  FIXTURE_BLANK_PART_NAME,
  FIXTURE_CAD_PART_ID,
  FIXTURE_CAD_PART_NAME,
  FIXTURE_LARGE_PART_ID,
  FIXTURE_LARGE_PART_NAME,
  FIXTURE_MESH_PART_ID,
  FIXTURE_MESH_PART_NAME,
  FIXTURE_PROJECT_ID,
  FIXTURE_PROJECT_NAME,
} from "./fixtureIds";

/**
 * In-memory CadAgentClient over the generated fixtures.
 *
 * Mimics the verified cad-agent behaviors — including the error statuses,
 * deletion 409 guards, idempotent CAD submission, and the CONTRACT.md §4
 * get_export_job spec — so every later phase is built and tested against the
 * real contract before the live transport exists. Deterministic on purpose:
 * durable jobs advance exactly one step per status poll, never on a timer.
 *
 * Every response passes through the same Zod schemas the live client will
 * use, so a fixture that drifts from the contract fails its own tests.
 */

type FixturePart = PartRecord & { blank: boolean };

type FixtureEditJob = {
  id: string;
  project_id: string;
  requested_part_id: string | null;
  workflow_mode: "edit" | "initial_design";
  resolved_part_id: string | null;
  status: JobStatus;
  state: EditJobState;
  attempt_count: number;
  export_job_id: string | null;
  error_code: string | null;
  error_message: string | null;
  client_request_id: string;
  last_event_sequence: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  walk: EditJobState[];
  walkPosition: number;
  events: Array<{
    id: string;
    edit_job_id: string;
    sequence: number;
    event_type: EditJobEventType;
    state: EditJobState;
    message: string;
    metadata: Record<string, unknown>;
    created_at: string;
  }>;
};

type FixtureExportJob = {
  id: string;
  project_id: string;
  part_id: string;
  type: GenerationJobType;
  status: JobStatus;
  source_sha256: string | null;
  error_message: string | null;
  created_at: string;
  /** Status polls remaining before the job reaches `completed`. */
  pollsUntilDone: number;
};

type FixtureIndexJob = {
  id: string;
  project_id: string;
  type: "build_index" | "test_getter";
  request_text: string | null;
  status: JobStatus;
  result: unknown;
  created_at: string;
  completed_at: string | null;
};

const EDIT_WALK: EditJobState[] = [
  "ensuring_index",
  "resolving_target",
  "retrieving_context",
  "planning_edit",
  "validating_plan",
  "applying_edit",
  "validating_candidate",
  "committing",
  "reindexing",
  "queueing_export",
  "completed",
];

const INITIAL_DESIGN_WALK: EditJobState[] = [
  "ensuring_index",
  "planning_initial_design",
  "applying_initial_design",
  "validating_candidate",
  "committing",
  "reindexing",
  "queueing_export",
  "completed",
];

const STATE_EVENT_TYPES: Partial<Record<EditJobState, EditJobEventType>> = {
  ensuring_index: "indexing.started",
  resolving_target: "context.started",
  retrieving_context: "context.completed",
  planning_edit: "planning.started",
  planning_initial_design: "planning.started",
  validating_plan: "planning.completed",
  applying_edit: "tools.started",
  applying_initial_design: "tools.started",
  validating_candidate: "validation.started",
  committing: "commit.started",
  reindexing: "reindex.started",
  queueing_export: "export.queued",
  completed: "job.completed",
  failed: "job.failed",
};

function nowIso(): string {
  return new Date().toISOString();
}

/** Deterministic 64-hex pseudo-hash derived from a job id. */
function pseudoSha256(seed: string): string {
  const hex = seed.replace(/-/g, "");
  return (hex + hex).slice(0, 64);
}

function artifactUrl(projectId: string, partId: string, file: string): string {
  return `/fixtures/${projectId}/exports/${partId}/${file}`;
}

export class FixtureCadAgentClient implements CadAgentClient {
  private projects = new Map<string, ProjectRecord>();
  private parts = new Map<string, FixturePart>();
  private editJobs = new Map<string, FixtureEditJob>();
  private exportJobs = new Map<string, FixtureExportJob>();
  private indexJobs = new Map<string, FixtureIndexJob>();
  private clientRequestIndex = new Map<string, string>();

  constructor() {
    this.projects.set(FIXTURE_PROJECT_ID, {
      id: FIXTURE_PROJECT_ID,
      project_name: FIXTURE_PROJECT_NAME,
    });
    const seedParts: Array<[string, string, PartType, boolean]> = [
      [FIXTURE_CAD_PART_ID, FIXTURE_CAD_PART_NAME, "cad", false],
      [FIXTURE_MESH_PART_ID, FIXTURE_MESH_PART_NAME, "mesh", false],
      [FIXTURE_LARGE_PART_ID, FIXTURE_LARGE_PART_NAME, "cad", false],
      [FIXTURE_BLANK_PART_ID, FIXTURE_BLANK_PART_NAME, "cad", true],
    ];
    for (const [id, name, type, blank] of seedParts) {
      this.parts.set(id, {
        id,
        project_id: FIXTURE_PROJECT_ID,
        part_name: name,
        part_type: type,
        blank,
      });
      if (!blank) {
        this.createExportJob(
          FIXTURE_PROJECT_ID,
          id,
          type === "cad" ? "export_cad" : "export_mesh",
          0,
        );
      }
    }
  }

  /**
   * Fixture-only stand-in for the optional get_part_artifacts action
   * (CONTRACT.md §4.1): latest terminal export job for a part, if any.
   * Not part of CadAgentClient — do not use outside fixture wiring/tests.
   */
  latestExportJobIdForPart(partId: string): string | null {
    let latest: FixtureExportJob | null = null;
    for (const job of this.exportJobs.values()) {
      if (job.part_id !== partId) continue;
      if (job.type === "validate_cad") continue;
      if (!latest || job.created_at >= latest.created_at) latest = job;
    }
    return latest?.id ?? null;
  }

  // -- projects -------------------------------------------------------------

  async createProject(projectName: string) {
    const trimmed = this.requireName(projectName, "project_name");
    for (const project of this.projects.values()) {
      if (project.project_name.toLowerCase() === trimmed.toLowerCase()) {
        throw new ApiError(
          "conflict",
          `A project named "${trimmed}" already exists.`,
          409,
        );
      }
    }
    const project: ProjectRecord = {
      id: crypto.randomUUID(),
      project_name: trimmed,
    };
    this.projects.set(project.id, project);
    return createProjectResponseSchema.parse({
      message: `Created and linked project "${project.project_name}".`,
      status: "created",
      project,
    });
  }

  async linkProject(projectName: string) {
    const trimmed = this.requireName(projectName, "project_name");
    const project = [...this.projects.values()].find(
      (candidate) =>
        candidate.project_name.toLowerCase() === trimmed.toLowerCase(),
    );
    if (!project) {
      throw new ApiError(
        "not_found",
        `No project was found with the name "${trimmed}".`,
        404,
      );
    }
    return linkProjectResponseSchema.parse({
      message: `Linked project "${project.project_name}".`,
      status: "linked",
      project,
    });
  }

  async listProjects() {
    const projects = [...this.projects.values()].sort((a, b) =>
      a.project_name.localeCompare(b.project_name),
    );
    return listProjectsResponseSchema.parse({
      message:
        projects.length === 0
          ? "No projects found."
          : `Projects:\n${projects.map((p) => `- ${p.project_name}`).join("\n")}`,
      status: "listed",
      projects,
    });
  }

  async deleteProject(projectName: string) {
    const linked = await this.linkProject(projectName);
    const project = linked.project;
    this.guardDeletion(project.id, undefined);
    for (const part of [...this.parts.values()]) {
      if (part.project_id === project.id) this.parts.delete(part.id);
    }
    this.projects.delete(project.id);
    return deleteProjectResponseSchema.parse({
      message: `Deleted project "${project.project_name}" and all of its parts.`,
      status: "deleted",
      project,
    });
  }

  // -- parts ----------------------------------------------------------------

  async createPart(projectId: string, partName: string, partType: PartType) {
    this.requireProject(projectId);
    const trimmed = this.requireName(partName, "part_name");
    for (const part of this.parts.values()) {
      if (
        part.project_id === projectId &&
        part.part_name.toLowerCase() === trimmed.toLowerCase()
      ) {
        throw new ApiError(
          "conflict",
          `A part named "${trimmed}" already exists in the linked project.`,
          409,
        );
      }
    }
    const part: FixturePart = {
      id: crypto.randomUUID(),
      project_id: projectId,
      part_name: trimmed,
      part_type: partType,
      blank: partType === "cad",
    };
    this.parts.set(part.id, part);
    const indexJob =
      partType === "cad" ? this.createIndexJob(projectId, "build_index") : null;
    return createPartResponseSchema.parse({
      message: `Created and linked ${partType} part "${part.part_name}".`,
      status: "created",
      part: this.toPartRecord(part),
      ...(partType === "cad"
        ? {
            index_job_id: indexJob?.id ?? null,
            index_status: indexJob?.status ?? "not_queued",
            index_job_type: "build_index",
            warnings: [],
          }
        : {}),
    });
  }

  async linkPart(projectId: string, partName: string) {
    const part = this.findPartByName(projectId, partName);
    return linkPartResponseSchema.parse({
      message: `Linked ${part.part_type} part "${part.part_name}".`,
      status: "linked",
      part: this.toPartRecord(part),
    });
  }

  async listParts(projectId: string) {
    const project = this.requireProject(projectId);
    const parts = [...this.parts.values()]
      .filter((part) => part.project_id === projectId)
      .sort((a, b) => a.part_name.localeCompare(b.part_name))
      .map((part) => this.toPartRecord(part));
    return listPartsResponseSchema.parse({
      message:
        parts.length === 0
          ? `No parts found in project "${project.project_name}".`
          : `Parts in "${project.project_name}":\n${parts
              .map((p) => `- ${p.part_name} [${p.part_type}] id=${p.id}`)
              .join("\n")}`,
      status: "listed",
      project,
      parts,
    });
  }

  async deletePart(projectId: string, partName: string) {
    const part = this.findPartByName(projectId, partName);
    this.guardDeletion(projectId, part.id);
    this.parts.delete(part.id);
    return deletePartResponseSchema.parse({
      message: `Deleted ${part.part_type} part "${part.part_name}".`,
      status: "deleted",
      part: this.toPartRecord(part),
    });
  }

  // -- generation jobs ------------------------------------------------------

  async exportPart(partId: string) {
    const part = this.requirePartById(partId);
    const jobType = part.part_type === "cad" ? "export_cad" : "export_mesh";
    const job = this.createExportJob(part.project_id, part.id, jobType, 2);
    return exportPartResponseSchema.parse({
      message: `Queued ${jobType} for ${part.part_type} part "${part.part_name}". Job: ${job.id}`,
      status: "queued",
      job_type: jobType,
      project_id: part.project_id,
      part_id: part.id,
      job_id: job.id,
    });
  }

  async validatePart(partId: string) {
    const part = this.requirePartById(partId);
    if (part.part_type !== "cad") {
      throw new ApiError(
        "validation",
        "Only CAD parts support validation.",
        400,
      );
    }
    const job = this.createExportJob(part.project_id, part.id, "validate_cad", 1);
    return validatePartResponseSchema.parse({
      message: `Queued validate_cad for cad part "${part.part_name}". Job: ${job.id}`,
      status: "validating",
      job_type: "validate_cad",
      project_id: part.project_id,
      part_id: part.id,
      job_id: job.id,
    });
  }

  /** CONTRACT.md §4 — not yet live in cad-agent; fixture serves the spec. */
  async getExportJob(jobId: string) {
    const job = this.exportJobs.get(jobId);
    if (!job) {
      throw new ApiError(
        "not_found",
        `No export job was found with id "${jobId}".`,
        404,
      );
    }
    this.advanceExportJob(job);
    const completedExport =
      job.status === "completed" && job.type !== "validate_cad";
    const files =
      job.type === "export_mesh"
        ? ["model.stl", "model.glb"]
        : ["model.stl", "model.step"];
    return getExportJobResponseSchema.parse({
      message: `Export job ${job.id} is ${job.status}.`,
      status: job.status,
      job: {
        id: job.id,
        project_id: job.project_id,
        part_id: job.part_id,
        type: job.type,
        status: job.status,
        source_sha256: job.source_sha256,
        error_message: job.error_message,
        result: null,
        created_at: job.created_at,
      },
      ...(completedExport
        ? {
            artifacts: files.map((file) => ({
              file,
              url: artifactUrl(job.project_id, job.part_id, file),
            })),
          }
        : {}),
    });
  }

  // -- index jobs -----------------------------------------------------------

  async indexProject(projectId: string) {
    const project = this.requireIndexableProject(projectId);
    const job = this.createIndexJob(projectId, "build_index");
    return indexProjectResponseSchema.parse({
      message: `Index job for project "${project.project_name}" is ${job.status}. Job: ${job.id}`,
      status: job.status,
      job_type: "build_index",
      project_id: projectId,
      job_id: job.id,
    });
  }

  async testIndex(projectId: string, requestText: string) {
    const project = this.requireProject(projectId);
    const job = this.createIndexJob(projectId, "test_getter", requestText);
    return testIndexResponseSchema.parse({
      message: `Queued index Getter test for project "${project.project_name}". Job: ${job.id}`,
      status: job.status,
      job_type: "test_getter",
      project_id: projectId,
      job_id: job.id,
    });
  }

  async getIndexJob(projectId: string, jobId: string) {
    const job = this.indexJobs.get(jobId);
    if (!job || job.project_id !== projectId) {
      throw new ApiError(
        "not_found",
        `No index job was found with id "${jobId}".`,
        404,
      );
    }
    if (job.status === "queued") {
      job.status = "completed";
      job.completed_at = nowIso();
      job.result =
        job.type === "test_getter" ? { retrieved_symbols: [] } : null;
    }
    return getIndexJobResponseSchema.parse({
      message: `Index job ${jobId} is ${job.status}.`,
      status: job.status,
      job: {
        id: job.id,
        project_id: job.project_id,
        type: job.type,
        request_text: job.request_text,
        status: job.status,
        result: job.result,
        error_message: null,
        created_at: job.created_at,
        started_at: job.completed_at,
        completed_at: job.completed_at,
      },
    });
  }

  // -- chat + edit jobs -----------------------------------------------------

  async chat(request: ChatRequest): Promise<ChatResponse> {
    const lastMessage = request.messages.at(-1);
    if (!lastMessage || lastMessage.role !== "user" || !lastMessage.content.trim()) {
      throw new ApiError(
        "validation",
        "Messages must end with a non-empty user message.",
        400,
      );
    }
    const project = this.requireProject(request.projectId);
    const part = request.partId
      ? this.requirePartInProject(request.projectId, request.partId)
      : null;

    // Mesh path: synchronous source update + queued export (Amendment A2).
    if (part && part.part_type === "mesh") {
      const job = this.createExportJob(project.id, part.id, "export_mesh", 2);
      return chatResponseSchema.parse({
        message: `Updated mesh part "${part.part_name}" and queued its export.`,
        status: "queued",
        job_type: "export_mesh",
        project_id: project.id,
        part_id: part.id,
        job_id: job.id,
      });
    }

    // CAD paths are idempotent on client_request_id.
    const clientRequestId = request.clientRequestId ?? crypto.randomUUID();
    const existingJobId = this.clientRequestIndex.get(clientRequestId);
    if (existingJobId) {
      const existing = this.editJobs.get(existingJobId);
      if (existing) return this.chatResponseForEditJob(existing, part);
    }

    // Part-scoped guard mirroring edit_jobs_one_active_per_part_idx (A3).
    if (part) {
      for (const job of this.editJobs.values()) {
        if (
          job.resolved_part_id === part.id &&
          (job.status === "queued" || job.status === "running")
        ) {
          throw new ApiError(
            "conflict",
            `Part "${part.part_name}" already has an active CAD edit.`,
            409,
          );
        }
      }
    }

    const initialDesign = part !== null && part.blank;
    const job: FixtureEditJob = {
      id: crypto.randomUUID(),
      project_id: project.id,
      requested_part_id: part?.id ?? null,
      workflow_mode: initialDesign ? "initial_design" : "edit",
      resolved_part_id: part?.id ?? null,
      status: "queued",
      state: "received",
      attempt_count: 0,
      export_job_id: null,
      error_code: null,
      error_message: null,
      client_request_id: clientRequestId,
      last_event_sequence: 0,
      created_at: nowIso(),
      started_at: null,
      completed_at: null,
      walk: initialDesign ? [...INITIAL_DESIGN_WALK] : [...EDIT_WALK],
      walkPosition: -1,
      events: [],
    };
    this.pushEditEvent(job, "job.queued", "received", "Fixture edit job queued.");
    this.editJobs.set(job.id, job);
    this.clientRequestIndex.set(clientRequestId, job.id);
    return this.chatResponseForEditJob(job, part);
  }

  async getEditJob(jobId: string, afterSequence = 0) {
    const job = this.editJobs.get(jobId);
    if (!job) {
      throw new ApiError(
        "not_found",
        `No CAD edit job was found with id "${jobId}".`,
        404,
      );
    }
    this.advanceEditJob(job);
    return getEditJobResponseSchema.parse({
      message: `CAD edit job ${jobId} is ${job.status} (${job.state}).`,
      status: job.status,
      job: {
        id: job.id,
        project_id: job.project_id,
        requested_part_id: job.requested_part_id,
        workflow_mode: job.workflow_mode,
        resolved_part_id: job.resolved_part_id,
        resolved_targets: [],
        status: job.status,
        state: job.state,
        attempt_count: job.attempt_count,
        max_attempts: 3,
        validation_job_id: null,
        index_job_id: null,
        export_job_id: job.export_job_id,
        history: [],
        result: null,
        error_code: job.error_code,
        error_message: job.error_message,
        client_request_id: job.client_request_id,
        last_event_sequence: job.last_event_sequence,
        created_at: job.created_at,
        started_at: job.started_at,
        heartbeat_at: job.started_at,
        completed_at: job.completed_at,
      },
      events: job.events.filter((event) => event.sequence > afterSequence),
    });
  }

  // -- internals ------------------------------------------------------------

  private chatResponseForEditJob(
    job: FixtureEditJob,
    part: FixturePart | null,
  ): ChatResponse {
    const initialDesign = job.workflow_mode === "initial_design";
    const projectName =
      this.projects.get(job.project_id)?.project_name ?? "unknown";
    return chatResponseSchema.parse({
      message: initialDesign
        ? `Queued an initial CAD design for "${part?.part_name ?? "part"}". Job: ${job.id}`
        : `Queued a project-scoped CAD edit for "${projectName}". Job: ${job.id}`,
      status: job.status,
      job_type: initialDesign ? "initial_cad_design" : "edit_cad",
      project_id: job.project_id,
      // Amendment A1: the SENT part id is echoed; null only when project-scoped.
      part_id: job.requested_part_id,
      job_id: job.id,
      client_request_id: job.client_request_id,
    });
  }

  private advanceEditJob(job: FixtureEditJob): void {
    if (job.status !== "queued" && job.status !== "running") return;
    if (job.status === "queued") {
      job.status = "running";
      job.started_at = nowIso();
      this.pushEditEvent(job, "job.started", "received", "Fixture edit job started.");
      return;
    }
    job.walkPosition += 1;
    const state = job.walk[job.walkPosition];
    if (state === undefined) return;
    job.state = editJobStateSchema.parse(state);

    if (state === "resolving_target" && job.resolved_part_id === null) {
      // Project-scoped edits resolve to the established CAD part mid-walk.
      job.resolved_part_id = FIXTURE_CAD_PART_ID;
    }
    if (state === "queueing_export" && job.resolved_part_id) {
      const exportJob = this.createExportJob(
        job.project_id,
        job.resolved_part_id,
        "export_cad",
        2,
      );
      exportJob.source_sha256 = pseudoSha256(job.id);
      job.export_job_id = exportJob.id;
    }
    if (state === "completed") {
      job.status = "completed";
      job.completed_at = nowIso();
      const part = job.resolved_part_id
        ? this.parts.get(job.resolved_part_id)
        : undefined;
      if (part) part.blank = false;
    }
    const eventType = STATE_EVENT_TYPES[state];
    if (eventType) {
      this.pushEditEvent(job, eventType, state, `Fixture: entered state ${state}.`);
    }
  }

  private pushEditEvent(
    job: FixtureEditJob,
    eventType: EditJobEventType,
    state: EditJobState,
    message: string,
  ): void {
    job.last_event_sequence += 1;
    job.events.push({
      id: crypto.randomUUID(),
      edit_job_id: job.id,
      sequence: job.last_event_sequence,
      event_type: eventType,
      state,
      message,
      metadata: {},
      created_at: nowIso(),
    });
  }

  private createExportJob(
    projectId: string,
    partId: string,
    type: GenerationJobType,
    pollsUntilDone: number,
  ): FixtureExportJob {
    const job: FixtureExportJob = {
      id: crypto.randomUUID(),
      project_id: projectId,
      part_id: partId,
      type,
      status: pollsUntilDone <= 0 ? "completed" : "queued",
      source_sha256: type === "export_mesh" ? null : pseudoSha256(partId),
      error_message: null,
      created_at: nowIso(),
      pollsUntilDone,
    };
    this.exportJobs.set(job.id, job);
    return job;
  }

  private advanceExportJob(job: FixtureExportJob): void {
    if (job.status !== "queued" && job.status !== "running") return;
    job.pollsUntilDone -= 1;
    job.status = job.pollsUntilDone <= 0 ? "completed" : "running";
  }

  private createIndexJob(
    projectId: string,
    type: "build_index" | "test_getter",
    requestText: string | null = null,
  ): FixtureIndexJob {
    const job: FixtureIndexJob = {
      id: crypto.randomUUID(),
      project_id: projectId,
      type,
      request_text: requestText,
      status: "queued",
      result: null,
      created_at: nowIso(),
      completed_at: null,
    };
    this.indexJobs.set(job.id, job);
    return job;
  }

  private guardDeletion(projectId: string, partId: string | undefined): void {
    for (const job of this.editJobs.values()) {
      if (job.project_id !== projectId) continue;
      if (partId && job.resolved_part_id !== null && job.resolved_part_id !== partId) {
        continue;
      }
      if (job.status === "running") {
        throw new ApiError(
          "conflict",
          "Deletion is blocked while a CAD edit is running. Try again after it finishes.",
          409,
        );
      }
      if (job.status === "queued") {
        job.status = "cancelled";
        job.state = "cancelled";
        job.error_code = "CANCELLED_FOR_DELETION";
        job.error_message =
          "The edit was cancelled because its project or part was deleted.";
        job.completed_at = nowIso();
      }
    }
    for (const job of this.exportJobs.values()) {
      if (job.project_id !== projectId) continue;
      if (partId && job.part_id !== partId) continue;
      if (job.status === "running") {
        throw new ApiError(
          "conflict",
          "Deletion is blocked while an export job is running. Try again after it finishes.",
          409,
        );
      }
      if (job.status === "queued") job.status = "cancelled";
    }
  }

  private toPartRecord(part: FixturePart): PartRecord {
    return {
      id: part.id,
      project_id: part.project_id,
      part_name: part.part_name,
      part_type: part.part_type,
    };
  }

  private requireName(value: string, field: string): string {
    const trimmed = value.trim();
    if (!trimmed) {
      throw new ApiError(
        "validation",
        `Request body must include non-empty \`${field}\`.`,
        400,
      );
    }
    return trimmed;
  }

  private requireProject(projectId: string): ProjectRecord {
    const project = this.projects.get(projectId);
    if (!project) {
      throw new ApiError(
        "not_found",
        `No project was found with id "${projectId}".`,
        404,
      );
    }
    return project;
  }

  private requireIndexableProject(projectId: string): ProjectRecord {
    const project = this.requireProject(projectId);
    const hasCadPart = [...this.parts.values()].some(
      (part) => part.project_id === projectId && part.part_type === "cad",
    );
    if (!hasCadPart) {
      throw new ApiError(
        "validation",
        `Project "${project.project_name}" does not contain any CAD parts.`,
        400,
      );
    }
    return project;
  }

  private requirePartById(partId: string): FixturePart {
    const part = this.parts.get(partId);
    if (!part) {
      throw new ApiError(
        "not_found",
        `No part was found with id "${partId}".`,
        404,
      );
    }
    return part;
  }

  private requirePartInProject(projectId: string, partId: string): FixturePart {
    const part = this.parts.get(partId);
    if (!part || part.project_id !== projectId) {
      throw new ApiError(
        "not_found",
        "The linked part no longer exists in this project.",
        404,
      );
    }
    return part;
  }

  private findPartByName(projectId: string, partName: string): FixturePart {
    this.requireProject(projectId);
    const trimmed = this.requireName(partName, "part_name");
    const part = [...this.parts.values()].find(
      (candidate) =>
        candidate.project_id === projectId &&
        candidate.part_name.toLowerCase() === trimmed.toLowerCase(),
    );
    if (!part) {
      throw new ApiError(
        "not_found",
        `No part was found with the name "${trimmed}" in the linked project.`,
        404,
      );
    }
    return part;
  }
}

export function createFixtureClient(): FixtureCadAgentClient {
  return new FixtureCadAgentClient();
}
