import { Inject, Injectable } from '@nestjs/common';
import OpenAI from 'openai';
import { CadAgentRepository } from './cad-agent.repository';
import type { PartRecord } from './contracts';
import { DEFAULT_MESH_MODEL_BODY } from './mesh-model-template';
import { loadMeshSystemPrompt } from './prompt-loader';

const DEFAULT_OPENAI_MODEL = 'gpt-5.4-mini';
const MAX_HISTORY_MESSAGES = 8;
const MESH_MODEL_RUNTIME_IMPORT =
  'from blender_runtime import bpy, bmesh, dataclass, Vector, Matrix, Euler, mesh_part, mm, get_or_create_collection, link_object';

const RESPONSE_SCHEMA = {
  name: 'mesh_model_agent_response',
  strict: true,
  schema: {
    type: 'object',
    additionalProperties: false,
    properties: {
      generated_code: {
        type: 'string',
        description: 'The complete AI-owned Python model-generation code body.',
      },
    },
    required: ['generated_code'],
  },
} as const;

type ChatMessage = { role: 'user' | 'assistant'; content: string };

function stripMarkdownFence(code: string): string {
  const trimmed = code.trim();
  const match = trimmed.match(/^```(?:python)?\s*\n([\s\S]*?)\n```$/i);
  return match ? match[1].trim() : trimmed;
}

export function composeMeshModelSource(modelBody: string): string {
  const body = stripMarkdownFence(modelBody)
    .split('\n')
    .filter((line) => line.trim() !== MESH_MODEL_RUNTIME_IMPORT)
    .join('\n')
    .trim();
  return `${MESH_MODEL_RUNTIME_IMPORT}\n\n${body}\n`;
}

export function generatedMeshModelBody(responseText: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(responseText);
  } catch {
    throw new Error('OpenAI returned invalid JSON.');
  }
  if (!parsed || typeof parsed !== 'object') throw new Error('AI response must be a JSON object.');
  const code = (parsed as Record<string, unknown>).generated_code;
  if (typeof code !== 'string' || !code.trim()) {
    throw new Error('AI response missing generated_code.');
  }
  if (['OK', 'SUCCESS', 'DONE'].includes(code.trim().toUpperCase())) {
    throw new Error('AI returned a status value instead of Python code.');
  }
  return code;
}

@Injectable()
export class MeshGenerationService {
  private readonly client: OpenAI;
  private readonly model: string;
  private readonly instructions: string;

  constructor(@Inject(CadAgentRepository) private readonly repository: CadAgentRepository) {
    const apiKey = process.env.OPENAI_API_KEY?.trim();
    if (!apiKey) throw new Error('OPENAI_API_KEY is required.');
    this.client = new OpenAI({ apiKey });
    this.model = process.env.OPENAI_MODEL?.trim() || DEFAULT_OPENAI_MODEL;
    this.instructions = loadMeshSystemPrompt();
  }

  starterSource(): string {
    return composeMeshModelSource(DEFAULT_MESH_MODEL_BODY);
  }

  async generate(part: PartRecord, messages: ChatMessage[]): Promise<string> {
    const modelPath = `${part.project_id}/parts/mesh/${part.id}/model.py`;
    const currentModelSource = await this.repository.readText(modelPath);
    const latestUserMessage = messages.at(-1);
    if (!latestUserMessage || latestUserMessage.role !== 'user') {
      throw new Error('Messages must end with a user message.');
    }
    const input = messages
      .slice(0, -1)
      .slice(-(MAX_HISTORY_MESSAGES - 1))
      .map((message) => ({
        type: 'message' as const,
        role: message.role,
        content: message.content,
      }));
    input.push({
      type: 'message',
      role: 'user',
      content:
        `Current state of model.py:\n\`\`\`python\n${currentModelSource}\n\`\`\`\n\n` +
        'Return the complete replacement model-generation body in the `generated_code` response field. ' +
        'It must contain Python source, not a status value such as OK.\n\n' +
        `User request:\n${latestUserMessage.content}`,
    });
    const response = await this.client.responses.create({
      model: this.model,
      instructions: this.instructions,
      input,
      text: { format: { type: 'json_schema', ...RESPONSE_SCHEMA } },
    });
    if (!response.output_text) throw new Error('OpenAI returned an empty response.');
    const generatedSource = composeMeshModelSource(generatedMeshModelBody(response.output_text));
    await this.repository.uploadText(modelPath, generatedSource, 'text/x-python', true);
    return this.repository.queueGenerationJob(part, 'export_mesh', null);
  }
}
