from __future__ import annotations

import unittest
from types import SimpleNamespace

from workers.cad_editor.cad_editor.agent import CadEditAgent
from workers.cad_editor.cad_editor.applier import apply_edit_plan
from workers.cad_editor.cad_editor.contracts import (
    EditPlan,
    SearchQueryPlan,
    WorkflowFailure,
)
from workers.cad_editor.cad_editor.error_classifier import (
    classify_validation_error,
)
from workers.cad_editor.cad_editor.repository import SupabaseEditRepository
from workers.cad_editor.cad_editor.targets import source_hash


PART_ID = "part-a"

MODEL_SOURCE = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    hole_diameter: float = 4.0
    plate_width: float = 20.0

@cad_part(
    semantic_id="mount_holes",
    role="fastener_features",
    library="cadquery",
    parameters=("hole_diameter",),
    depends_on=(),
    search_keys=("mounting holes", "screw holes"),
)
def cut_mounting_holes(params: ModelParams):
    return cq.Workplane("XY").circle(params.hole_diameter / 2).extrude(2)

def build_model(params: ModelParams):
    return cut_mounting_holes(params)
"""


def plan(*operations: dict, semantic_ids: list[str] | None = None) -> EditPlan:
    return EditPlan.model_validate(
        {
            "summary": "Update the mounting holes.",
            "target_semantic_ids": (
                ["mount_holes"] if semantic_ids is None else semantic_ids
            ),
            "operations": list(operations),
        }
    )


class DeterministicEditTests(unittest.TestCase):
    def test_edit_plan_schema_uses_supported_nested_any_of(self):
        schema = EditPlan.model_json_schema()
        operation_items = schema["properties"]["operations"]["items"]

        self.assertIn("anyOf", operation_items)
        self.assertNotIn("oneOf", operation_items)
        self.assertNotIn("discriminator", operation_items)

    def test_applies_allowed_operations_without_rewriting_unrelated_text(self):
        edit_plan = plan(
            {
                "operation": "replace_parameter_field",
                "target_id": f"{PART_ID}:model_parameter:hole_diameter",
                "replacement_source": "hole_diameter: float = 6.0",
            },
            {
                "operation": "update_cad_part_metadata",
                "target_id": f"{PART_ID}:cad_part_metadata:mount_holes",
                "role": "mounting_features",
                "parameters": ["hole_diameter"],
                "depends_on": [],
                "search_keys": ["mounting holes", "bolt holes"],
            },
            {
                "operation": "replace_function_body",
                "target_id": f"{PART_ID}:function_body:cut_mounting_holes",
                "replacement_source": (
                    "radius = params.hole_diameter / 2\n"
                    'return cq.Workplane("XY").circle(radius).extrude(3)'
                ),
            },
        )

        candidate = apply_edit_plan(
            MODEL_SOURCE,
            expected_hash=source_hash(MODEL_SOURCE),
            part_id=PART_ID,
            semantic_ids=["mount_holes"],
            plan=edit_plan,
        )

        self.assertIn("hole_diameter: float = 6.0", candidate.content)
        self.assertIn("role='mounting_features'", candidate.content)
        self.assertIn("search_keys=('mounting holes', 'bolt holes')", candidate.content)
        self.assertIn("radius = params.hole_diameter / 2", candidate.content)
        self.assertIn("plate_width: float = 20.0", candidate.content)
        self.assertIn(
            "def build_model(params: ModelParams):\n"
            "    return cut_mounting_holes(params)",
            candidate.content,
        )
        self.assertEqual(
            candidate.changed_symbols,
            ["hole_diameter", "mount_holes", "cut_mounting_holes"],
        )

    def test_rejects_targets_outside_the_server_owned_scope(self):
        edit_plan = plan(
            {
                "operation": "replace_function_body",
                "target_id": "part-b:function_body:cut_mounting_holes",
                "replacement_source": "return None",
            }
        )

        with self.assertRaisesRegex(WorkflowFailure, "outside the allowed context"):
            apply_edit_plan(
                MODEL_SOURCE,
                expected_hash=source_hash(MODEL_SOURCE),
                part_id=PART_ID,
                semantic_ids=["mount_holes"],
                plan=edit_plan,
            )

    def test_parameter_replacement_cannot_rename_the_selected_symbol(self):
        edit_plan = plan(
            {
                "operation": "replace_parameter_field",
                "target_id": f"{PART_ID}:model_parameter:hole_diameter",
                "replacement_source": "other_name: float = 6.0",
            }
        )

        with self.assertRaisesRegex(WorkflowFailure, "preserve the targeted field"):
            apply_edit_plan(
                MODEL_SOURCE,
                expected_hash=source_hash(MODEL_SOURCE),
                part_id=PART_ID,
                semantic_ids=["mount_holes"],
                plan=edit_plan,
            )

    def test_rejects_a_changed_candidate_base_hash(self):
        edit_plan = plan(
            {
                "operation": "replace_function_body",
                "target_id": f"{PART_ID}:function_body:cut_mounting_holes",
                "replacement_source": "return None",
            }
        )

        with self.assertRaisesRegex(WorkflowFailure, "source changed"):
            apply_edit_plan(
                MODEL_SOURCE,
                expected_hash="0" * 64,
                part_id=PART_ID,
                semantic_ids=["mount_holes"],
                plan=edit_plan,
            )

    def test_adds_parameters_helpers_features_and_updates_build_model(self):
        edit_plan = plan(
            {
                "operation": "add_model_parameter",
                "name": "slot_width",
                "field_source": "slot_width: float = 3.0",
            },
            {
                "operation": "add_private_helper",
                "function_name": "_slot_spacing",
                "function_source": (
                    "def _slot_spacing(params: ModelParams):\n"
                    "    return params.slot_width * 2"
                ),
            },
            {
                "operation": "add_cad_feature",
                "semantic_id": "drainage_slots",
                "function_name": "cut_drainage_slots",
                "role": "drainage_features",
                "parameters": ["slot_width"],
                "depends_on": ["mount_holes"],
                "search_keys": ["drainage slots", "soap drainage"],
                "function_source": (
                    "def cut_drainage_slots(params: ModelParams, base):\n"
                    "    spacing = _slot_spacing(params)\n"
                    "    return base.faces('>Z').workplane().rect("
                    "params.slot_width, spacing).cutThruAll()"
                ),
            },
            {
                "operation": "replace_build_model_body",
                "target_id": f"{PART_ID}:build_model_body:build_model",
                "replacement_source": (
                    "base = cut_mounting_holes(params)\n"
                    "return cut_drainage_slots(params, base)"
                ),
            },
        )

        candidate = apply_edit_plan(
            MODEL_SOURCE,
            expected_hash=source_hash(MODEL_SOURCE),
            part_id=PART_ID,
            semantic_ids=["mount_holes"],
            plan=edit_plan,
        )

        self.assertIn("slot_width: float = 3.0", candidate.content)
        self.assertIn("def _slot_spacing(params: ModelParams):", candidate.content)
        self.assertIn("# PART-START: drainage_slots", candidate.content)
        self.assertIn("semantic_id='drainage_slots'", candidate.content)
        self.assertIn("parameters=('slot_width',)", candidate.content)
        self.assertIn("depends_on=('mount_holes',)", candidate.content)
        self.assertIn(
            "def build_model(params: ModelParams):\n"
            "    base = cut_mounting_holes(params)\n"
            "    return cut_drainage_slots(params, base)",
            candidate.content,
        )

    def test_rejects_unsafe_or_unresolved_additions(self):
        invalid_plans = [
            (
                plan(
                    {
                        "operation": "add_model_parameter",
                        "name": "hole_diameter",
                        "field_source": "hole_diameter: float = 8.0",
                    }
                ),
                "already exists",
            ),
            (
                plan(
                    {
                        "operation": "add_private_helper",
                        "function_name": "_helper",
                        "function_source": (
                            "def _helper():\n"
                            "    import os\n"
                            "    return os.getcwd()"
                        ),
                    }
                ),
                "must not contain imports",
            ),
            (
                plan(
                    {
                        "operation": "add_cad_feature",
                        "semantic_id": "new_feature",
                        "function_name": "new_feature",
                        "role": "feature",
                        "parameters": ["missing_parameter"],
                        "depends_on": ["missing_dependency"],
                        "search_keys": ["new feature"],
                        "function_source": (
                            "@cad_part()\n"
                            "def new_feature(params: ModelParams):\n"
                            "    return cq.Workplane('XY')"
                        ),
                    }
                ),
                "must not provide decorators",
            ),
            (
                plan(
                    {
                        "operation": "add_cad_feature",
                        "semantic_id": "new_feature",
                        "function_name": "new_feature",
                        "role": "feature",
                        "parameters": ["missing_parameter"],
                        "depends_on": ["mount_holes"],
                        "search_keys": ["new feature"],
                        "function_source": (
                            "def new_feature(params: ModelParams):\n"
                            "    return cq.Workplane('XY')"
                        ),
                    }
                ),
                "unknown ModelParams",
            ),
        ]

        for edit_plan, message in invalid_plans:
            with self.subTest(message=message):
                with self.assertRaisesRegex(WorkflowFailure, message):
                    apply_edit_plan(
                        MODEL_SOURCE,
                        expected_hash=source_hash(MODEL_SOURCE),
                        part_id=PART_ID,
                        semantic_ids=["mount_holes"],
                        plan=edit_plan,
                    )


class ErrorClassificationTests(unittest.TestCase):
    def test_validator_nonrepairable_hint_stops_a_retry(self):
        result = {
            "stage": "cadquery_runtime",
            "repairable_hint": False,
            "diagnostics": [
                {
                    "error_code": "CADQUERY_RUNTIME_ERROR",
                    "repairable_hint": False,
                    "file_path": "candidate/model.py",
                }
            ],
        }

        classification = classify_validation_error(
            result,
            candidate_path="candidate/model.py",
        )

        self.assertFalse(classification.repairable)

    def test_diagnostic_outside_candidate_scope_stops_a_retry(self):
        result = {
            "stage": "cadquery_runtime",
            "repairable_hint": True,
            "diagnostics": [
                {
                    "error_code": "CADQUERY_RUNTIME_ERROR",
                    "file_path": "another/model.py",
                }
            ],
        }

        classification = classify_validation_error(
            result,
            candidate_path="candidate/model.py",
        )

        self.assertFalse(classification.repairable)
        self.assertEqual(classification.category, "scope")


class StructuredAgentTests(unittest.TestCase):
    def test_uses_responses_parse_with_the_pydantic_output_type(self):
        calls = []

        class Responses:
            def parse(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    output_parsed=SearchQueryPlan(
                        queries=["mounting holes"],
                    ),
                    output=[],
                )

        client = SimpleNamespace(responses=Responses())
        agent = CadEditAgent(client=client, model="test-model")

        result = agent.extract_search_queries("make the holes larger")

        self.assertEqual(result.queries, ["mounting holes"])
        self.assertEqual(calls[0]["model"], "test-model")
        self.assertIs(calls[0]["text_format"], SearchQueryPlan)

    def test_surfaces_a_structured_output_refusal(self):
        class Responses:
            def parse(self, **_kwargs):
                return SimpleNamespace(
                    output_parsed=None,
                    output=[
                        SimpleNamespace(
                            content=[
                                SimpleNamespace(
                                    type="refusal",
                                    refusal="Request refused.",
                                )
                            ]
                        )
                    ],
                )

        agent = CadEditAgent(
            client=SimpleNamespace(responses=Responses()),
            model="test-model",
        )

        with self.assertRaisesRegex(WorkflowFailure, "Request refused"):
            agent.extract_search_queries("unsafe request")


class SupabaseEditRepositoryTests(unittest.TestCase):
    def test_patch_edit_job_uses_the_update_builder_without_single(self):
        calls = []

        class UpdateBuilder:
            def update(self, values):
                calls.append(("update", values))
                return self

            def eq(self, column, value):
                calls.append(("eq", column, value))
                return self

            def select(self, columns):
                calls.append(("select", columns))
                return self

            def execute(self):
                calls.append(("execute",))
                return SimpleNamespace(
                    data=[{"id": "edit-a", "state": "resolving_target"}]
                )

        class Supabase:
            def table(self, name):
                calls.append(("table", name))
                return UpdateBuilder()

        repository = SupabaseEditRepository(Supabase())

        updated = repository.patch_edit_job(
            "edit-a",
            {"state": "resolving_target"},
        )

        self.assertEqual(
            updated,
            {"id": "edit-a", "state": "resolving_target"},
        )
        self.assertEqual(
            calls,
            [
                ("table", "edit_jobs"),
                ("update", {"state": "resolving_target"}),
                ("eq", "id", "edit-a"),
                ("select", "*"),
                ("execute",),
            ],
        )

    def test_patch_edit_job_rejects_an_update_that_returns_no_row(self):
        class UpdateBuilder:
            def update(self, _values):
                return self

            def eq(self, _column, _value):
                return self

            def select(self, _columns):
                return self

            def execute(self):
                return SimpleNamespace(data=[])

        supabase = SimpleNamespace(table=lambda _name: UpdateBuilder())
        repository = SupabaseEditRepository(supabase)

        with self.assertRaisesRegex(RuntimeError, "could not be updated"):
            repository.patch_edit_job("missing-edit", {"state": "failed"})


if __name__ == "__main__":
    unittest.main()
