import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import OpenAI from "openai";
import { DEFAULT_MESH_MODEL_BODY } from "./mesh_model_template.ts";
import { MESH_SYSTEM_PROMPT } from "./mesh_prompt.ts";
import { SYSTEM_PROMPT } from "./prompt.ts";

const DEFAULT_OPENAI_MODEL = "gpt-5.4-mini";
const STORAGE_BUCKET = "3dProjects";
const MAX_HISTORY_MESSAGES = 8;
const STORAGE_PAGE_SIZE = 1000;
const STORAGE_DELETE_BATCH_SIZE = 100;
const JSON_HEADERS = { "Content-Type": "application/json" };
const CAD_MODEL_RUNTIME_IMPORT =
  "from cadquery_runtime import cad_part, cq, dataclass";
const MESH_MODEL_RUNTIME_IMPORT =
  "from blender_runtime import bpy, bmesh, dataclass, Vector, Matrix, Euler, mesh_part, mm, get_or_create_collection, link_object";
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const RESPONSE_SCHEMA = {
  name: "model_agent_response",
  strict: true,
  schema: {
    type: "object",
    additionalProperties: false,
    properties: {
      generated_code: {
        type: "string",
        description: "The complete AI-owned Python model-generation code body.",
      },
    },
    required: ["generated_code"],
  },
} as const;

type Action =
  | "create_project"
  | "create_part"
  | "link_project"
  | "link_part"
  | "list_projects"
  | "list_parts"
  | "export_part"
  | "validate_part"
  | "index_project"
  | "test_index"
  | "get_index_job"
  | "get_edit_job"
  | "delete_project"
  | "delete_part"
  | "chat";

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

type PartType = "cad" | "mesh";
type GenerationJobType = "validate_cad" | "export_cad" | "export_mesh";
type IndexJobType = "build_index" | "test_getter";

type PartConfig = {
  instructions: string;
  jobType: "validate_cad" | "export_mesh";
  runtimeImports: string[];
};

type ProjectRecord = {
  id: string;
  project_name: string;
};

type PartRecord = {
  id: string;
  project_id: string;
  part_name: string;
  part_type: PartType;
};

const PART_CONFIGS: Record<PartType, PartConfig> = {
  cad: {
    instructions: SYSTEM_PROMPT,
    jobType: "validate_cad",
    runtimeImports: [CAD_MODEL_RUNTIME_IMPORT],
  },
  mesh: {
    instructions: MESH_SYSTEM_PROMPT,
    jobType: "export_mesh",
    runtimeImports: [MESH_MODEL_RUNTIME_IMPORT],
  },
};

type ResponseInputMessage = {
  type: "message";
  role: "user" | "assistant";
  content: string;
};

class RequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: JSON_HEADERS,
  });
}

function requestObject(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null) {
    throw new RequestError("Request body must be a JSON object.", 400);
  }
  return value as Record<string, unknown>;
}

function requestAction(body: Record<string, unknown>): Action {
  const action = body.action;
  const validActions: Action[] = [
    "create_project",
    "create_part",
    "link_project",
    "link_part",
    "list_projects",
    "list_parts",
    "export_part",
    "validate_part",
    "index_project",
    "test_index",
    "get_index_job",
    "get_edit_job",
    "delete_project",
    "delete_part",
    "chat",
  ];
  if (typeof action !== "string" || !validActions.includes(action as Action)) {
    throw new RequestError("Request body includes an invalid `action`.", 400);
  }
  return action as Action;
}

function requiredName(body: Record<string, unknown>, field: string): string {
  const value = body[field];
  if (typeof value !== "string" || !value.trim()) {
    throw new RequestError(
      `Request body must include non-empty \`${field}\`.`,
      400,
    );
  }
  return value.trim();
}

function requiredUuid(body: Record<string, unknown>, field: string): string {
  const value = body[field];
  if (typeof value !== "string" || !UUID_PATTERN.test(value)) {
    throw new RequestError(
      `Request body must include valid UUID \`${field}\`.`,
      400,
    );
  }
  return value;
}

function optionalUuid(
  body: Record<string, unknown>,
  field: string,
): string | null {
  const value = body[field];
  if (value === undefined || value === null || value === "") {
    return null;
  }
  if (typeof value !== "string" || !UUID_PATTERN.test(value)) {
    throw new RequestError(
      `Request body must include valid UUID \`${field}\` when provided.`,
      400,
    );
  }
  return value;
}

function normalizeMessages(value: unknown): ChatMessage[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((message): message is Record<string, unknown> => {
      return typeof message === "object" && message !== null;
    })
    .filter((message): message is ChatMessage => {
      return (
        (message.role === "user" || message.role === "assistant") &&
        typeof message.content === "string" &&
        message.content.trim().length > 0
      );
    });
}

