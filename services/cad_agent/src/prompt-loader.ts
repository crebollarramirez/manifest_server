import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const promptCache = new Map<string, string>();

function readPrompt(path: string): string {
  const cached = promptCache.get(path);
  if (cached !== undefined) return cached;

  const prompt = readFileSync(path, 'utf8').trim();
  if (!prompt) throw new Error(`Prompt file is empty: ${path}`);
  promptCache.set(path, prompt);
  return prompt;
}

export function loadMeshSystemPrompt(): string {
  const configuredPath = process.env.MESH_SYSTEM_PROMPT_PATH?.trim();
  return readPrompt(configuredPath || resolve(__dirname, '../prompts/mesh-system.md'));
}
