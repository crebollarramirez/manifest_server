import assert from 'node:assert/strict';
import test from 'node:test';
import { CadEditSubmissionSchema } from '../src/contracts';

const PART_ID = '11111111-1111-4111-8111-111111111111';
const PROJECT_ID = '22222222-2222-4222-8222-222222222222';

test('submission accepts request text with optional bounded conversation', () => {
  const parsed = CadEditSubmissionSchema.parse({
    project_id: PROJECT_ID,
    part_id: PART_ID,
    request_text: 'Add a mounting hole',
    messages: [{ role: 'user', content: 'Add a mounting hole' }],
  });
  assert.equal(parsed.request_text, 'Add a mounting hole');
});

test('submission rejects mismatched final conversation message and unknown keys', () => {
  assert.throws(() =>
    CadEditSubmissionSchema.parse({
      project_id: PROJECT_ID,
      request_text: 'Add a mounting hole',
      messages: [{ role: 'user', content: 'Make a bracket' }],
    }),
  );
  assert.throws(() =>
    CadEditSubmissionSchema.parse({
      project_id: PROJECT_ID,
      request_text: 'Add a mounting hole',
      arbitrary: true,
    }),
  );
  assert.throws(() =>
    CadEditSubmissionSchema.parse({
      project_id: PROJECT_ID,
      request_text: 'ninth message',
      messages: Array.from({ length: 9 }, (_, index) => ({
        role: 'user',
        content: index === 8 ? 'ninth message' : `message ${index}`,
      })),
    }),
  );
});

test('submission preserves request whitespace at the Nest boundary', () => {
  const request = '  Make the plate 20 mm wider.  ';
  const parsed = CadEditSubmissionSchema.parse({
    project_id: PROJECT_ID,
    request_text: request,
    messages: [{ role: 'user', content: request }],
  });
  assert.equal(parsed.request_text, request);
  assert.equal(parsed.messages?.[0]?.content, request);
});
