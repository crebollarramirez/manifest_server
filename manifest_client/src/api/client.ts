import type {
  ChatResponse,
  CreatePartResponse,
  CreateProjectResponse,
  DeletePartResponse,
  DeleteProjectResponse,
  ExportPartResponse,
  GetEditJobResponse,
  GetExportJobResponse,
  GetIndexJobResponse,
  IndexProjectResponse,
  LinkPartResponse,
  LinkProjectResponse,
  ListPartsResponse,
  ListProjectsResponse,
  PartType,
  TestIndexResponse,
  ValidatePartResponse,
} from "./schemas";

/** Mirrors MAX_HISTORY_MESSAGES in cad-agent index.ts:12 — longer histories are dead payload. */
export const MAX_HISTORY_MESSAGES = 8;

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

export type ChatRequest = {
  projectId: string;
  /** Omit for a project-scoped CAD edit (Amendment A1/A3). */
  partId?: string | null;
  /** Idempotency key for CAD workflows; unused by the mesh path (Amendment A2). */
  clientRequestId?: string;
  /** Must end with a non-empty user message; trimmed to MAX_HISTORY_MESSAGES by callers. */
  messages: ChatMessage[];
};

/**
 * The ONLY import path to the network (scalability discipline). Components and
 * queries depend on this interface; whether it is backed by fixtures or the
 * live Edge Function is invisible above this line. Every implementation must
 * return schema-parsed values and throw ApiError — nothing else escapes.
 *
 * getExportJob follows CONTRACT.md §4 — spec'd but not yet live in cad-agent;
 * until it ships, only the fixture implementation can serve it.
 */
export interface CadAgentClient {
  createProject(projectName: string): Promise<CreateProjectResponse>;
  linkProject(projectName: string): Promise<LinkProjectResponse>;
  createPart(
    projectId: string,
    partName: string,
    partType: PartType,
  ): Promise<CreatePartResponse>;
  linkPart(projectId: string, partName: string): Promise<LinkPartResponse>;
  listProjects(): Promise<ListProjectsResponse>;
  listParts(projectId: string): Promise<ListPartsResponse>;
  exportPart(partId: string): Promise<ExportPartResponse>;
  validatePart(partId: string): Promise<ValidatePartResponse>;
  indexProject(projectId: string): Promise<IndexProjectResponse>;
  testIndex(projectId: string, requestText: string): Promise<TestIndexResponse>;
  getIndexJob(projectId: string, jobId: string): Promise<GetIndexJobResponse>;
  getEditJob(jobId: string, afterSequence?: number): Promise<GetEditJobResponse>;
  getExportJob(jobId: string): Promise<GetExportJobResponse>;
  deleteProject(projectName: string): Promise<DeleteProjectResponse>;
  deletePart(projectId: string, partName: string): Promise<DeletePartResponse>;
  chat(request: ChatRequest): Promise<ChatResponse>;
}
