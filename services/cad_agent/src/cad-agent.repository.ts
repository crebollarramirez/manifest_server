import { Injectable } from '@nestjs/common';
import { createClient, SupabaseClient } from '@supabase/supabase-js';
import {
  EditJob,
  EditJobEvent,
  PartRecord,
  PartType,
  ProjectRecord,
  WorkflowError,
} from './contracts';

const STORAGE_BUCKET = '3dProjects';
const STORAGE_PAGE_SIZE = 1000;
const STORAGE_DELETE_BATCH_SIZE = 100;

function requireEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required.`);
  return value;
}

function row<T>(value: unknown, label: string): T {
  const current = Array.isArray(value) ? value[0] : value;
  if (!current || typeof current !== 'object') {
    throw new Error(`${label} returned an unexpected value.`);
  }
  return current as T;
}

@Injectable()
export class CadAgentRepository {
  readonly client: SupabaseClient;

  constructor() {
    this.client = createClient(
      requireEnvironment('SUPABASE_URL'),
      requireEnvironment('SUPABASE_SERVICE_ROLE_KEY'),
      { auth: { persistSession: false, autoRefreshToken: false } },
    );
  }

  async editJob(id: string): Promise<EditJob> {
    const { data, error } = await this.client
      .from('edit_jobs')
      .select('*')
      .eq('id', id)
      .maybeSingle();
    if (error) throw new Error(error.message);
    if (!data) throw new WorkflowError('EDIT_JOB_MISSING', `Edit job "${id}" was not found.`);
    return row<EditJob>(data, 'submit_cad_edit_job');
  }

  async editJobForClientRequest(clientRequestId: string): Promise<EditJob | null> {
    const { data, error } = await this.client
      .from('edit_jobs')
      .select('*')
      .eq('client_request_id', clientRequestId)
      .maybeSingle();
    if (error) throw new Error(error.message);
    return (data as EditJob | null) ?? null;
  }

  async submitEditJob(input: {
    projectId: string;
    requestText: string;
    messages: Array<{ role: 'user' | 'assistant'; content: string }>;
    requestedPartId: string | null;
    workflowMode: 'edit' | 'initial_design';
    clientRequestId: string;
    requestFingerprint: string;
    resolvedTargets: unknown[];
  }): Promise<EditJob> {
    const { data, error } = await this.client.rpc('submit_cad_edit_job', {
      p_project_id: input.projectId,
      p_request_text: input.requestText,
      p_messages: input.messages,
      p_requested_part_id: input.requestedPartId,
      p_workflow_mode: input.workflowMode,
      p_client_request_id: input.clientRequestId,
      p_request_fingerprint: input.requestFingerprint,
      p_resolved_targets: input.resolvedTargets,
    });
    if (error) {
      const code = error.message.includes('CLIENT_REQUEST_ID_CONFLICT')
        ? 'CLIENT_REQUEST_ID_CONFLICT'
        : error.message.includes('PART_EDIT_IN_PROGRESS')
          ? 'PART_EDIT_IN_PROGRESS'
        : 'EDIT_SUBMISSION_FAILED';
      throw new WorkflowError(code, error.message);
    }
    return data as EditJob;
  }

  async events(jobId: string, afterSequence = 0, throughSequence?: number): Promise<EditJobEvent[]> {
    let query = this.client
      .from('edit_job_events')
      .select('*')
      .eq('edit_job_id', jobId)
      .gt('sequence', afterSequence)
      .order('sequence', { ascending: true });
    if (throughSequence !== undefined) query = query.lte('sequence', throughSequence);
    const { data, error } = await query;
    if (error) throw new Error(error.message);
    return (data ?? []) as EditJobEvent[];
  }

  async eventsForJobs(cursors: Record<string, number>): Promise<EditJobEvent[]> {
    if (!Object.keys(cursors).length) return [];
    const { data, error } = await this.client.rpc('edit_job_events_after_cursors', {
      p_cursors: cursors,
      p_limit_per_job: 100,
    });
    if (error) throw new Error(error.message);
    return (data ?? []) as EditJobEvent[];
  }

  async part(projectId: string, partId: string): Promise<Record<string, unknown>> {
    const { data, error } = await this.client
      .from('parts')
      .select('id, project_id, part_name, part_type')
      .eq('project_id', projectId)
      .eq('id', partId)
      .maybeSingle();
    if (error) throw new Error(error.message);
    if (!data) throw new WorkflowError('PART_NOT_FOUND', 'The requested part does not exist in the project.');
    return data;
  }

  async project(projectId: string): Promise<Record<string, unknown>> {
    const { data, error } = await this.client
      .from('projects')
      .select('id, project_name')
      .eq('id', projectId)
      .maybeSingle();
    if (error) throw new Error(error.message);
    if (!data) throw new WorkflowError('PROJECT_NOT_FOUND', 'The requested project does not exist.');
    return data;
  }

  async readText(path: string): Promise<string> {
    const { data, error } = await this.client.storage.from(STORAGE_BUCKET).download(path);
    if (error || !data) throw new WorkflowError('SOURCE_MISSING', error?.message ?? `Source "${path}" was not found.`);
    return data.text();
  }

  async projects(): Promise<ProjectRecord[]> {
    const { data, error } = await this.client
      .from('projects')
      .select('id, project_name')
      .order('project_name', { ascending: true });
    if (error) throw new Error(`Could not list projects: ${error.message}`);
    return (data ?? []) as ProjectRecord[];
  }

  async createProject(projectName: string): Promise<ProjectRecord> {
    const { data, error } = await this.client
      .from('projects')
      .insert({ project_name: projectName })
      .select('id, project_name')
      .single();
    if (error?.code === '23505') {
      throw new WorkflowError('PROJECT_EXISTS', `A project named "${projectName}" already exists.`);
    }
    if (error || !data) {
      throw new Error(`Could not create project: ${error?.message ?? 'missing row'}`);
    }
    return data as ProjectRecord;
  }

  async parts(projectId: string): Promise<PartRecord[]> {
    const { data, error } = await this.client
      .from('parts')
      .select('id, project_id, part_name, part_type')
      .eq('project_id', projectId)
      .order('part_name', { ascending: true });
    if (error) throw new Error(`Could not list parts: ${error.message}`);
    return (data ?? []) as PartRecord[];
  }

  async partById(partId: string): Promise<PartRecord> {
    const { data, error } = await this.client
      .from('parts')
      .select('id, project_id, part_name, part_type')
      .eq('id', partId)
      .maybeSingle();
    if (error) throw new Error(`Part lookup failed: ${error.message}`);
    if (!data) throw new WorkflowError('PART_NOT_FOUND', `No part was found with id "${partId}".`);
    return data as PartRecord;
  }

  async createPart(input: {
    projectId: string;
    partName: string;
    partType: PartType;
  }): Promise<PartRecord> {
    const { data, error } = await this.client
      .from('parts')
      .insert({
        project_id: input.projectId,
        part_name: input.partName,
        part_type: input.partType,
      })
      .select('id, project_id, part_name, part_type')
      .single();
    if (error?.code === '23505') {
      throw new WorkflowError(
        'PART_EXISTS',
        `A part named "${input.partName}" already exists in the linked project.`,
      );
    }
    if (error?.code === '23503') {
      throw new WorkflowError('PROJECT_NOT_FOUND', 'The linked project no longer exists.');
    }
    if (error || !data) {
      throw new Error(`Could not create part: ${error?.message ?? 'missing row'}`);
    }
    return data as PartRecord;
  }

  async deleteProject(projectId: string): Promise<void> {
    const { error } = await this.client.from('projects').delete().eq('id', projectId);
    if (error?.code === '23503') {
      throw new WorkflowError(
        'PROJECT_HAS_PUBLISHED_ASSEMBLIES',
        'This project has published assembly revisions and cannot be deleted while they exist.',
      );
    }
    if (error) throw new Error(`Could not delete project: ${error.message}`);
  }

  async deletePart(partId: string): Promise<void> {
    const { error } = await this.client.from('parts').delete().eq('id', partId);
    if (error) throw new Error(`Could not delete part: ${error.message}`);
  }

  async uploadText(
    path: string,
    content: string,
    contentType: string,
    upsert: boolean,
  ): Promise<void> {
    const { error } = await this.client.storage
      .from(STORAGE_BUCKET)
      .upload(path, Buffer.from(content, 'utf8'), { contentType, upsert });
    if (error) throw new Error(`Storage upload failed for ${path}: ${error.message}`);
  }

  async storageFiles(prefix: string): Promise<string[]> {
    const files: string[] = [];
    let offset = 0;
    while (true) {
      const { data, error } = await this.client.storage
        .from(STORAGE_BUCKET)
        .list(prefix, { limit: STORAGE_PAGE_SIZE, offset });
      if (error) throw new Error(`Could not list Storage prefix ${prefix}: ${error.message}`);
      const entries = data ?? [];
      for (const entry of entries) {
        const entryPath = `${prefix}/${entry.name}`;
        if (entry.id === null || entry.metadata === null) {
          files.push(...await this.storageFiles(entryPath));
        } else {
          files.push(entryPath);
        }
      }
      if (entries.length < STORAGE_PAGE_SIZE) break;
      offset += entries.length;
    }
    return files;
  }

  async deleteStoragePrefix(prefix: string): Promise<void> {
    const files = await this.storageFiles(prefix);
    for (let index = 0; index < files.length; index += STORAGE_DELETE_BATCH_SIZE) {
      const { error } = await this.client.storage
        .from(STORAGE_BUCKET)
        .remove(files.slice(index, index + STORAGE_DELETE_BATCH_SIZE));
      if (error) throw new Error(`Could not delete Storage prefix ${prefix}: ${error.message}`);
    }
  }

  async queueGenerationJob(
    part: PartRecord,
    jobType: 'validate_cad' | 'export_cad' | 'export_mesh',
    sourceSha256: string | null,
  ): Promise<string> {
    const { data, error } = await this.client
      .from('generation_jobs')
      .insert({
        project_id: part.project_id,
        part_id: part.id,
        type: jobType,
        status: 'queued',
        source_sha256: sourceSha256,
      })
      .select('id')
      .single();
    if (error || !data?.id) {
      throw new Error(`Could not queue ${jobType} job: ${error?.message ?? 'missing job id'}`);
    }
    return String(data.id);
  }

  async queueStandaloneIndexJob(
    projectId: string,
    jobType: 'build_index' | 'test_getter',
    requestText: string | null = null,
  ): Promise<{ id: string; status: string }> {
    const { data, error } = await this.client
      .from('index_jobs')
      .insert({
        project_id: projectId,
        type: jobType,
        request_text: requestText,
        status: 'queued',
      })
      .select('id, status')
      .single();
    if (!error && data?.id) return { id: String(data.id), status: String(data.status) };
    if (jobType === 'build_index' && error?.code === '23505') {
      const { data: existing, error: lookupError } = await this.client
        .from('index_jobs')
        .select('id, status')
        .eq('project_id', projectId)
        .eq('type', 'build_index')
        .in('status', ['queued', 'running'])
        .order('created_at', { ascending: true })
        .limit(1)
        .maybeSingle();
      if (!lookupError && existing?.id) {
        return { id: String(existing.id), status: String(existing.status) };
      }
      throw new Error(`Could not resolve active index job: ${lookupError?.message ?? 'missing job'}`);
    }
    throw new Error(`Could not queue ${jobType} job: ${error?.message ?? 'missing job id'}`);
  }

  async indexJobInProject(projectId: string, jobId: string): Promise<Record<string, unknown>> {
    const { data, error } = await this.client
      .from('index_jobs')
      .select('id, project_id, type, request_text, status, result, error_message, created_at, started_at, completed_at')
      .eq('project_id', projectId)
      .eq('id', jobId)
      .maybeSingle();
    if (error) throw new Error(`Could not inspect index job: ${error.message}`);
    if (!data) throw new WorkflowError('INDEX_JOB_NOT_FOUND', `No index job was found with id "${jobId}".`);
    return data;
  }

  async queueProjectPlanningJob(
    projectId: string,
    requestText: string,
    options: { autoPublish?: boolean; assemblyId?: string } = {},
  ): Promise<{ id: string; status: string }> {
    const { data, error } = await this.client
      .from('project_planning_jobs')
      .insert({
        project_id: projectId,
        request_text: requestText,
        status: 'queued',
        auto_publish: options.autoPublish ?? false,
        target_assembly_id: options.assemblyId ?? null,
      })
      .select('id, status')
      .single();
    if (error || !data?.id) {
      throw new Error(`Could not queue project planning job: ${error?.message ?? 'missing job id'}`);
    }
    return { id: String(data.id), status: String(data.status) };
  }

  async projectPlanningJobInProject(projectId: string, jobId: string): Promise<Record<string, unknown>> {
    const { data, error } = await this.client
      .from('project_planning_jobs')
      .select('id, project_id, request_text, status, auto_publish, target_assembly_id, project_plan, assembly_spec, error_code, error_message, error_details, created_at, started_at, completed_at')
      .eq('project_id', projectId)
      .eq('id', jobId)
      .maybeSingle();
    if (error) throw new Error(`Could not inspect project planning job: ${error.message}`);
    if (!data) {
      throw new WorkflowError(
        'PROJECT_PLANNING_JOB_NOT_FOUND',
        `No project planning job was found with id "${jobId}".`,
      );
    }
    return data;
  }

  // Unlike assemblyPublishJobInProject, this must not throw NOT_FOUND -- a
  // plan with auto_publish=false legitimately never has a matching publish
  // job, and getProjectPlan uses this to "peek" at the auto-publish outcome
  // without a separate get_assembly_publish_job round trip.
  async publishJobForDesignRequest(
    projectId: string,
    designRequestId: string,
  ): Promise<Record<string, unknown> | null> {
    const { data, error } = await this.client
      .from('assembly_publish_jobs')
      .select('id, project_id, design_request_id, target_assembly_id, status, assembly_revision, error_code, error_message, error_details, created_at, started_at, completed_at')
      .eq('project_id', projectId)
      .eq('design_request_id', designRequestId)
      .maybeSingle();
    if (error) throw new Error(`Could not inspect assembly publish job: ${error.message}`);
    return data ?? null;
  }

  async queueAssemblyPublishJob(
    projectId: string,
    designRequestId: string,
    assemblyId?: string,
  ): Promise<{ id: string; status: string }> {
    const { data, error } = await this.client
      .from('assembly_publish_jobs')
      .insert({
        project_id: projectId,
        design_request_id: designRequestId,
        target_assembly_id: assemblyId ?? null,
        status: 'queued',
      })
      .select('id, status')
      .single();
    if (error?.code === '23505') {
      throw new WorkflowError(
        'ASSEMBLY_PUBLISH_ALREADY_IN_PROGRESS',
        'A publish job is already queued or running for this assembly or design request.',
      );
    }
    if (error || !data?.id) {
      throw new Error(`Could not queue assembly publish job: ${error?.message ?? 'missing job id'}`);
    }
    return { id: String(data.id), status: String(data.status) };
  }

  async assemblyPublishJobInProject(projectId: string, jobId: string): Promise<Record<string, unknown>> {
    const { data, error } = await this.client
      .from('assembly_publish_jobs')
      .select('id, project_id, design_request_id, target_assembly_id, status, assembly_revision, error_code, error_message, error_details, created_at, started_at, completed_at')
      .eq('project_id', projectId)
      .eq('id', jobId)
      .maybeSingle();
    if (error) throw new Error(`Could not inspect assembly publish job: ${error.message}`);
    if (!data) {
      throw new WorkflowError(
        'ASSEMBLY_PUBLISH_JOB_NOT_FOUND',
        `No assembly publish job was found with id "${jobId}".`,
      );
    }
    return data;
  }

  async assemblyRevisions(projectId: string, assemblyId: string): Promise<Record<string, unknown>[]> {
    const { data: assembly, error: assemblyError } = await this.client
      .from('assemblies')
      .select('id, project_id, head_revision, created_at')
      .eq('project_id', projectId)
      .eq('id', assemblyId)
      .maybeSingle();
    if (assemblyError) throw new Error(`Could not inspect assembly: ${assemblyError.message}`);
    if (!assembly) {
      throw new WorkflowError('ASSEMBLY_NOT_FOUND', `No assembly was found with id "${assemblyId}".`);
    }
    const { data, error } = await this.client
      .from('assembly_revisions')
      .select('id, assembly_id, revision, parent_revision, design_request_id, schema_version, definition_digest, definition_json, created_at')
      .eq('assembly_id', assemblyId)
      .order('revision', { ascending: true });
    if (error) throw new Error(`Could not list assembly revisions: ${error.message}`);
    return data ?? [];
  }

  async assembliesInProject(projectId: string): Promise<Record<string, unknown>[]> {
    const { data, error } = await this.client
      .from('assemblies')
      .select('id, project_id, head_revision, created_at')
      .eq('project_id', projectId)
      .order('created_at', { ascending: true });
    if (error) throw new Error(`Could not list assemblies: ${error.message}`);
    return data ?? [];
  }

  async hasCadParts(projectId: string): Promise<boolean> {
    const { data, error } = await this.client
      .from('parts')
      .select('id')
      .eq('project_id', projectId)
      .eq('part_type', 'cad')
      .limit(1);
    if (error) throw new Error(`Could not inspect project CAD parts: ${error.message}`);
    return Boolean(data?.length);
  }

  async hasRunningGenerationJobs(projectId: string, partId?: string): Promise<boolean> {
    let query = this.client
      .from('generation_jobs')
      .select('id')
      .eq('project_id', projectId)
      .eq('status', 'running')
      .limit(1);
    if (partId) query = query.eq('part_id', partId);
    const { data, error } = await query;
    if (error) throw new Error(`Could not inspect running export jobs: ${error.message}`);
    return Boolean(data?.length);
  }

  async cancelQueuedGenerationJobs(projectId: string, partId?: string): Promise<void> {
    let query = this.client
      .from('generation_jobs')
      .update({ status: 'cancelled' })
      .eq('project_id', projectId)
      .eq('status', 'queued');
    if (partId) query = query.eq('part_id', partId);
    const { error } = await query;
    if (error) throw new Error(`Could not cancel queued export jobs: ${error.message}`);
  }

  async hasRunningIndexJobs(projectId: string): Promise<boolean> {
    const { data, error } = await this.client
      .from('index_jobs')
      .select('id')
      .eq('project_id', projectId)
      .eq('status', 'running')
      .limit(1);
    if (error) throw new Error(`Could not inspect running index jobs: ${error.message}`);
    return Boolean(data?.length);
  }

  async cancelQueuedIndexJobs(projectId: string): Promise<void> {
    const { error } = await this.client
      .from('index_jobs')
      .update({ status: 'cancelled', completed_at: new Date().toISOString() })
      .eq('project_id', projectId)
      .eq('status', 'queued');
    if (error) throw new Error(`Could not cancel queued index jobs: ${error.message}`);
  }

  async hasRunningEditJobs(projectId: string, partId?: string): Promise<boolean> {
    let query = this.client
      .from('edit_jobs')
      .select('id')
      .eq('project_id', projectId)
      .eq('status', 'running')
      .limit(1);
    if (partId) query = query.or(`resolved_part_id.is.null,resolved_part_id.eq.${partId}`);
    const { data, error } = await query;
    if (error) throw new Error(`Could not inspect running edit jobs: ${error.message}`);
    return Boolean(data?.length);
  }

  async cancelQueuedEditJobs(projectId: string, partId?: string): Promise<void> {
    let query = this.client
      .from('edit_jobs')
      .update({
        status: 'cancelled',
        state: 'cancelled',
        error_code: 'CANCELLED_FOR_DELETION',
        error_message: 'The edit was cancelled because its project or part was deleted.',
        completed_at: new Date().toISOString(),
      })
      .eq('project_id', projectId)
      .eq('status', 'queued');
    if (partId) query = query.or(`resolved_part_id.is.null,resolved_part_id.eq.${partId}`);
    const { error } = await query;
    if (error) throw new Error(`Could not cancel queued edit jobs: ${error.message}`);
  }
}
