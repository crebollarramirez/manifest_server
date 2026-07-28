import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const promptCache = new Map<string, string>();
export type ServicePromptName =
  | 'tool-plan'
  | 'initialization'
  | 'edit-plan'
  | 'repair';
export type CadReasoningMode = 'initial_design' | 'edit';

function readPrompt(path: string): string {
  const cached = promptCache.get(path);
  if (cached !== undefined) return cached;

  const prompt = readFileSync(path, 'utf8').trim();
  if (!prompt) throw new Error(`Prompt file is empty: ${path}`);
  promptCache.set(path, prompt);
  return prompt;
}

export function loadCadSystemPrompt(): string {
  const configuredPath = process.env.CAD_SYSTEM_PROMPT_PATH?.trim();
  const path =
    configuredPath ||
    resolve(
      __dirname,
      '../../../supabase/functions/cad-agent/CAD_SYSTEM_PROMPT.md',
    );
  return readPrompt(path);
}

export function loadServicePrompt(name: ServicePromptName): string {
  return readPrompt(resolve(__dirname, '../prompts', `${name}.md`));
}

export function loadCadReasoningPrompt(options: {
  workflowMode: CadReasoningMode;
  repair?: boolean;
}): string {
  const prompts = [
    loadCadSystemPrompt(),
    loadServicePrompt('tool-plan'),
    loadServicePrompt(
      options.workflowMode === 'initial_design'
        ? 'initialization'
        : 'edit-plan',
    ),
  ];
  if (options.repair) prompts.push(loadServicePrompt('repair'));
  return prompts.join('\n\n');
}