function projectPath(projectId: string, ...parts: string[]): string {
  return [projectId, ...parts].join("/");
}

function partSourcePath(
  projectId: string,
  partType: "cad" | "mesh",
  partId: string,
  ...parts: string[]
): string {
  return projectPath(projectId, "parts", partType, partId, ...parts);
}

function partExportPath(
  projectId: string,
  partId: string,
  ...parts: string[]
): string {
  return projectPath(projectId, "exports", partId, ...parts);
}

function stripMarkdownFence(code: string): string {
  const trimmed = code.trim();
  const match = trimmed.match(/^```(?:python)?\s*\n([\s\S]*?)\n```$/i);
  return match ? match[1].trim() : trimmed;
}

function composeModelSource(partType: PartType, modelBody: string): string {
  const runtimeImports = PART_CONFIGS[partType].runtimeImports;
  const body = stripMarkdownFence(modelBody)
    .split("\n")
    .filter((line) => {
      const trimmed = line.trim();
      return !runtimeImports.includes(trimmed);
    })
    .join("\n")
    .trim();
  return `${runtimeImports.join("\n")}\n\n${body}\n`;
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return Array.from(
    new Uint8Array(digest),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
}

async function uploadText(
  supabase: SupabaseClient,
  storagePath: string,
  content: string,
  contentType: string,
  upsert: boolean,
): Promise<void> {
  const { error } = await supabase.storage.from(STORAGE_BUCKET).upload(
    storagePath,
    new Blob([content], { type: contentType }),
    { contentType, upsert },
  );
  if (error) {
    throw new Error(
      `Storage upload failed for ${storagePath}: ${error.message}`,
    );
  }
}

async function createPartFiles(
  supabase: SupabaseClient,
  projectId: string,
  partId: string,
  partType: PartType,
): Promise<void> {
  const initialSource = partType === "cad"
    ? `${CAD_MODEL_RUNTIME_IMPORT}\n`
    : composeModelSource(partType, DEFAULT_MESH_MODEL_BODY);
  await uploadText(
    supabase,
    partSourcePath(projectId, partType, partId, "model.py"),
    initialSource,
    "text/x-python",
    false,
  );
  await uploadText(
    supabase,
    partSourcePath(projectId, partType, partId, "params.json"),
    "{}\n",
    "application/json",
    false,
  );
}

async function downloadModelSource(
  supabase: SupabaseClient,
  projectId: string,
  partId: string,
  partType: PartType,
): Promise<string> {
  const modelPath = partSourcePath(projectId, partType, partId, "model.py");
  const { data, error } = await supabase.storage.from(STORAGE_BUCKET).download(
    modelPath,
  );
  if (error) {
    throw new Error(
      `Storage download failed for ${modelPath}: ${error.message}`,
    );
  }
  return await data.text();
}

async function listStorageFiles(
  supabase: SupabaseClient,
  prefix: string,
): Promise<string[]> {
  const files: string[] = [];
  let offset = 0;

  while (true) {
    const { data, error } = await supabase.storage.from(STORAGE_BUCKET).list(
      prefix,
      { limit: STORAGE_PAGE_SIZE, offset },
    );
    if (error) {
      throw new Error(
        `Could not list Storage prefix ${prefix}: ${error.message}`,
      );
    }

    const entries = data ?? [];
    for (const entry of entries) {
      const entryPath = `${prefix}/${entry.name}`;
      if (entry.id === null || entry.metadata === null) {
        files.push(...await listStorageFiles(supabase, entryPath));
      } else {
        files.push(entryPath);
      }
    }

    if (entries.length < STORAGE_PAGE_SIZE) {
      break;
    }
    offset += entries.length;
  }

  return files;
}

async function deleteStoragePrefix(
  supabase: SupabaseClient,
  prefix: string,
): Promise<void> {
  const files = await listStorageFiles(supabase, prefix);
  for (
    let index = 0;
    index < files.length;
    index += STORAGE_DELETE_BATCH_SIZE
  ) {
    const batch = files.slice(index, index + STORAGE_DELETE_BATCH_SIZE);
    const { error } = await supabase.storage.from(STORAGE_BUCKET).remove(batch);
    if (error) {
      throw new Error(
        `Could not delete Storage prefix ${prefix}: ${error.message}`,
      );
    }
  }
}

async function findProjectByName(
  supabase: SupabaseClient,
  projectName: string,
): Promise<ProjectRecord | null> {
  const { data, error } = await supabase.rpc("find_project_by_name", {
    p_project_name: projectName,
  });
  if (error) {
    throw new Error(`Project lookup failed: ${error.message}`);
  }
  const project = Array.isArray(data) ? data[0] : null;
  return project
    ? { id: String(project.id), project_name: String(project.project_name) }
    : null;
}

async function findProjectById(
  supabase: SupabaseClient,
  projectId: string,
): Promise<ProjectRecord | null> {
  const { data, error } = await supabase
    .from("projects")
    .select("id, project_name")
    .eq("id", projectId)
    .maybeSingle();
  if (error) {
    throw new Error(`Project lookup failed: ${error.message}`);
  }
  return data as ProjectRecord | null;
}

async function findPartByName(
  supabase: SupabaseClient,
  projectId: string,
  partName: string,
): Promise<PartRecord | null> {
  const { data, error } = await supabase.rpc("find_part_by_name", {
    p_project_id: projectId,
    p_part_name: partName,
  });
  if (error) {
    throw new Error(`Part lookup failed: ${error.message}`);
  }
  const part = Array.isArray(data) ? data[0] : null;
  return part
    ? {
      id: String(part.id),
      project_id: String(part.project_id),
      part_name: String(part.part_name),
      part_type: part.part_type as PartType,
    }
    : null;
}

async function findPartInProject(
  supabase: SupabaseClient,
  projectId: string,
  partId: string,
): Promise<PartRecord | null> {
  const { data, error } = await supabase
    .from("parts")
    .select("id, project_id, part_name, part_type")
    .eq("project_id", projectId)
    .eq("id", partId)
    .maybeSingle();
  if (error) {
    throw new Error(`Part lookup failed: ${error.message}`);
  }
  return data as PartRecord | null;
}

async function findPartById(
  supabase: SupabaseClient,
  partId: string,
): Promise<PartRecord | null> {
  const { data, error } = await supabase
    .from("parts")
    .select("id, project_id, part_name, part_type")
    .eq("id", partId)
    .maybeSingle();
  if (error) {
    throw new Error(`Part lookup failed: ${error.message}`);
  }
  return data as PartRecord | null;
}

async function queueGenerationJob(
  supabase: SupabaseClient,
  part: PartRecord,
  jobType: GenerationJobType,
  sourceSha256: string | null,
): Promise<string> {
  const { data: job, error } = await supabase
    .from("generation_jobs")
    .insert({
      project_id: part.project_id,
      part_id: part.id,
      type: jobType,
      status: "queued",
      source_sha256: sourceSha256,
    })
    .select("id")
    .single();
  if (error || !job?.id) {
    throw new Error(
      `Could not queue ${jobType} job: ${error?.message ?? "missing job id"}`,
    );
  }
  return String(job.id);
}

async function queueIndexJob(
  supabase: SupabaseClient,
  projectId: string,
  jobType: IndexJobType,
  requestText: string | null = null,
): Promise<{ id: string; status: string }> {
  const { data: job, error } = await supabase
    .from("index_jobs")
    .insert({
      project_id: projectId,
      type: jobType,
      request_text: requestText,
      status: "queued",
    })
    .select("id, status")
    .single();

  if (!error && job?.id) {
    return { id: String(job.id), status: String(job.status) };
  }

  if (jobType === "build_index" && error?.code === "23505") {
    const { data: existing, error: lookupError } = await supabase
      .from("index_jobs")
      .select("id, status")
      .eq("project_id", projectId)
      .eq("type", "build_index")
      .in("status", ["queued", "running"])
      .order("created_at", { ascending: true })
      .limit(1)
      .maybeSingle();
    if (lookupError || !existing?.id) {
      throw new Error(
        `Could not resolve active index job: ${
          lookupError?.message ?? "missing job"
        }`,
      );
    }
    return { id: String(existing.id), status: String(existing.status) };
  }

  throw new Error(
    `Could not queue ${jobType} job: ${error?.message ?? "missing job id"}`,
  );
}

async function queueEditJob(
  supabase: SupabaseClient,
  projectId: string,
  messages: ChatMessage[],
  requestedPart?: PartRecord,
  workflowMode: "edit" | "initial_design" = "edit",
): Promise<{ id: string; status: string }> {
  const latestRequest = messages.at(-1)?.content.trim();
  if (!latestRequest) {
    throw new RequestError(
      "Messages must end with a non-empty user message.",
      400,
    );
  }

  const initialDesign = workflowMode === "initial_design";
  const { data: job, error } = await supabase
    .from("edit_jobs")
    .insert({
      project_id: projectId,
      request_text: latestRequest,
      messages: messages.slice(-MAX_HISTORY_MESSAGES),
      status: "queued",
      state: "received",
      workflow_mode: workflowMode,
      requested_part_id: requestedPart?.id ?? null,
      ...(initialDesign && requestedPart
        ? {
          resolved_part_id: requestedPart.id,
          resolved_targets: [{
            part_id: requestedPart.id,
            part_name: requestedPart.part_name,
            semantic_ids: [],
            confidence: 1,
            reason: "Linked blank CAD part selected for initial design.",
            candidates: [],
          }],
        }
        : {}),
    })
    .select("id, status")
    .single();
  if (error || !job?.id) {
    throw new Error(
      `Could not queue CAD edit job: ${error?.message ?? "missing job id"}`,
    );
  }
  return { id: String(job.id), status: String(job.status) };
}

function isBlankCadSource(source: string): boolean {
  return source === `${CAD_MODEL_RUNTIME_IMPORT}\n`;
}

async function currentCadSourceSha256(
  supabase: SupabaseClient,
  part: PartRecord,
): Promise<string> {
  const source = await downloadModelSource(
    supabase,
    part.project_id,
    part.id,
    "cad",
  );
  return await sha256Hex(source);
}

async function hasRunningJobs(
  supabase: SupabaseClient,
  projectId: string,
  partId?: string,
): Promise<boolean> {
  let query = supabase
    .from("generation_jobs")
    .select("id")
    .eq("project_id", projectId)
    .eq("status", "running")
    .limit(1);
  if (partId) {
    query = query.eq("part_id", partId);
  }
  const { data, error } = await query;
  if (error) {
    throw new Error(`Could not inspect running export jobs: ${error.message}`);
  }
  return (data?.length ?? 0) > 0;
}

async function cancelQueuedJobs(
  supabase: SupabaseClient,
  projectId: string,
  partId?: string,
): Promise<void> {
  let query = supabase
    .from("generation_jobs")
    .update({ status: "cancelled" })
    .eq("project_id", projectId)
    .eq("status", "queued");
  if (partId) {
    query = query.eq("part_id", partId);
  }
  const { error } = await query;
  if (error) {
    throw new Error(`Could not cancel queued export jobs: ${error.message}`);
  }
}

async function hasRunningIndexJobs(
  supabase: SupabaseClient,
  projectId: string,
): Promise<boolean> {
  const { data, error } = await supabase
    .from("index_jobs")
    .select("id")
    .eq("project_id", projectId)
    .eq("status", "running")
    .limit(1);
  if (error) {
    throw new Error(`Could not inspect running index jobs: ${error.message}`);
  }
  return (data?.length ?? 0) > 0;
}

async function cancelQueuedIndexJobs(
  supabase: SupabaseClient,
  projectId: string,
): Promise<void> {
  const { error } = await supabase
    .from("index_jobs")
    .update({
      status: "cancelled",
      completed_at: new Date().toISOString(),
    })
    .eq("project_id", projectId)
    .eq("status", "queued");
  if (error) {
    throw new Error(`Could not cancel queued index jobs: ${error.message}`);
  }
}

async function hasRunningEditJobs(
  supabase: SupabaseClient,
  projectId: string,
  partId?: string,
): Promise<boolean> {
  let query = supabase
    .from("edit_jobs")
    .select("id")
    .eq("project_id", projectId)
    .eq("status", "running")
    .limit(1);
  if (partId) {
    query = query.or(
      `resolved_part_id.is.null,resolved_part_id.eq.${partId}`,
    );
  }
  const { data, error } = await query;
  if (error) {
    throw new Error(`Could not inspect running edit jobs: ${error.message}`);
  }
  return (data?.length ?? 0) > 0;
}

async function cancelQueuedEditJobs(
  supabase: SupabaseClient,
  projectId: string,
  partId?: string,
): Promise<void> {
  let query = supabase
    .from("edit_jobs")
    .update({
      status: "cancelled",
      state: "cancelled",
      error_code: "CANCELLED_FOR_DELETION",
      error_message:
        "The edit was cancelled because its project or part was deleted.",
      completed_at: new Date().toISOString(),
    })
    .eq("project_id", projectId)
    .eq("status", "queued");
  if (partId) {
    query = query.or(
      `resolved_part_id.is.null,resolved_part_id.eq.${partId}`,
    );
  }
  const { error } = await query;
  if (error) {
    throw new Error(`Could not cancel queued edit jobs: ${error.message}`);
  }
}

async function prepareForDeletion(
  supabase: SupabaseClient,
  projectId: string,
  partId?: string,
): Promise<void> {
  if (!partId && await hasRunningIndexJobs(supabase, projectId)) {
    throw new RequestError(
      "Deletion is blocked while an index job is running. Try again after it finishes.",
      409,
    );
  }
  if (await hasRunningEditJobs(supabase, projectId, partId)) {
    throw new RequestError(
      "Deletion is blocked while a CAD edit is running. Try again after it finishes.",
      409,
    );
  }
  if (await hasRunningJobs(supabase, projectId, partId)) {
    throw new RequestError(
      "Deletion is blocked while an export job is running. Try again after it finishes.",
      409,
    );
  }
  if (!partId) {
    await cancelQueuedIndexJobs(supabase, projectId);
  }
  await cancelQueuedEditJobs(supabase, projectId, partId);
  await cancelQueuedJobs(supabase, projectId, partId);
  if (!partId && await hasRunningIndexJobs(supabase, projectId)) {
    throw new RequestError(
      "Deletion is blocked because an index job started. Try again after it finishes.",
      409,
    );
  }
  if (await hasRunningEditJobs(supabase, projectId, partId)) {
    throw new RequestError(
      "Deletion is blocked because a CAD edit started. Try again after it finishes.",
      409,
    );
  }
  if (await hasRunningJobs(supabase, projectId, partId)) {
    throw new RequestError(
      "Deletion is blocked because an export job started. Try again after it finishes.",
      409,
    );
  }
}

function buildResponseInput(
  messages: ChatMessage[],
  currentModelSource: string,
): ResponseInputMessage[] {
  const latestUserMessage = messages.at(-1);
  if (!latestUserMessage || latestUserMessage.role !== "user") {
    throw new RequestError("Messages must end with a user message.", 400);
  }

  const history = messages
    .slice(0, -1)
    .slice(-(MAX_HISTORY_MESSAGES - 1))
    .map((message) => ({
      type: "message" as const,
      role: message.role,
      content: message.content,
    }));
  history.push({
    type: "message",
    role: "user",
    content: "Current state of model.py:\n```python\n" +
      currentModelSource +
      "\n```\n\nReturn the complete replacement model-generation body in the " +
      "`generated_code` response field. It must contain Python source, not a " +
      "status value such as OK.\n\nUser request:\n" +
      latestUserMessage.content,
  });
  return history;
}

function generatedModelBody(responseText: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(responseText);
  } catch {
    throw new Error("OpenAI returned invalid JSON.");
  }
  if (typeof parsed !== "object" || parsed === null) {
    throw new Error("AI response must be a JSON object.");
  }
  const code = (parsed as Record<string, unknown>).generated_code;
  if (typeof code !== "string" || !code.trim()) {
    throw new Error("AI response missing generated_code.");
  }
  if (["OK", "SUCCESS", "DONE"].includes(code.trim().toUpperCase())) {
    throw new Error("AI returned a status value instead of Python code.");
  }
  return code;
}

