import assert from 'node:assert/strict';
import test from 'node:test';
import { loadMeshSystemPrompt } from '../src/prompt-loader';

test('loads and caches the Nest-owned mesh prompt', () => {
  const first = loadMeshSystemPrompt();
  const second = loadMeshSystemPrompt();

  assert.match(first, /Blender Mesh Model Generation/);
  assert.strictEqual(first, second);
  assert.doesNotMatch(first, /CAD Goal Planning|Registered CAD Tools/);
});
