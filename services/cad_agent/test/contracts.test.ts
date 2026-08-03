import assert from 'node:assert/strict';
import test from 'node:test';
import {
  CadEditSubmissionSchema,
  InitialDesignToolPlanV2Schema,
  ToolPlanSchema,
} from '../src/contracts';

const PART_ID = '11111111-1111-4111-8111-111111111111';
const PROJECT_ID = '22222222-2222-4222-8222-222222222222';
const HASH = 'a'.repeat(64);

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

test('tool plan supports composed add, modify, and delete operations', () => {
  const plan = ToolPlanSchema.parse({
    schema_version: 1,
    summary: 'Add a slot and remove an owned helper.',
    target_part_id: PART_ID,
    base_source_sha256: HASH,
    operations: [
      {
        tool: 'add_model_parameter',
        name: 'slot_width',
        field_source: 'slot_width: float = 3.0',
      },
      {
        tool: 'replace_build_model_body',
        target_id: `${PART_ID}:build_model_body:build_model`,
        target_fingerprint: HASH,
        replacement_source: 'return make_slot(params)',
      },
      {
        tool: 'replace_cad_feature_body',
        semantic_id: 'body',
        target_fingerprint: HASH,
        replacement_source: 'return make_body(params)',
      },
      {
        tool: 'delete_private_helper',
        target_id: `${PART_ID}:owned_private_helper:_old`,
        target_fingerprint: HASH,
      },
    ],
  });
  assert.equal(plan.operations.length, 4);
});

test('tool plan v2 requires explicit dependency impact review', () => {
  const plan = ToolPlanSchema.parse({
    schema_version: 2,
    summary: 'Increase the holder width and review dependent legs.',
    target_part_id: PART_ID,
    base_source_sha256: HASH,
    operations: [
      {
        tool: 'replace_parameter_field',
        target_id: `${PART_ID}:model_parameter:holder_width_mm`,
        target_fingerprint: HASH,
        replacement_source: 'holder_width_mm: float = 120.0',
      },
    ],
    impact_review: [
      {
        semantic_id: 'soap_holder_support_legs',
        decision: 'verified_compatible',
        reason: 'Leg offsets already derive from holder_width_mm.',
      },
    ],
  });

  assert.equal(plan.schema_version, 2);
  assert.equal(plan.impact_review[0]?.decision, 'verified_compatible');
  assert.equal(
    ToolPlanSchema.safeParse({
      ...plan,
      impact_review: undefined,
    }).success,
    false,
  );
});

test('tool plan supports an evidence-bearing no-change confirmation only by itself', () => {
  const confirmation = {
    tool: 'confirm_no_change',
    reason: 'The accepted feature already contains the requested holes.',
    evidence: [
      {
        semantic_id: 'soap_drain_holes',
        target_fingerprint: HASH,
        reason: 'The feature cuts three centered cylinders through the tray base.',
      },
    ],
  };
  const plan = ToolPlanSchema.parse({
    schema_version: 2,
    summary: 'Confirmed the requested drainage is already present.',
    target_part_id: PART_ID,
    base_source_sha256: HASH,
    operations: [confirmation],
    impact_review: [],
  });
  assert.equal(plan.operations[0]?.tool, 'confirm_no_change');

  assert.equal(
    ToolPlanSchema.safeParse({
      ...plan,
      operations: [
        confirmation,
        {
          tool: 'add_model_parameter',
          name: 'hole_count',
          field_source: 'hole_count: int = 3',
        },
      ],
    }).success,
    false,
  );
  assert.equal(
    ToolPlanSchema.safeParse({
      ...plan,
      impact_review: [
        {
          semantic_id: 'soap_drain_holes',
          decision: 'verified_compatible',
          reason: 'Already present.',
        },
      ],
    }).success,
    false,
  );
});

test('initial-design v2 schema permits only one full-model write', () => {
  const valid = InitialDesignToolPlanV2Schema.parse({
    schema_version: 2,
    summary: 'Repair the initial soap-holder candidate.',
    target_part_id: PART_ID,
    base_source_sha256: HASH,
    operations: [
      {
        tool: 'write_initial_model',
        model_body: 'class ModelParams: pass',
      },
    ],
    impact_review: [],
  });
  assert.equal(valid.operations[0].tool, 'write_initial_model');

  assert.equal(
    InitialDesignToolPlanV2Schema.safeParse({
      ...valid,
      operations: [
        {
          tool: 'replace_build_model_body',
          target_id: `${PART_ID}:build_model_body:build_model`,
          target_fingerprint: HASH,
          replacement_source: 'return body',
        },
      ],
    }).success,
    false,
  );
});

test('tool plan rejects unknown versions, tools, and malformed fingerprints', () => {
  for (const payload of [
    {
      schema_version: 3,
      summary: 'Wrong version',
      target_part_id: PART_ID,
      base_source_sha256: HASH,
      operations: [{ tool: 'write_initial_model', model_body: 'pass' }],
    },
    {
      schema_version: 1,
      summary: 'Unknown tool',
      target_part_id: PART_ID,
      base_source_sha256: HASH,
      operations: [{ tool: 'shell', command: 'echo unsafe' }],
    },
    {
      schema_version: 1,
      summary: 'Bad fingerprint',
      target_part_id: PART_ID,
      base_source_sha256: HASH,
      operations: [
        {
          tool: 'delete_cad_feature',
          target_id: `${PART_ID}:cad_feature:body`,
          target_fingerprint: 'stale',
        },
      ],
    },
  ]) {
    assert.equal(ToolPlanSchema.safeParse(payload).success, false);
  }
});