async function handleCreateProject(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const projectName = requiredName(body, "project_name");
  const { data, error } = await supabase
    .from("projects")
    .insert({ project_name: projectName })
    .select("id, project_name")
    .single();
  if (error?.code === "23505") {
    throw new RequestError(
      `A project named "${projectName}" already exists.`,
      409,
    );
  }
  if (error || !data) {
    throw new Error(
      `Could not create project: ${error?.message ?? "missing row"}`,
    );
  }
  return {
    message: `Created and linked project "${data.project_name}".`,
    status: "created",
    project: data,
  };
}

async function handleLinkProject(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const projectName = requiredName(body, "project_name");
  const project = await findProjectByName(supabase, projectName);
  if (!project) {
    throw new RequestError(
      `No project was found with the name "${projectName}".`,
      404,
    );
  }
  return {
    message: `Linked project "${project.project_name}".`,
    status: "linked",
    project,
  };
}

async function handleCreatePart(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const projectId = requiredUuid(body, "project_id");
  const partName = requiredName(body, "part_name");
  const partType = body.part_type;
  if (partType !== "cad" && partType !== "mesh") {
    throw new RequestError(
      "Part type must be either `cad` or `mesh`.",
      400,
    );
  }

  const { data, error } = await supabase
    .from("parts")
    .insert({ project_id: projectId, part_name: partName, part_type: partType })
    .select("id, project_id, part_name, part_type")
    .single();
  if (error?.code === "23505") {
    throw new RequestError(
      `A part named "${partName}" already exists in the linked project.`,
      409,
    );
  }
  if (error?.code === "23503") {
    throw new RequestError("The linked project no longer exists.", 404);
  }
  if (error || !data) {
    throw new Error(
      `Could not create part: ${error?.message ?? "missing row"}`,
    );
  }

  const part = data as PartRecord;
  try {
    await createPartFiles(supabase, projectId, part.id, partType);
  } catch (error) {
    try {
      await deleteStoragePrefix(
        supabase,
        partSourcePath(projectId, partType, part.id),
      );
    } finally {
      await supabase.from("parts").delete().eq("id", part.id);
    }
    throw error;
  }

  let indexJob: { id: string; status: string } | null = null;
  const warnings: string[] = [];
  if (part.part_type === "cad") {
    try {
      indexJob = await queueIndexJob(supabase, projectId, "build_index");
    } catch (_error) {
      warnings.push(
        `Automatic indexing could not be queued; run /index ${projectId}.`,
      );
    }
  }

  return {
    message: `Created and linked ${part.part_type} part "${part.part_name}".`,
    status: "created",
    part,
    ...(part.part_type === "cad"
      ? {
        index_job_id: indexJob?.id ?? null,
        index_status: indexJob?.status ?? "not_queued",
        index_job_type: "build_index",
        warnings,
      }
      : {}),
  };
}

