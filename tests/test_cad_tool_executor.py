from __future__ import annotations

import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from workers.cad_editor.cad_editor.contracts import WorkflowFailure
from workers.cad_editor.cad_editor.targets import semantic_ids_in_source, source_hash
from workers.cad_editor.cad_editor.tool_contracts import ToolPlan
from workers.cad_editor.cad_editor.tool_executor import (
    CadToolExecutor,
    RUNTIME_IMPORT,
    _annotate_initial_model,
    planner_inventory,
    target_inventory,
)
from tests.test_indexer_smoke import DEPENDENT_SOURCE
from workers.indexer.indexer import IndexGetter, SourceFile, build_project_index


PROJECT_ID = "22222222-2222-4222-8222-222222222222"
PART_ID = "11111111-1111-4111-8111-111111111111"
EDIT_ID = "33333333-3333-4333-8333-333333333333"
CANONICAL_PATH = f"{PROJECT_ID}/parts/cad/{PART_ID}/model.py"

MODEL_BODY = """@dataclass(frozen=True)
class ModelParams:
    width: float = 10.0
    height: float = 20.0

@cad_part(
    semantic_id="body",
    role="primary_body",
    library="cadquery",
    parameters=("width", "height"),
    depends_on=(),
    search_keys=("body",),
)
def make_body(params: ModelParams):
    return cq.Workplane("XY").box(params.width, params.height, 2)

def _double(value):
    return value * 2

def build_model(params: ModelParams):
    return make_body(params)
"""


class FakeRepository:
    def __init__(self, source: str):
        self.files = {CANONICAL_PATH: source}
        self.writes: list[str] = []

    def canonical_source_path(self, project_id: str, part_id: str) -> str:
        return f"{project_id}/parts/cad/{part_id}/model.py"

    def candidate_path(
        self,
        project_id: str,
        part_id: str,
        edit_job_id: str,
        attempt: int,
    ) -> str:
        return (
            f"{project_id}/candidates/cad/{part_id}/{edit_job_id}/"
            f"attempt-{attempt}/model.py"
        )

    def read_text(self, path: str) -> str:
        return self.files[path]

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content
        self.writes.append(path)

    def verify_text_hash(self, path: str, expected_hash: str) -> None:
        if source_hash(self.files[path]) != expected_hash:
            raise AssertionError("hash mismatch")

    def source(self, project_id: str, part_id: str) -> SimpleNamespace:
        path = self.canonical_source_path(project_id, part_id)
        content = self.files[path]
        return SimpleNamespace(
            part_id=part_id,
            part_name="Blank part",
            storage_path=path,
            content=content,
            content_hash=source_hash(content),
        )


def tool_job(source: str, plan: dict, *, workflow_mode: str = "edit") -> dict:
    return {
        "project_id": PROJECT_ID,
        "part_id": PART_ID,
        "edit_job_id": EDIT_ID,
        "attempt": 1,
        "kind": "apply_plan",
        "input": {
            "workflow_mode": workflow_mode,
            "base_storage_path": CANONICAL_PATH,
            "plan": {
                "schema_version": 1,
                "summary": "Apply bounded CAD changes.",
                "target_part_id": PART_ID,
                "base_source_sha256": source_hash(source),
                **plan,
            },
        },
    }


