import { createHash } from 'node:crypto';
import { Inject, Injectable } from '@nestjs/common';
import { CadAgentRepository } from './cad-agent.repository';
import {
  ActionError,
  CadAgentAction,
  CadAgentActionSchema,
  PartRecord,
  ProjectRecord,
} from './contracts';
import { MeshGenerationService } from './mesh-generation.service';
import { publicActionJob } from './public-job';

const CAD_MODEL_RUNTIME_IMPORT = 'from cadquery_runtime import cad_part, cq, dataclass';

// Kept in sync by hand with workers/agent_3d/tools/part/part_tools.py's
// _skeleton_source() — a new part should look exactly like one CreateCadPartTool
// would have created, so both paths produce the same indexable, feature-free
// starting point (empty ModelParams, trivial build_model).
const CAD_MODEL_SKELETON_SOURCE = `${CAD_MODEL_RUNTIME_IMPORT}

@dataclass(frozen=True)
class ModelParams:
    pass


def build_model(params: ModelParams):
    return cq.Workplane("XY")
`;

function projectPath(projectId: string, ...parts: string[]): string {
  return [projectId, ...parts].join('/');
}

function partSourcePath(
  projectId: string,
  partType: 'cad' | 'mesh',
  partId: string,
  ...parts: string[]
): string {
  return projectPath(projectId, 'parts', partType, partId, ...parts);
}

function partExportPath(projectId: string, partId: string): string {
  return projectPath(projectId, 'exports', partId);
}

@Injectable()
export class CadActionsService {
  constructor(
    @Inject(CadAgentRepository) private readonly repository: CadAgentRepository,
    @Inject(MeshGenerationService) private readonly mesh: MeshGenerationService,
  ) {}

  async execute(raw: unknown): Promise<Record<string, unknown>> {
    const parsed = CadAgentActionSchema.safeParse(raw);
    if (!parsed.success) {
      throw new ActionError(400, parsed.error.issues[0]?.message ?? 'Invalid action request.');
    }
    const action = parsed.data;
    switch (action.action) {
      case 'create_project': return this.createProject(action.project_name);
      case 'create_part': return this.createPart(action);
      case 'link_project': return this.linkProject(action.project_id);
      case 'link_part': return this.linkPart(action.project_id, action.part_id);
      case 'list_projects': return this.listProjects();
      case 'list_parts': return this.listParts(action.project_id);
      case 'export_part': return this.exportPart(action.part_id);
      case 'validate_part': return this.validatePart(action.part_id);
      case 'index_project': return this.indexProject(action.project_id);
      case 'test_index': return this.testIndex(action.project_id, action.request_text);
      case 'get_index_job': return this.getIndexJob(action.project_id, action.job_id);
      case 'get_edit_job': return this.getEditJob(action.job_id, action.after_sequence ?? 0);
      case 'delete_project': return this.deleteProject(action.project_id);
      case 'delete_part': return this.deletePart(action.project_id, action.part_id);
    }
  }

  private async createProject(projectName: string): Promise<Record<string, unknown>> {
    const project = await this.repository.createProject(projectName);
    return {
      message: `Created and linked project "${project.project_name}" (id=${project.id}).`,
      status: 'created',
      project,
    };
  }

  private async linkProject(projectId: string): Promise<Record<string, unknown>> {
    const project = await this.repository.project(projectId) as ProjectRecord;
    return {
      message: `Linked project "${project.project_name}" (id=${project.id}).`,
      status: 'linked',
      project,
    };
  }

  private async listProjects(): Promise<Record<string, unknown>> {
    const projects = await this.repository.projects();
    return {
      message: projects.length
        ? `Projects:\n${projects.map((project) => `- ${project.project_name} id=${project.id}`).join('\n')}`
        : 'No projects found.',
      status: 'listed',
      projects,
    };
  }