async function handleLinkPart(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const projectId = requiredUuid(body, "project_id");
  const partName = requiredName(body, "part_name");
  const part = await findPartByName(supabase, projectId, partName);
  if (!part) {
    throw new RequestError(
      `No part was found with the name "${partName}" in the linked project.`,
      404,
    );
  }
  return {
    message: `Linked ${part.part_type} part "${part.part_name}".`,
    status: "linked",
    part,
  };
}

async function handleListProjects(
  supabase: SupabaseClient,
): Promise<unknown> {
  const { data, error } = await supabase
    .from("projects")
    .select("id, project_name")
    .order("project_name", { ascending: true });
  if (error) {
    throw new Error(`Could not list projects: ${error.message}`);
  }

  const projects = (data ?? []) as ProjectRecord[];
  return {
    message: projects.length === 0
      ? "No projects found."
      : `Projects:\n${
        projects.map((project) => `- ${project.project_name}`).join("\n")
      }`,
    status: "listed",
    projects,
  };
}

async function handleListParts(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const projectId = requiredUuid(body, "project_id");
  const { data: project, error: projectError } = await supabase
    .from("projects")
    .select("id, project_name")
    .eq("id", projectId)
    .maybeSingle();
  if (projectError) {
    throw new Error(
      `Could not inspect the linked project: ${projectError.message}`,
    );
  }
  if (!project) {
    throw new RequestError("The linked project no longer exists.", 404);
  }

  const { data, error } = await supabase
    .from("parts")
    .select("id, project_id, part_name, part_type")
    .eq("project_id", projectId)
    .order("part_name", { ascending: true });
  if (error) {
    throw new Error(`Could not list parts: ${error.message}`);
  }

  const parts = (data ?? []) as PartRecord[];
  return {
    message: parts.length === 0
      ? `No parts found in project "${project.project_name}".`
      : `Parts in "${project.project_name}":\n${
        parts.map((part) =>
          `- ${part.part_name} [${part.part_type}] id=${part.id}`
        ).join("\n")
      }`,
    status: "listed",
    project,
    parts,
  };
}