class CadToolExecutorTests(unittest.TestCase):
    def test_blank_context_exposes_explicit_empty_planner_inventory(self):
        repository = FakeRepository(RUNTIME_IMPORT)
        context = CadToolExecutor(repository).prepare_context(
            {
                "project_id": PROJECT_ID,
                "input": {
                    "requested_part_id": PART_ID,
                    "workflow_mode": "initial_design",
                    "request_text": "Create a soap holder.",
                    "messages": [],
                },
            }
        )

        self.assertEqual(context["source_state"], "blank")
        self.assertEqual(context["existing_features"], [])
        self.assertEqual(context["existing_parameters"], [])
        self.assertEqual(context["allowed_dependencies"], [])
        self.assertIsNone(context["build_model_target"])

    def test_validation_repair_context_preserves_previous_plan_and_candidate(self):
        source = _annotate_initial_model(MODEL_BODY)
        repository = FakeRepository(source)
        candidate_path = (
            f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_ID}/"
            "attempt-1/model.py"
        )
        repository.files[candidate_path] = source
        previous_plan = {
            "schema_version": 2,
            "summary": "Create the initial model.",
            "target_part_id": PART_ID,
            "base_source_sha256": source_hash(RUNTIME_IMPORT),
            "operations": [
                {
                    "tool": "write_initial_model",
                    "model_body": MODEL_BODY,
                }
            ],
            "impact_review": [],
        }

        context = CadToolExecutor(repository).prepare_repair_context(
            {
                "project_id": PROJECT_ID,
                "part_id": PART_ID,
                "edit_job_id": EDIT_ID,
                "input": {
                    "candidate_path": candidate_path,
                    "candidate_sha256": source_hash(source),
                    "previous_plan": previous_plan,
                    "repair_source": "validation",
                    "validation": {
                        "status": "failed",
                        "errors": [{"code": "PARAMETER_METADATA_MISMATCH"}],
                    },
                },
            }
        )

        self.assertEqual(context["candidate_source"], source)
        self.assertEqual(context["previous_plan"], previous_plan)
        self.assertEqual(context["repair_source"], "validation")
        self.assertEqual(
            context["validation"]["errors"][0]["code"],
            "PARAMETER_METADATA_MISMATCH",
        )

    def test_validation_repair_context_rejects_missing_previous_plan(self):
        source = _annotate_initial_model(MODEL_BODY)
        repository = FakeRepository(source)

        with self.assertRaises(WorkflowFailure) as raised:
            CadToolExecutor(repository).prepare_repair_context(
                {
                    "project_id": PROJECT_ID,
                    "part_id": PART_ID,
                    "edit_job_id": EDIT_ID,
                    "input": {
                        "candidate_path": (
                            f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_ID}/"
                            "attempt-1/model.py"
                        ),
                        "candidate_sha256": source_hash(source),
                        "repair_source": "validation",
                        "validation": {},
                    },
                }
            )

        self.assertEqual(raised.exception.code, "REPAIR_CONTEXT_INCOMPLETE")

    def test_established_context_exposes_dependency_impact_and_metadata_warnings(self):
        source_file = SourceFile.from_content(
            part_id=PART_ID,
            part_name="Soap holder",
            storage_path=CANONICAL_PATH,
            content=DEPENDENT_SOURCE,
        )
        index = build_project_index(PROJECT_ID, "Fixture", [source_file])
        getter = IndexGetter(index, [source_file])
        repository = FakeRepository(DEPENDENT_SOURCE)
        repository.getter = lambda _project_id: getter

        context = CadToolExecutor(repository).prepare_context(
            {
                "project_id": PROJECT_ID,
                "input": {
                    "requested_part_id": PART_ID,
                    "workflow_mode": "edit",
                    "request_text": "Make the holder square.",
                    "messages": [
                        {"role": "user", "content": "Make the holder square."}
                    ],
                },
            }
        )

        body_node = next(
            node
            for node in context["dependency_graph"]["nodes"]
            if node["semantic_id"] == "body"
        )
        legs = next(
            feature
            for feature in context["impact_candidates"]
            if feature["semantic_id"] == "legs"
        )
        self.assertEqual(
            body_node["dependent_paths"]["legs"],
            ["body", "fillets", "legs"],
        )
        self.assertIn("holder_width_mm", legs["parameter_references"])
        self.assertEqual(
            context["metadata_warnings"][0]["semantic_id"],
            "legs",
        )

    def test_planner_inventory_explicitly_lists_existing_features(self):
        source = _annotate_initial_model(MODEL_BODY)
        inventory = target_inventory(source, part_id=PART_ID, semantic_ids=["body"])
        context = planner_inventory(
            inventory,
            semantic_ids=["body"],
            parameters=[
                {"name": "width", "type_name": "float", "default_source": "10.0"}
            ],
        )
        self.assertEqual(context["allowed_dependencies"], ["body"])
        self.assertEqual(context["existing_features"][0]["semantic_id"], "body")
        self.assertEqual(
            context["existing_features"][0]["function_name"], "make_body"
        )
        parameter = context["existing_parameters"][0]
        self.assertEqual(parameter["name"], "width")
        self.assertEqual(
            parameter["target_id"],
            f"{PART_ID}:model_parameter:width",
        )
        self.assertEqual(
            parameter["target_fingerprint"],
            inventory[f"{PART_ID}:model_parameter:width"].fingerprint,
        )
        self.assertTrue(context["build_model_target"]["target_id"].endswith("build_model"))

    def test_parameter_replacement_uses_model_parameter_target(self):
        source = _annotate_initial_model(MODEL_BODY)
        inventory = target_inventory(source, part_id=PART_ID, semantic_ids=["body"])
        width = inventory[f"{PART_ID}:model_parameter:width"]
        repository = FakeRepository(source)
        result = CadToolExecutor(repository).execute(
            tool_job(
                source,
                {
                    "operations": [
                        {
                            "tool": "replace_parameter_field",
                            "target_id": width.target_id,
                            "target_fingerprint": width.fingerprint,
                            "replacement_source": "width: float = 16.0",
                        }
                    ]
                },
            )
        )

        candidate = repository.files[result["candidate_path"]]
        self.assertIn("    width: float = 16.0", candidate)
        self.assertEqual(candidate.count("CAD-AGENT-START: model_parameter:width"), 1)

    def test_parameter_replacement_rejects_owned_deletion_target(self):
        source = _annotate_initial_model(MODEL_BODY)
        inventory = target_inventory(source, part_id=PART_ID, semantic_ids=["body"])
        owned_width = inventory[f"{PART_ID}:owned_model_parameter:width"]
        repository = FakeRepository(source)

        with self.assertRaises(WorkflowFailure) as failure:
            CadToolExecutor(repository).execute(
                tool_job(
                    source,
                    {
                        "operations": [
                            {
                                "tool": "replace_parameter_field",
                                "target_id": owned_width.target_id,
                                "target_fingerprint": owned_width.fingerprint,
                                "replacement_source": "width: float = 16.0",
                            }
                        ]
                    },
                )
            )

        self.assertEqual(
            failure.exception.code,
            "PARAMETER_REPLACEMENT_TARGET_INVALID",
        )
        self.assertEqual(
            failure.exception.details["suggested_target_id"],
            f"{PART_ID}:model_parameter:width",
        )
        self.assertEqual(repository.writes, [])

    def test_parameter_replacement_rejects_provenance_markers(self):
        source = _annotate_initial_model(MODEL_BODY)
        inventory = target_inventory(source, part_id=PART_ID, semantic_ids=["body"])
        width = inventory[f"{PART_ID}:model_parameter:width"]
        repository = FakeRepository(source)

        with self.assertRaisesRegex(WorkflowFailure, "provenance markers"):
            CadToolExecutor(repository).execute(
                tool_job(
                    source,
                    {
                        "operations": [
                            {
                                "tool": "replace_parameter_field",
                                "target_id": width.target_id,
                                "target_fingerprint": width.fingerprint,
                                "replacement_source": (
                                    "# CAD-AGENT-START: model_parameter:width\n"
                                    "width: float = 16.0\n"
                                    "# CAD-AGENT-END: model_parameter:width"
                                ),
                            }
                        ]
                    },
                )
            )

        self.assertEqual(repository.writes, [])

    def test_v2_parameter_edit_reviews_consumers_and_transitive_dependents(self):
        source = DEPENDENT_SOURCE
        inventory = target_inventory(
            source,
            part_id=PART_ID,
            semantic_ids=semantic_ids_in_source(source),
        )
        width = inventory[f"{PART_ID}:model_parameter:holder_width_mm"]
        repository = FakeRepository(source)
        result = CadToolExecutor(repository).execute(
            tool_job(
                source,
                {
                    "schema_version": 2,
                    "operations": [
                        {
                            "tool": "replace_parameter_field",
                            "target_id": width.target_id,
                            "target_fingerprint": width.fingerprint,
                            "replacement_source": "holder_width_mm: float = 120.0",
                        }
                    ],
                    "impact_review": [
                        {
                            "semantic_id": semantic_id,
                            "decision": "verified_compatible",
                            "reason": "Feature derives geometry from the shared width or dependency input.",
                        }
                        for semantic_id in ("body", "fillets", "legs")
                    ],
                },
            )
        )

        self.assertIn(
            "holder_width_mm: float = 120.0",
            repository.files[result["candidate_path"]],
        )

    def test_v2_plan_rejects_missing_or_false_impact_decisions(self):
        source = DEPENDENT_SOURCE
        inventory = target_inventory(
            source,
            part_id=PART_ID,
            semantic_ids=semantic_ids_in_source(source),
        )
        width = inventory[f"{PART_ID}:model_parameter:holder_width_mm"]
        body = inventory[f"{PART_ID}:function_body:make_body"]
        repository = FakeRepository(source)

        with self.assertRaises(WorkflowFailure) as incomplete:
            CadToolExecutor(repository).execute(
                tool_job(
                    source,
                    {
                        "schema_version": 2,
                        "operations": [
                            {
                                "tool": "replace_parameter_field",
                                "target_id": width.target_id,
                                "target_fingerprint": width.fingerprint,
                                "replacement_source": "holder_width_mm: float = 120.0",
                            }
                        ],
                        "impact_review": [
                            {
                                "semantic_id": "body",
                                "decision": "verified_compatible",
                                "reason": "Body uses the shared width.",
                            },
                            {
                                "semantic_id": "fillets",
                                "decision": "verified_compatible",
                                "reason": "Fillets consume the resulting body.",
                            },
                        ],
                    },
                )
            )
        self.assertEqual(incomplete.exception.code, "IMPACT_REVIEW_INCOMPLETE")

        with self.assertRaises(WorkflowFailure) as false_modified:
            CadToolExecutor(repository).execute(
                tool_job(
                    source,
                    {
                        "schema_version": 2,
                        "operations": [
                            {
                                "tool": "replace_cad_feature_body",
                                "semantic_id": "body",
                                "target_fingerprint": body.fingerprint,
                                "replacement_source": (
                                    'return cq.Workplane("XY").box('
                                    "params.holder_length_mm, "
                                    "params.holder_width_mm, 10)"
                                ),
                            }
                        ],
                        "impact_review": [
                            {
                                "semantic_id": "body",
                                "decision": "verified_compatible",
                                "reason": "Incorrectly claims no source edit.",
                            },
                            {
                                "semantic_id": "fillets",
                                "decision": "verified_compatible",
                                "reason": "Consumes generic body geometry.",
                            },
                            {
                                "semantic_id": "legs",
                                "decision": "verified_compatible",
                                "reason": "Uses shared dimensions.",
                            },
                        ],
                    },
                )
            )
        self.assertEqual(false_modified.exception.code, "IMPACT_REVIEW_INVALID")
        self.assertEqual(repository.writes, [])

    def test_initial_model_preserves_runtime_import_and_adds_provenance(self):
        repository = FakeRepository(RUNTIME_IMPORT)
        result = CadToolExecutor(repository).execute(
            tool_job(
                RUNTIME_IMPORT,
                {
                    "operations": [
                        {
                            "tool": "write_initial_model",
                            "model_body": MODEL_BODY,
                        }
                    ]
                },
                workflow_mode="initial_design",
            )
        )
        candidate = repository.files[result["candidate_path"]]

        self.assertTrue(candidate.startswith(RUNTIME_IMPORT))
        self.assertIn(
            "# CAD-AGENT-START: model_parameter:width",
            candidate,
        )
        self.assertIn("# CAD-AGENT-START: private_helper:_double", candidate)
        self.assertIn("# PART-START: body", candidate)

    def test_composed_plan_can_remove_owned_parameter_after_updating_references(self):
        source = _annotate_initial_model(MODEL_BODY)
        inventory = target_inventory(
            source,
            part_id=PART_ID,
            semantic_ids=semantic_ids_in_source(source),
        )
        metadata = inventory[f"{PART_ID}:cad_part_metadata:body"]
        body = inventory[f"{PART_ID}:function_body:make_body"]
        height = inventory[f"{PART_ID}:owned_model_parameter:height"]
        repository = FakeRepository(source)
        result = CadToolExecutor(repository).execute(
            tool_job(
                source,
                {
                    "operations": [
                        {
                            "tool": "update_cad_part_metadata",
                            "target_id": metadata.target_id,
                            "target_fingerprint": metadata.fingerprint,
                            "role": "primary_body",
                            "parameters": ["width"],
                            "depends_on": [],
                            "search_keys": ["body"],
                        },
                        {
                            "tool": "replace_function_body",
                            "target_id": body.target_id,
                            "target_fingerprint": body.fingerprint,
                            "replacement_source": (
                                'return cq.Workplane("XY").box('
                                "params.width, params.width, 2)"
                            ),
                        },
                        {
                            "tool": "delete_model_parameter",
                            "target_id": height.target_id,
                            "target_fingerprint": height.fingerprint,
                        },
                    ]
                },
            )
        )
        candidate = repository.files[result["candidate_path"]]

        self.assertNotIn("height: float", candidate)
        self.assertNotIn("params.height", candidate)
        self.assertIn("parameters=('width',)", candidate)

    def test_semantic_feature_body_edit_resolves_current_function(self):
        source = _annotate_initial_model(MODEL_BODY)
        inventory = target_inventory(source, part_id=PART_ID, semantic_ids=["body"])
        body = inventory[f"{PART_ID}:function_body:make_body"]
        repository = FakeRepository(source)
        result = CadToolExecutor(repository).execute(
            tool_job(
                source,
                {
                    "operations": [
                        {
                            "tool": "replace_cad_feature_body",
                            "semantic_id": "body",
                            "target_fingerprint": body.fingerprint,
                            "replacement_source": (
                                'return cq.Workplane("XY").box('
                                "params.width, params.height, 4)"
                            ),
                        }
                    ]
                },
            )
        )
        self.assertIn("params.height, 4)", repository.files[result["candidate_path"]])

    def test_normalizes_mixed_first_line_indentation(self):
        source = _annotate_initial_model(MODEL_BODY)
        inventory = target_inventory(source, part_id=PART_ID, semantic_ids=["body"])
        body = inventory[f"{PART_ID}:function_body:make_body"]
        repository = FakeRepository(source)
        result = CadToolExecutor(repository).execute(
            tool_job(
                source,
                {
                    "operations": [
                        {
                            "tool": "replace_function_body",
                            "target_id": body.target_id,
                            "target_fingerprint": body.fingerprint,
                            "replacement_source": (
                                '"""Updated body."""\n'
                                '    return cq.Workplane("XY").box(3, 3, 3)'
                            ),
                        }
                    ]
                },
            )
        )
        candidate = repository.files[result["candidate_path"]]
        self.assertIn('    """Updated body."""\n    return', candidate)
        self.assertEqual(
            result["normalization_notes"],
            ["replace_function_body indentation normalized"],
        )

    def test_normalizes_build_model_body_and_preserves_nested_indentation(self):
        source = _annotate_initial_model(MODEL_BODY)
        inventory = target_inventory(source, part_id=PART_ID, semantic_ids=["body"])
        build = inventory[f"{PART_ID}:build_model_body:build_model"]
        repository = FakeRepository(source)
        result = CadToolExecutor(repository).execute(
            tool_job(
                source,
                {
                    "operations": [
                        {
                            "tool": "replace_build_model_body",
                            "target_id": build.target_id,
                            "target_fingerprint": build.fingerprint,
                            "replacement_source": (
                                '"""Assemble the part."""\n'
                                '    if params.width > 0:\n'
                                '        return make_body(params)\n'
                                '    return None'
                            ),
                        }
                    ]
                },
            )
        )
        candidate = repository.files[result["candidate_path"]]
        self.assertIn('    if params.width > 0:\n        return make_body(params)', candidate)
        self.assertEqual(
            result["normalization_notes"],
            ["replace_build_model_body indentation normalized"],
        )

    def test_semantic_feature_edit_survives_function_rename(self):
        source = _annotate_initial_model(MODEL_BODY).replace(
            "def make_body(params: ModelParams):", "def soap_body(params: ModelParams):"
        ).replace("return make_body(params)", "return soap_body(params)")
        inventory = target_inventory(source, part_id=PART_ID, semantic_ids=["body"])
        body = inventory[f"{PART_ID}:function_body:soap_body"]
        repository = FakeRepository(source)
        result = CadToolExecutor(repository).execute(
            tool_job(
                source,
                {
                    "operations": [
                        {
                            "tool": "replace_cad_feature_body",
                            "semantic_id": "body",
                            "target_fingerprint": body.fingerprint,
                            "replacement_source": "return cq.Workplane(\"XY\").box(3, 3, 3)",
                        }
                    ]
                },
            )
        )
        self.assertIn("def soap_body", repository.files[result["candidate_path"]])
        self.assertIn("box(3, 3, 3)", repository.files[result["candidate_path"]])

    def test_semantic_feature_edit_rejects_unknown_id_without_upload(self):
        source = _annotate_initial_model(MODEL_BODY)
        repository = FakeRepository(source)
        with self.assertRaises(WorkflowFailure) as failure:
            CadToolExecutor(repository).execute(
                tool_job(
                    source,
                    {
                        "operations": [
                            {
                                "tool": "replace_cad_feature_body",
                                "semantic_id": "missing",
                                "target_fingerprint": "a" * 64,
                                "replacement_source": "return cq.Workplane(\"XY\")",
                            }
                        ]
                    },
                )
            )
        self.assertEqual(
            failure.exception.code,
            "FEATURE_NOT_FOUND_FOR_REPLACEMENT",
        )
        self.assertEqual(
            failure.exception.details["suggested_operation"],
            "add_cad_feature",
        )
        self.assertEqual(repository.writes, [])

    def test_new_feature_requires_build_model_assembly(self):
        source = _annotate_initial_model(MODEL_BODY)
        repository = FakeRepository(source)
        with self.assertRaises(WorkflowFailure) as failure:
            CadToolExecutor(repository).execute(
                tool_job(
                    source,
                    {
                        "operations": [
                            {
                                "tool": "add_cad_feature",
                                "semantic_id": "legs",
                                "function_name": "add_legs",
                                "role": "support_legs",
                                "parameters": ["width"],
                                "depends_on": ["body"],
                                "search_keys": ["legs", "feet"],
                                "function_source": (
                                    "def add_legs(params: ModelParams, body):\n"
                                    "    return body"
                                ),
                            }
                        ]
                    },
                )
            )
        self.assertEqual(failure.exception.code, "NEW_FEATURE_NOT_ASSEMBLED")
        self.assertEqual(repository.writes, [])

    def test_new_feature_is_created_and_assembled_transactionally(self):
        source = _annotate_initial_model(MODEL_BODY)
        inventory = target_inventory(source, part_id=PART_ID, semantic_ids=["body"])
        build = inventory[f"{PART_ID}:build_model_body:build_model"]
        repository = FakeRepository(source)
        result = CadToolExecutor(repository).execute(
            tool_job(
                source,
                {
                    "operations": [
                        {
                            "tool": "add_cad_feature",
                            "semantic_id": "legs",
                            "function_name": "add_legs",
                            "role": "support_legs",
                            "parameters": ["width"],
                            "depends_on": ["body"],
                            "search_keys": ["legs", "feet"],
                            "function_source": (
                                "def add_legs(params: ModelParams, body):\n"
                                "    return body"
                            ),
                        },
                        {
                            "tool": "replace_build_model_body",
                            "target_id": build.target_id,
                            "target_fingerprint": build.fingerprint,
                            "replacement_source": (
                                "body = make_body(params)\n"
                                "return add_legs(params, body)"
                            ),
                        },
                    ]
                },
            )
        )
        candidate = repository.files[result["candidate_path"]]
        self.assertIn("# PART-START: legs", candidate)
        self.assertIn("def add_legs", candidate)
        self.assertIn("return add_legs(params, body)", candidate)

    def test_existing_feature_cannot_be_created_again(self):
        source = _annotate_initial_model(MODEL_BODY)
        repository = FakeRepository(source)
        with self.assertRaises(WorkflowFailure) as failure:
            CadToolExecutor(repository).execute(
                tool_job(
                    source,
                    {
                        "operations": [
                            {
                                "tool": "add_cad_feature",
                                "semantic_id": "body",
                                "function_name": "another_body",
                                "role": "primary_body",
                                "parameters": ["width"],
                                "depends_on": [],
                                "search_keys": ["body"],
                                "function_source": (
                                    "def another_body(params: ModelParams):\n"
                                    "    return cq.Workplane(\"XY\")"
                                ),
                            }
                        ]
                    },
                )
            )
        self.assertEqual(
            failure.exception.code,
            "FEATURE_ALREADY_EXISTS_FOR_CREATE",
        )
        self.assertEqual(repository.writes, [])

    def test_legacy_cad_feature_target_explains_semantic_operation(self):
        source = _annotate_initial_model(MODEL_BODY)
        inventory = target_inventory(source, part_id=PART_ID, semantic_ids=["body"])
        marker = inventory[f"{PART_ID}:cad_feature:body"]
        function_body = inventory[f"{PART_ID}:function_body:make_body"]
        repository = FakeRepository(source)
        with self.assertRaises(WorkflowFailure) as failure:
            CadToolExecutor(repository).execute(
                tool_job(
                    source,
                    {
                        "operations": [
                            {
                                "tool": "replace_function_body",
                                "target_id": marker.target_id,
                                "target_fingerprint": marker.fingerprint,
                                "replacement_source": "return cq.Workplane(\"XY\")",
                            }
                        ]
                    },
                )
            )
        self.assertEqual(
            failure.exception.code,
            "FEATURE_REPLACEMENT_TARGET_INVALID",
        )
        self.assertEqual(failure.exception.details["semantic_id"], "body")
        self.assertEqual(
            failure.exception.details["suggested_operation"],
            "replace_cad_feature_body",
        )
        self.assertEqual(
            failure.exception.details["suggested_target_fingerprint"],
            function_body.fingerprint,
        )
        self.assertEqual(repository.writes, [])

    def test_unmarked_legacy_parameter_cannot_be_deleted(self):
        source = (
            RUNTIME_IMPORT
            + """
@dataclass(frozen=True)
class ModelParams:
    width: float = 10.0

@cad_part(semantic_id="body", role="body", library="cadquery", parameters=("width",), depends_on=(), search_keys=("body",))
def make_body(params: ModelParams):
    return cq.Workplane("XY").box(params.width, params.width, 2)

def build_model(params: ModelParams):
    return make_body(params)
"""
        )
        repository = FakeRepository(source)
        with self.assertRaisesRegex(WorkflowFailure, "not in the bounded context"):
            CadToolExecutor(repository).execute(
                tool_job(
                    source,
                    {
                        "operations": [
                            {
                                "tool": "delete_model_parameter",
                                "target_id": (
                                    f"{PART_ID}:owned_model_parameter:width"
                                ),
                                "target_fingerprint": "a" * 64,
                            }
                        ]
                    },
                )
            )
        self.assertEqual(repository.writes, [])

    def test_stale_fingerprint_and_failed_multi_operation_never_upload(self):
        source = _annotate_initial_model(MODEL_BODY)
        inventory = target_inventory(source, part_id=PART_ID, semantic_ids=["body"])
        build = inventory[f"{PART_ID}:build_model_body:build_model"]
        repository = FakeRepository(source)
        with self.assertRaisesRegex(WorkflowFailure, "changed after planning"):
            CadToolExecutor(repository).execute(
                tool_job(
                    source,
                    {
                        "operations": [
                            {
                                "tool": "replace_build_model_body",
                                "target_id": build.target_id,
                                "target_fingerprint": "b" * 64,
                                "replacement_source": "return make_body(params)",
                            }
                        ]
                    },
                )
            )
        self.assertEqual(repository.writes, [])

    def test_duplicate_targeted_operations_never_upload(self):
        source = _annotate_initial_model(MODEL_BODY)
        inventory = target_inventory(source, part_id=PART_ID, semantic_ids=["body"])
        helper = inventory[f"{PART_ID}:owned_private_helper:_double"]
        operation = {
            "tool": "delete_private_helper",
            "target_id": helper.target_id,
            "target_fingerprint": helper.fingerprint,
        }
        repository = FakeRepository(source)
        with self.assertRaisesRegex(
            WorkflowFailure,
            "same owned element more than once",
        ):
            CadToolExecutor(repository).execute(
                tool_job(source, {"operations": [operation, operation]})
            )
        self.assertEqual(repository.writes, [])

        with self.assertRaises(WorkflowFailure):
            CadToolExecutor(repository).execute(
                tool_job(
                    source,
                    {
                        "operations": [
                            {
                                "tool": "add_model_parameter",
                                "name": "depth",
                                "field_source": "depth: float = 3.0",
                            },
                            {
                                "tool": "add_private_helper",
                                "function_name": "_broken",
                                "function_source": "not a function",
                            },
                        ]
                    },
                )
            )
        self.assertEqual(repository.writes, [])

    def test_contract_rejects_unknown_catalog_version(self):
        with self.assertRaises(ValidationError):
            ToolPlan.model_validate(
                {
                    "schema_version": 2,
                    "summary": "Unsupported.",
                    "target_part_id": PART_ID,
                    "base_source_sha256": "a" * 64,
                    "operations": [
                        {
                            "tool": "write_initial_model",
                            "model_body": MODEL_BODY,
                        }
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