  private async createPart(
    action: Extract<CadAgentAction, { action: 'create_part' }>,
  ): Promise<Record<string, unknown>> {
    const part = await this.repository.createPart({
      projectId: action.project_id,
      partName: action.part_name,
      partType: action.part_type,
    });
    const prefix = partSourcePath(part.project_id, part.part_type, part.id);
    try {
      const initialSource = part.part_type === 'cad'
        ? CAD_MODEL_SKELETON_SOURCE
        : this.mesh.starterSource();
      await this.repository.uploadText(
        `${prefix}/model.py`,
        initialSource,
        'text/x-python',
        false,
      );
      await this.repository.uploadText(`${prefix}/params.json`, '{}\n', 'application/json', false);
    } catch (error) {
      try {
        await this.repository.deleteStoragePrefix(prefix);
      } finally {
        await this.repository.deletePart(part.id);
      }
      throw error;
    }

    let indexJob: { id: string; status: string } | null = null;
    const warnings: string[] = [];
    if (part.part_type === 'cad') {
      try {
        indexJob = await this.repository.queueStandaloneIndexJob(part.project_id, 'build_index');
      } catch {
        warnings.push(`Automatic indexing could not be queued; run /index ${part.project_id}.`);
      }
    }
    return {
      message: `Created and linked ${part.part_type} part "${part.part_name}" (id=${part.id}).`,
      status: 'created',
      part,
      ...(part.part_type === 'cad' ? {
        index_job_id: indexJob?.id ?? null,
        index_status: indexJob?.status ?? 'not_queued',
        index_job_type: 'build_index',
        warnings,
      } : {}),
    };
  }

  private async linkPart(projectId: string, partId: string): Promise<Record<string, unknown>> {
    const part = await this.repository.part(projectId, partId) as unknown as PartRecord;
    return {
      message: `Linked ${part.part_type} part "${part.part_name}" (id=${part.id}).`,
      status: 'linked',
      part,
    };
  }

  private async listParts(projectId: string): Promise<Record<string, unknown>> {
    const project = await this.repository.project(projectId) as ProjectRecord;
    const parts = await this.repository.parts(projectId);
    return {
      message: parts.length
        ? `Parts in "${project.project_name}":\n${parts.map((part) =>
          `- ${part.part_name} [${part.part_type}] id=${part.id}`).join('\n')}`
        : `No parts found in project "${project.project_name}".`,
      status: 'listed',
      project,
      parts,
    };
  }

  private async currentCadSourceSha256(part: PartRecord): Promise<string> {
    const source = await this.repository.readText(
      partSourcePath(part.project_id, 'cad', part.id, 'model.py'),
    );
    return createHash('sha256').update(source).digest('hex');
  }

  private async exportPart(partId: string): Promise<Record<string, unknown>> {
    const part = await this.repository.partById(partId);
    const jobType = part.part_type === 'cad' ? 'export_cad' : 'export_mesh';
    const sourceSha256 = part.part_type === 'cad'
      ? await this.currentCadSourceSha256(part)
      : null;
    const jobId = await this.repository.queueGenerationJob(part, jobType, sourceSha256);
    return {
      message: `Queued ${jobType} for ${part.part_type} part "${part.part_name}". Job: ${jobId}`,
      status: 'queued',
      job_type: jobType,
      project_id: part.project_id,
      part_id: part.id,
      job_id: jobId,
    };
  }

  private async validatePart(partId: string): Promise<Record<string, unknown>> {
    const part = await this.repository.partById(partId);
    if (part.part_type !== 'cad') throw new ActionError(400, 'Only CAD parts support validation.');
    const sourceSha256 = await this.currentCadSourceSha256(part);
    const jobId = await this.repository.queueGenerationJob(part, 'validate_cad', sourceSha256);
    return {
      message: `Queued validate_cad for cad part "${part.part_name}". Job: ${jobId}`,
      status: 'validating',
      job_type: 'validate_cad',
      project_id: part.project_id,
      part_id: part.id,
      job_id: jobId,
    };
  }

  private async requireIndexableProject(projectId: string): Promise<ProjectRecord> {
    const project = await this.repository.project(projectId) as ProjectRecord;
    if (!await this.repository.hasCadParts(projectId)) {
      throw new ActionError(
        400,
        `Project "${project.project_name}" does not contain any CAD parts.`,
      );
    }
    return project;
  }