async function handleExportPart(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const partId = requiredUuid(body, "part_id");
  const part = await findPartById(supabase, partId);
  if (!part) {
    throw new RequestError(`No part was found with id "${partId}".`, 404);
  }

  const jobType: GenerationJobType = part.part_type === "cad"
    ? "export_cad"
    : "export_mesh";
  const sourceSha256 = part.part_type === "cad"
    ? await currentCadSourceSha256(supabase, part)
    : null;
  const jobId = await queueGenerationJob(
    supabase,
    part,
    jobType,
    sourceSha256,
  );
  return {
    message:
      `Queued ${jobType} for ${part.part_type} part "${part.part_name}". Job: ${jobId}`,
    status: "queued",
    job_type: jobType,
    project_id: part.project_id,
    part_id: part.id,
    job_id: jobId,
  };
}

async function handleValidatePart(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const partId = requiredUuid(body, "part_id");
  const part = await findPartById(supabase, partId);
  if (!part) {
    throw new RequestError(`No part was found with id "${partId}".`, 404);
  }
  if (part.part_type !== "cad") {
    throw new RequestError("Only CAD parts support validation.", 400);
  }

  const sourceSha256 = await currentCadSourceSha256(supabase, part);
  const jobId = await queueGenerationJob(
    supabase,
    part,
    "validate_cad",
    sourceSha256,
  );
  return {
    message:
      `Queued validate_cad for cad part "${part.part_name}". Job: ${jobId}`,
    status: "validating",
    job_type: "validate_cad",
    project_id: part.project_id,
    part_id: part.id,
    job_id: jobId,
  };
}

