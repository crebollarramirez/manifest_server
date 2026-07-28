import assert from 'node:assert/strict';
import test from 'node:test';
import {
  loadCadSystemPrompt,
  loadCadReasoningPrompt,
  loadServicePrompt,
} from '../src/prompt-loader';
import { shouldUseRepairPrompt } from '../src/reasoner.service';

const TOOL_NAMES = [
  'write_initial_model',
  'replace_parameter_field',
  'update_cad_part_metadata',
  'replace_cad_feature_body',
  'replace_function_body',
  'add_model_parameter',
  'add_private_helper',
  'add_cad_feature',
  'replace_build_model_body',
  'delete_model_parameter',
  'delete_private_helper',
  'delete_cad_feature',
];

test('loads separated style, tool, initialization, edit, and repair prompts from Markdown', () => {
  const contract = loadCadSystemPrompt();
  const toolPlan = loadServicePrompt('tool-plan');
  const initialization = loadServicePrompt('initialization');
  const editPlan = loadServicePrompt('edit-plan');
  const repair = loadServicePrompt('repair');

  assert.match(contract, /CadQuery Source Style Contract/);
  assert.doesNotMatch(contract, /ToolPlan|Registered CAD Tools|original request/);
  for (const tool of TOOL_NAMES) assert.match(toolPlan, new RegExp(tool));
  assert.doesNotMatch(toolPlan, /existing_features.*authoritative|planning_feedback/);
  assert.match(initialization, /CAD Model Initialization/);
  assert.match(initialization, /write_initial_model/);
  assert.doesNotMatch(initialization, /existing_features.*authoritative/);
  assert.match(editPlan, /CAD Edit Plan Formation/);
  assert.match(editPlan, /existing_features/);
  assert.doesNotMatch(editPlan, /Arguments:/);
  assert.match(repair, /CAD Plan Repair/);
  assert.match(repair, /validator diagnostics/);
});

test('composes workflow-specific normal and repair instructions', () => {
  const initial = loadCadReasoningPrompt({ workflowMode: 'initial_design' });
  const initialRepair = loadCadReasoningPrompt({
    workflowMode: 'initial_design',
    repair: true,
  });
  const edit = loadCadReasoningPrompt({ workflowMode: 'edit' });
  const editRepair = loadCadReasoningPrompt({
    workflowMode: 'edit',
    repair: true,
  });

  assert.equal(
    initial,
    [
      loadCadSystemPrompt(),
      loadServicePrompt('tool-plan'),
      loadServicePrompt('initialization'),
    ].join('\n\n'),
  );
  assert.equal(
    edit,
    [
      loadCadSystemPrompt(),
      loadServicePrompt('tool-plan'),
      loadServicePrompt('edit-plan'),
    ].join('\n\n'),
  );
  assert.equal(initialRepair, `${initial}\n\n${loadServicePrompt('repair')}`);
  assert.equal(editRepair, `${edit}\n\n${loadServicePrompt('repair')}`);
  assert.match(initial, /# CAD Model Initialization/);
  assert.doesNotMatch(initial, /# CAD Edit Plan Formation/);
  assert.match(edit, /# CAD Edit Plan Formation/);
  assert.doesNotMatch(edit, /# CAD Model Initialization/);
  assert.doesNotMatch(initial, /# CAD Plan Repair/);
  assert.match(initialRepair, /# CAD Plan Repair/);
});

test('selects repair composition for validation and tool-preflight feedback', () => {
  assert.equal(shouldUseRepairPrompt({}), false);
  assert.equal(shouldUseRepairPrompt({ planningFeedback: {} }), false);
  assert.equal(
    shouldUseRepairPrompt({ validation: { status: 'failed' } }),
    true,
  );
  assert.equal(
    shouldUseRepairPrompt({
      planningFeedback: { error_code: 'NEW_FEATURE_NOT_ASSEMBLED' },
    }),
    true,
  );
});

test('returns cached prompt fragments after the first read', () => {
  assert.strictEqual(
    loadServicePrompt('initialization'),
    loadServicePrompt('initialization'),
  );
});