  private async indexProject(projectId: string): Promise<Record<string, unknown>> {
    const project = await this.requireIndexableProject(projectId);
    const job = await this.repository.queueStandaloneIndexJob(projectId, 'build_index');
    return {
      message: `Index job for project "${project.project_name}" is ${job.status}. Job: ${job.id}`,
      status: job.status,
      job_type: 'build_index',
      project_id: projectId,
      job_id: job.id,
    };
  }

  private async testIndex(projectId: string, requestText: string): Promise<Record<string, unknown>> {
    const project = await this.repository.project(projectId) as ProjectRecord;
    const job = await this.repository.queueStandaloneIndexJob(projectId, 'test_getter', requestText);
    return {
      message: `Queued index Getter test for project "${project.project_name}". Job: ${job.id}`,
      status: job.status,
      job_type: 'test_getter',
      project_id: projectId,
      job_id: job.id,
    };
  }

  private async getIndexJob(projectId: string, jobId: string): Promise<Record<string, unknown>> {
    const job = await this.repository.indexJobInProject(projectId, jobId);
    return {
      message: `Index job ${jobId} is ${job.status}.`,
      status: job.status,
      job,
    };
  }

  private async getEditJob(jobId: string, afterSequence: number): Promise<Record<string, unknown>> {
    const job = await this.repository.editJob(jobId);
    const events = await this.repository.events(jobId, afterSequence);
    return {
      message: `CAD edit job ${jobId} is ${job.status} (${job.state}).`,
      status: job.status,
      job: publicActionJob(job),
      events,
    };
  }

  private async prepareForDeletion(projectId: string, partId?: string): Promise<void> {
    if (!partId && await this.repository.hasRunningIndexJobs(projectId)) {
      throw new ActionError(409, 'Deletion is blocked while an index job is running. Try again after it finishes.');
    }
    if (await this.repository.hasRunningEditJobs(projectId, partId)) {
      throw new ActionError(409, 'Deletion is blocked while a CAD edit is running. Try again after it finishes.');
    }
    if (await this.repository.hasRunningGenerationJobs(projectId, partId)) {
      throw new ActionError(409, 'Deletion is blocked while an export job is running. Try again after it finishes.');
    }
    if (!partId) await this.repository.cancelQueuedIndexJobs(projectId);
    await this.repository.cancelQueuedEditJobs(projectId, partId);
    await this.repository.cancelQueuedGenerationJobs(projectId, partId);
    if (!partId && await this.repository.hasRunningIndexJobs(projectId)) {
      throw new ActionError(409, 'Deletion is blocked because an index job started. Try again after it finishes.');
    }
    if (await this.repository.hasRunningEditJobs(projectId, partId)) {
      throw new ActionError(409, 'Deletion is blocked because a CAD edit started. Try again after it finishes.');
    }
    if (await this.repository.hasRunningGenerationJobs(projectId, partId)) {
      throw new ActionError(409, 'Deletion is blocked because an export job started. Try again after it finishes.');
    }
  }

  private async deleteProject(projectId: string): Promise<Record<string, unknown>> {
    const project = await this.repository.project(projectId) as ProjectRecord;
    await this.prepareForDeletion(projectId);
    await this.repository.deleteStoragePrefix(projectId);
    await this.repository.deleteProject(projectId);
    return {
      message: `Deleted project "${project.project_name}" (id=${project.id}) and all of its parts.`,
      status: 'deleted',
      project,
    };
  }

  private async deletePart(projectId: string, partId: string): Promise<Record<string, unknown>> {
    const part = await this.repository.part(projectId, partId) as unknown as PartRecord;
    await this.prepareForDeletion(projectId, part.id);
    await this.repository.deleteStoragePrefix(partSourcePath(projectId, part.part_type, part.id));
    await this.repository.deleteStoragePrefix(partExportPath(projectId, part.id));
    await this.repository.deleteStoragePrefix(projectPath(projectId, 'candidates', 'cad', part.id));
    await this.repository.deletePart(part.id);
    return {
      message: `Deleted ${part.part_type} part "${part.part_name}" (id=${part.id}).`,
      status: 'deleted',
      part,
    };
  }

}