async function requireProject(
  supabase: SupabaseClient,
  projectId: string,
): Promise<ProjectRecord> {
  const project = await findProjectById(supabase, projectId);
  if (!project) {
    throw new RequestError(
      `No project was found with id "${projectId}".`,
      404,
    );
  }
  return project;
}

async function requireIndexableProject(
  supabase: SupabaseClient,
  projectId: string,
): Promise<ProjectRecord> {
  const project = await requireProject(supabase, projectId);
  const { data, error } = await supabase
    .from("parts")
    .select("id")
    .eq("project_id", projectId)
    .eq("part_type", "cad")
    .limit(1);
  if (error) {
    throw new Error(`Could not inspect project CAD parts: ${error.message}`);
  }
  if (!data?.length) {
    throw new RequestError(
      `Project "${project.project_name}" does not contain any CAD parts.`,
      400,
    );
  }
  return project;
}

async function handleIndexProject(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const projectId = requiredUuid(body, "project_id");
  const project = await requireIndexableProject(supabase, projectId);
  const job = await queueIndexJob(supabase, projectId, "build_index");
  return {
    message:
      `Index job for project "${project.project_name}" is ${job.status}. Job: ${job.id}`,
    status: job.status,
    job_type: "build_index",
    project_id: projectId,
    job_id: job.id,
  };
}

async function handleTestIndex(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const projectId = requiredUuid(body, "project_id");
  const requestText = requiredName(body, "request_text");
  const project = await requireProject(supabase, projectId);
  const job = await queueIndexJob(
    supabase,
    projectId,
    "test_getter",
    requestText,
  );
  return {
    message:
      `Queued index Getter test for project "${project.project_name}". Job: ${job.id}`,
    status: job.status,
    job_type: "test_getter",
    project_id: projectId,
    job_id: job.id,
  };
}

async function handleGetIndexJob(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const projectId = requiredUuid(body, "project_id");
  const jobId = requiredUuid(body, "job_id");
  const { data: job, error } = await supabase
    .from("index_jobs")
    .select(
      "id, project_id, type, request_text, status, result, error_message, created_at, started_at, completed_at",
    )
    .eq("project_id", projectId)
    .eq("id", jobId)
    .maybeSingle();
  if (error) {
    throw new Error(`Could not inspect index job: ${error.message}`);
  }
  if (!job) {
    throw new RequestError(`No index job was found with id "${jobId}".`, 404);
  }
  return {
    message: `Index job ${jobId} is ${job.status}.`,
    status: job.status,
    job,
  };
}

async function handleGetEditJob(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const jobId = requiredUuid(body, "job_id");
  const { data: job, error } = await supabase
    .from("edit_jobs")
    .select(
      "id, project_id, requested_part_id, workflow_mode, resolved_part_id, resolved_targets, status, state, attempt_count, max_attempts, validation_job_id, index_job_id, export_job_id, history, result, error_code, error_message, created_at, started_at, heartbeat_at, completed_at",
    )
    .eq("id", jobId)
    .maybeSingle();
  if (error) {
    throw new Error(`Could not inspect CAD edit job: ${error.message}`);
  }
  if (!job) {
    throw new RequestError(
      `No CAD edit job was found with id "${jobId}".`,
      404,
    );
  }
  const safeHistory = Array.isArray(job.history)
    ? job.history.map((event: Record<string, unknown>) => {
      const allowed = [
        "recorded_at",
        "event",
        "attempt",
        "part_id",
        "semantic_ids",
        "confidence",
        "reason",
        "candidate_hash",
        "changed_symbols",
        "validation_job_id",
        "validation_status",
        "validation_result",
      ];
      return Object.fromEntries(
        allowed
          .filter((key) => event[key] !== undefined)
          .map((key) => [key, event[key]]),
      );
    })
    : [];
  return {
    message: `CAD edit job ${jobId} is ${job.status} (${job.state}).`,
    status: job.status,
    job: { ...job, history: safeHistory },
  };
}

async function handleDeleteProject(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const projectName = requiredName(body, "project_name");
  const project = await findProjectByName(supabase, projectName);
  if (!project) {
    throw new RequestError(
      `No project was found with the name "${projectName}".`,
      404,
    );
  }

  await prepareForDeletion(supabase, project.id);
  await deleteStoragePrefix(supabase, project.id);
  const { error } = await supabase.from("projects").delete().eq(
    "id",
    project.id,
  );
  if (error) {
    throw new Error(`Could not delete project: ${error.message}`);
  }
  return {
    message: `Deleted project "${project.project_name}" and all of its parts.`,
    status: "deleted",
    project,
  };
}

async function handleDeletePart(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const projectId = requiredUuid(body, "project_id");
  const partName = requiredName(body, "part_name");
  const part = await findPartByName(supabase, projectId, partName);
  if (!part) {
    throw new RequestError(
      `No part was found with the name "${partName}" in the linked project.`,
      404,
    );
  }

  await prepareForDeletion(supabase, projectId, part.id);
  await deleteStoragePrefix(
    supabase,
    partSourcePath(projectId, part.part_type, part.id),
  );
  await deleteStoragePrefix(supabase, partExportPath(projectId, part.id));
  await deleteStoragePrefix(
    supabase,
    projectPath(projectId, "candidates", "cad", part.id),
  );
  const { error } = await supabase.from("parts").delete().eq("id", part.id);
  if (error) {
    throw new Error(`Could not delete part: ${error.message}`);
  }
  return {
    message: `Deleted ${part.part_type} part "${part.part_name}".`,
    status: "deleted",
    part,
  };
}

async function handleChat(
  supabase: SupabaseClient,
  body: Record<string, unknown>,
): Promise<unknown> {
  const projectId = requiredUuid(body, "project_id");
  const partId = optionalUuid(body, "part_id");
  const messages = normalizeMessages(body.messages);
  if (messages.length === 0 || messages.at(-1)?.role !== "user") {
    throw new RequestError(
      "Messages must end with a non-empty user message.",
      400,
    );
  }

  let part: PartRecord | null = null;
  if (partId) {
    part = await findPartInProject(supabase, projectId, partId);
    if (!part) {
      throw new RequestError(
        "The linked part no longer exists in this project.",
        404,
      );
    }
  }

  if (part?.part_type === "cad") {
    const source = await downloadModelSource(supabase, projectId, part.id, "cad");
    if (isBlankCadSource(source)) {
      const job = await queueEditJob(
        supabase,
        projectId,
        messages,
        part,
        "initial_design",
      );
      return {
        message: `Queued an initial CAD design for "${part.part_name}". Job: ${job.id}`,
        status: job.status,
        job_type: "initial_cad_design",
        project_id: projectId,
        part_id: part.id,
        job_id: job.id,
      };
    }
  }

  if (!part || part.part_type === "cad") {
    const project = await requireIndexableProject(supabase, projectId);
    const job = await queueEditJob(
      supabase,
      projectId,
      messages,
      part ?? undefined,
    );
    return {
      message:
        `Queued a project-scoped CAD edit for "${project.project_name}". Job: ${job.id}`,
      status: job.status,
      job_type: "edit_cad",
      project_id: projectId,
      part_id: null,
      job_id: job.id,
    };
  }

  const apiKey = Deno.env.get("OPENAI_API_KEY");
  if (!apiKey) {
    throw new Error("Missing OPENAI_API_KEY secret.");
  }
  const currentModelSource = await downloadModelSource(
    supabase,
    projectId,
    part.id,
    part.part_type,
  );
  const config = PART_CONFIGS[part.part_type];
  const openai = new OpenAI({ apiKey });
  const response = await openai.responses.create({
    model: Deno.env.get("OPENAI_MODEL") ?? DEFAULT_OPENAI_MODEL,
    instructions: config.instructions,
    input: buildResponseInput(messages, currentModelSource),
    text: {
      format: {
        type: "json_schema",
        ...RESPONSE_SCHEMA,
      },
    },
  });
  if (!response.output_text) {
    throw new Error("OpenAI returned an empty response.");
  }

  const generatedSource = composeModelSource(
    part.part_type,
    generatedModelBody(response.output_text),
  );
  await uploadText(
    supabase,
    partSourcePath(projectId, part.part_type, part.id, "model.py"),
    generatedSource,
    "text/x-python",
    true,
  );
  const jobId = await queueGenerationJob(
    supabase,
    part,
    config.jobType,
    null,
  );

  return {
    message: `Updated mesh part "${part.part_name}" and queued its export.`,
    status: "queued",
    job_type: config.jobType,
    project_id: projectId,
    part_id: part.id,
    job_id: jobId,
  };
}

Deno.serve(async (req) => {
  if (req.method !== "POST") {
    return jsonResponse({ error: "Method not allowed" }, 405);
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!supabaseUrl || !serviceRoleKey) {
    return jsonResponse(
      { error: "Missing Supabase service configuration." },
      500,
    );
  }

  try {
    const body = requestObject(await req.json());
    const action = requestAction(body);
    const supabase = createClient(supabaseUrl, serviceRoleKey, {
      auth: { persistSession: false, autoRefreshToken: false },
    });

    const handlers: Record<Action, () => Promise<unknown>> = {
      create_project: () => handleCreateProject(supabase, body),
      create_part: () => handleCreatePart(supabase, body),
      link_project: () => handleLinkProject(supabase, body),
      link_part: () => handleLinkPart(supabase, body),
      list_projects: () => handleListProjects(supabase),
      list_parts: () => handleListParts(supabase, body),
      export_part: () => handleExportPart(supabase, body),
      validate_part: () => handleValidatePart(supabase, body),
      index_project: () => handleIndexProject(supabase, body),
      test_index: () => handleTestIndex(supabase, body),
      get_index_job: () => handleGetIndexJob(supabase, body),
      get_edit_job: () => handleGetEditJob(supabase, body),
      delete_project: () => handleDeleteProject(supabase, body),
      delete_part: () => handleDeletePart(supabase, body),
      chat: () => handleChat(supabase, body),
    };
    return jsonResponse(await handlers[action]());
  } catch (error) {
    if (error instanceof RequestError) {
      return jsonResponse({ error: error.message }, error.status);
    }
    const message = error instanceof Error
      ? error.message
      : "Unknown backend error.";
    return jsonResponse({ error: message }, 500);
  }
});
