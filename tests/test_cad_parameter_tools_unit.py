from __future__ import annotations

import ast
import unittest
from typing import Any

from pydantic import ValidationError

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.tools import (
    CreateParameterInput,
    CreateParameterTool,
    DeleteParameterInput,
    DeleteParameterTool,
    EditParameterInput,
    EditParameterTool,
    ToolExecutionContext,
    ToolFailure,
    ToolServices,
    ToolSuccess,
)


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
PART_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "33333333-3333-4333-8333-333333333333"
CANDIDATE_ID = "candidate-1"
CANDIDATE_PATH = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{CANDIDATE_ID}/model.py"

# hole_diameter is hand-authored (no markers) and used by cut_hole.
# plate_width is agent-owned (markers) and unused.
BASE_SOURCE = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    hole_diameter: float = 4.0
    # CAD-AGENT-START: model_parameter:plate_width
    plate_width: float = 20.0
    # CAD-AGENT-END: model_parameter:plate_width

@cad_part(
    semantic_id='cut_hole',
    role='fastener',
    library="cadquery",
    parameters=('hole_diameter',),
    depends_on=(),
    search_keys=('hole',),
)
def cut_hole(params: ModelParams):
    return cq.Workplane("XY").circle(params.hole_diameter / 2).extrude(-2)

def build_model(params: ModelParams):
    return cut_hole(params)
"""

# plate_width is agent-owned and used by cut_plate -- for the in-use rejection test.
SOURCE_WITH_OWNED_IN_USE = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    # CAD-AGENT-START: model_parameter:plate_width
    plate_width: float = 20.0
    # CAD-AGENT-END: model_parameter:plate_width

@cad_part(
    semantic_id='cut_plate',
    role='structural',
    library="cadquery",
    parameters=('plate_width',),
    depends_on=(),
    search_keys=('plate',),
)
def cut_plate(params: ModelParams):
    return cq.Workplane("XY").box(params.plate_width, params.plate_width, 2)

def build_model(params: ModelParams):
    return cut_plate(params)
"""

# Exactly one agent-owned, unused field -- for the last-field-becomes-pass test.
SOURCE_SINGLE_OWNED = """from cadquery_runtime import cad_part, cq, dataclass

@dataclass(frozen=True)
class ModelParams:
    # CAD-AGENT-START: model_parameter:plate_width
    plate_width: float = 20.0
    # CAD-AGENT-END: model_parameter:plate_width

def build_model(params: ModelParams):
    return cq.Workplane("XY")
"""


class FakeRepository:
    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})

    def read_text(self, path: str) -> str:
        try:
            return self.files[path]
        except KeyError as exc:
            raise WorkflowFailure("SOURCE_MISSING", f"{path} was not found.") from exc

    def write_text(self, path: str, content: str) -> None:
        self.files[path] = content


class ExplodingWriteRepository(FakeRepository):
    def write_text(self, path: str, content: str) -> None:
        raise RuntimeError("internal-storage-secret")


def make_context(
    repository: FakeRepository,
    *,
    candidate_id: str | None = CANDIDATE_ID,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        part_id=PART_ID,
        candidate_id=candidate_id,
        services=ToolServices(repository=repository),
    )


class CreateParameterUnitTests(unittest.IsolatedAsyncioTestCase):
    def _repository(self, source: str = BASE_SOURCE) -> FakeRepository:
        return FakeRepository({CANDIDATE_PATH: source})

    def test_valid_input_parses(self):
        parsed = CreateParameterInput.model_validate({"parameter_name": "wall_height", "value": 3.0})
        self.assertEqual(parsed.parameter_name, "wall_height")
        self.assertEqual(parsed.value, 3.0)

    def test_rejects_unknown_field_and_invalid_type(self):
        with self.assertRaises(ValidationError):
            CreateParameterInput.model_validate({"parameter_name": "wall_height", "value": 3.0, "type": "float"})
        with self.assertRaises(ValidationError):
            CreateParameterInput.model_validate({"parameter_name": "wall_height", "value": "3.0"})

    async def test_normalization_trims_name(self):
        tool = CreateParameterTool()
        repository = self._repository()

        result = await tool.run({"parameter_name": "  wall_height  ", "value": 3.0}, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(result.data.parameter_name, "wall_height")

    async def test_rejects_duplicate_name(self):
        tool = CreateParameterTool()
        result = await tool.run({"parameter_name": "hole_diameter", "value": 3.0}, make_context(self._repository()))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    async def test_rejects_invalid_identifier(self):
        tool = CreateParameterTool()
        result = await tool.run({"parameter_name": "_private", "value": 3.0}, make_context(self._repository()))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    async def test_missing_candidate_rejected(self):
        tool = CreateParameterTool()
        result = await tool.run(
            {"parameter_name": "wall_height", "value": 3.0},
            make_context(self._repository(), candidate_id=None),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertFalse(result.error.retryable)

    async def test_successful_creation_marker_block_and_output(self):
        repository = self._repository()
        tool = CreateParameterTool()

        result = await tool.run({"parameter_name": "wall_height", "value": 3.0}, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(result.data.status, "created")
        self.assertEqual(result.data.parameter_name, "wall_height")
        self.assertEqual(result.data.value, 3.0)
        self.assertEqual(result.data.candidate_id, CANDIDATE_ID)
        self.assertTrue(result.data.content_hash)

        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn(
            "    # CAD-AGENT-START: model_parameter:wall_height\n"
            "    wall_height: float = 3.0\n"
            "    # CAD-AGENT-END: model_parameter:wall_height\n",
            content,
        )

    async def test_unexpected_execution_failure_is_sanitized(self):
        tool = CreateParameterTool()
        repository = ExplodingWriteRepository({CANDIDATE_PATH: BASE_SOURCE})

        result = await tool.run({"parameter_name": "wall_height", "value": 3.0}, make_context(repository))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_EXECUTION_FAILED")
        self.assertNotIn("internal-storage-secret", result.error.message)


class RepeatedCreateParameterTests(unittest.IsolatedAsyncioTestCase):
    """Several create_parameter calls against one candidate, in sequence.

    This is what `create_parameter`'s batch group depends on: the orchestrator
    dispatches a batch strictly sequentially and nothing caches source, so N
    batched calls are exactly N ordered read-modify-write cycles. These tests
    prove the splice logic composes under in-process ordering. They do NOT
    prove Supabase storage read-after-write consistency -- the fake is a dict.
    """

    SKELETON = (
        "from cadquery_runtime import cad_part, cq, dataclass\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class ModelParams:\n"
        "    pass\n"
        "\n"
        "\n"
        "def build_model(params: ModelParams):\n"
        '    return cq.Workplane("XY")\n'
    )

    @staticmethod
    async def _create(repository: FakeRepository, name: str, value: float):
        return await CreateParameterTool().run(
            {"parameter_name": name, "value": value},
            make_context(repository),
        )

    @staticmethod
    def _marker_span(source: str, name: str) -> tuple[int, int]:
        start = source.index(f"# CAD-AGENT-START: model_parameter:{name}")
        end = source.index(f"# CAD-AGENT-END: model_parameter:{name}")
        return start, end

    async def test_a_second_create_appends_after_the_first_rather_than_inside_it(self):
        repository = FakeRepository({CANDIDATE_PATH: BASE_SOURCE})

        self.assertIsInstance(await self._create(repository, "slot_depth", 3.0), ToolSuccess)
        self.assertIsInstance(await self._create(repository, "rib_height", 7.5), ToolSuccess)

        source = repository.files[CANDIDATE_PATH]
        ast.parse(source)
        first_start, first_end = self._marker_span(source, "slot_depth")
        second_start, second_end = self._marker_span(source, "rib_height")
        # Ordering, not mere presence: a plain assertIn for both blocks would
        # pass even if the second were spliced inside the first's markers,
        # which is exactly what _parameter_insertion_span exists to prevent.
        self.assertLess(first_start, first_end)
        self.assertLess(first_end, second_start)
        self.assertLess(second_start, second_end)

    async def test_a_second_create_of_the_same_name_is_rejected_as_duplicate(self):
        repository = FakeRepository({CANDIDATE_PATH: BASE_SOURCE})

        self.assertIsInstance(await self._create(repository, "slot_depth", 3.0), ToolSuccess)
        result = await self._create(repository, "slot_depth", 9.0)

        # Proves validate_input's re-read observes the previous call's write --
        # the read-after-write ordering a batch relies on, asserted directly.
        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertEqual(result.error.details["reason"], "duplicate")

    async def test_three_creates_land_in_call_order(self):
        repository = FakeRepository({CANDIDATE_PATH: BASE_SOURCE})

        for name, value in (("first_mm", 1.0), ("second_mm", 2.0), ("third_mm", 3.0)):
            self.assertIsInstance(await self._create(repository, name, value), ToolSuccess)

        source = repository.files[CANDIDATE_PATH]
        ast.parse(source)
        positions = [
            source.index(f"{name}: float")
            for name in ("first_mm", "second_mm", "third_mm")
        ]
        self.assertEqual(positions, sorted(positions))

    async def test_two_creates_against_an_empty_model_params_replace_pass_then_append(self):
        repository = FakeRepository({CANDIDATE_PATH: self.SKELETON})

        # Call 1 takes the `pass`-replacement branch; call 2 takes the append
        # branch. Different code paths, and a brand-new part starts here.
        self.assertIsInstance(await self._create(repository, "plate_mm", 4.0), ToolSuccess)
        self.assertIsInstance(await self._create(repository, "boss_mm", 8.0), ToolSuccess)

        source = repository.files[CANDIDATE_PATH]
        ast.parse(source)
        self.assertNotIn("    pass\n", source)
        first_start, first_end = self._marker_span(source, "plate_mm")
        second_start, _second_end = self._marker_span(source, "boss_mm")
        self.assertLess(first_end, second_start)
        self.assertLess(first_start, first_end)


class LaggingRepository(FakeRepository):
    """A store where a write is not immediately visible to later reads.

    Not a simulation of Supabase -- it is the counterexample. Batched
    create_parameter calls assume an upload is visible to a download issued
    moments later; this makes the consequence of that assumption failing
    explicit and testable instead of leaving it implied.

    The lag is two reads per write, because that is one whole
    ``create_parameter`` call: the tool reads once in ``validate_input`` and
    again in ``execute``, and it is the second read that decides what gets
    spliced and written back. A one-read lag would be absorbed by the first
    read and prove nothing.
    """

    STALE_READS_PER_WRITE = 2

    def __init__(self, files: dict[str, str] | None = None):
        super().__init__(files)
        self._stale: dict[str, tuple[int, str]] = {}

    def read_text(self, path: str) -> str:
        pending = self._stale.get(path)
        if pending is not None:
            remaining, content = pending
            if remaining > 1:
                self._stale[path] = (remaining - 1, content)
            else:
                del self._stale[path]
            return content
        return super().read_text(path)

    def write_text(self, path: str, content: str) -> None:
        if path in self.files:
            self._stale[path] = (self.STALE_READS_PER_WRITE, self.files[path])
        super().write_text(path, content)


class ReadAfterWriteAssumptionTests(unittest.IsolatedAsyncioTestCase):
    """What batching depends on, stated as a test rather than a comment.

    `create_parameter` reads the candidate, splices, and writes the whole
    file. Two calls in one round are therefore correct only if the second
    call's read observes the first call's write. In process, with the real
    Supabase repository, that is a storage upload followed by a download
    milliseconds later.

    The gap is not new -- the same read-after-write already spans consecutive
    rounds today, and spans plan steps -- but batching shortens the window
    from one model round to one function call. These tests pin both sides so
    the dependency is visible to whoever reads this next.
    """

    async def test_a_read_your_writes_store_composes_two_creates(self):
        repository = FakeRepository({CANDIDATE_PATH: BASE_SOURCE})

        for name in ("slot_mm", "rib_mm"):
            result = await CreateParameterTool().run(
                {"parameter_name": name, "value": 2.0}, make_context(repository)
            )
            self.assertIsInstance(result, ToolSuccess)

        source = repository.files[CANDIDATE_PATH]
        self.assertIn("slot_mm: float", source)
        self.assertIn("rib_mm: float", source)

    async def test_a_store_without_read_after_write_would_lose_the_first_field(self):
        repository = LaggingRepository({CANDIDATE_PATH: BASE_SOURCE})

        for name in ("slot_mm", "rib_mm"):
            result = await CreateParameterTool().run(
                {"parameter_name": name, "value": 2.0}, make_context(repository)
            )
            self.assertIsInstance(result, ToolSuccess)

        # Both calls reported success, yet the first field is gone: the
        # second call spliced into a stale copy and wrote it back over the
        # first. This is the failure mode to look for if batched parameter
        # creation ever silently drops a field in production.
        source = repository.files[CANDIDATE_PATH]
        self.assertNotIn("slot_mm: float", source)
        self.assertIn("rib_mm: float", source)


class EditParameterUnitTests(unittest.IsolatedAsyncioTestCase):
    def _repository(self, source: str = BASE_SOURCE) -> FakeRepository:
        return FakeRepository({CANDIDATE_PATH: source})

    def test_valid_input_parses(self):
        parsed = EditParameterInput.model_validate({"parameter_name": "hole_diameter", "value": 5.0})
        self.assertEqual(parsed.value, 5.0)

    async def test_rejects_missing_parameter(self):
        tool = EditParameterTool()
        result = await tool.run({"parameter_name": "does_not_exist", "value": 5.0}, make_context(self._repository()))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    async def test_edits_hand_authored_field(self):
        repository = self._repository()
        tool = EditParameterTool()

        result = await tool.run({"parameter_name": "hole_diameter", "value": 8.0}, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(result.data.previous_value, 4.0)
        self.assertEqual(result.data.value, 8.0)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn("hole_diameter: float = 8.0", content)
        self.assertNotIn("hole_diameter: float = 4.0", content)

    async def test_edits_owned_field_preserving_markers(self):
        repository = self._repository()
        tool = EditParameterTool()

        result = await tool.run({"parameter_name": "plate_width", "value": 25.0}, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn(
            "    # CAD-AGENT-START: model_parameter:plate_width\n"
            "    plate_width: float = 25.0\n"
            "    # CAD-AGENT-END: model_parameter:plate_width\n",
            content,
        )

    async def test_missing_candidate_rejected(self):
        tool = EditParameterTool()
        result = await tool.run(
            {"parameter_name": "hole_diameter", "value": 8.0},
            make_context(self._repository(), candidate_id=None),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")


class DeleteParameterUnitTests(unittest.IsolatedAsyncioTestCase):
    def _repository(self, source: str = BASE_SOURCE) -> FakeRepository:
        return FakeRepository({CANDIDATE_PATH: source})

    def test_valid_input_parses(self):
        parsed = DeleteParameterInput.model_validate({"parameter_name": "plate_width"})
        self.assertEqual(parsed.parameter_name, "plate_width")

    async def test_rejects_missing_parameter(self):
        tool = DeleteParameterTool()
        result = await tool.run({"parameter_name": "does_not_exist"}, make_context(self._repository()))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    async def test_rejects_non_owned_field(self):
        tool = DeleteParameterTool()
        result = await tool.run({"parameter_name": "hole_diameter"}, make_context(self._repository()))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertEqual(result.error.details["reason"], "not_owned")

    async def test_rejects_in_use_field_with_referenced_by_details(self):
        tool = DeleteParameterTool()
        repository = self._repository(SOURCE_WITH_OWNED_IN_USE)

        result = await tool.run({"parameter_name": "plate_width"}, make_context(repository))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")
        self.assertEqual(result.error.details["reason"], "in_use")
        self.assertEqual(result.error.details["referenced_by"], ["cut_plate"])
        # Rejected write must not mutate the candidate.
        self.assertEqual(repository.files[CANDIDATE_PATH], SOURCE_WITH_OWNED_IN_USE)

    async def test_deletes_owned_unused_field(self):
        repository = self._repository()
        tool = DeleteParameterTool()

        result = await tool.run({"parameter_name": "plate_width"}, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        self.assertEqual(result.data.removed_value, 20.0)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertNotIn("plate_width", content)
        self.assertNotIn("CAD-AGENT-START: model_parameter:plate_width", content)
        self.assertIn("hole_diameter: float = 4.0", content)

    async def test_deleting_last_field_leaves_pass(self):
        repository = self._repository(SOURCE_SINGLE_OWNED)
        tool = DeleteParameterTool()

        result = await tool.run({"parameter_name": "plate_width"}, make_context(repository))

        self.assertIsInstance(result, ToolSuccess)
        content = repository.files[CANDIDATE_PATH]
        ast.parse(content)
        self.assertIn("class ModelParams:\n    pass\n", content)

    async def test_missing_candidate_rejected(self):
        tool = DeleteParameterTool()
        result = await tool.run(
            {"parameter_name": "plate_width"},
            make_context(self._repository(), candidate_id=None),
        )

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_VALIDATION_FAILED")

    async def test_unexpected_execution_failure_is_sanitized(self):
        tool = DeleteParameterTool()
        repository = ExplodingWriteRepository({CANDIDATE_PATH: BASE_SOURCE})

        result = await tool.run({"parameter_name": "plate_width"}, make_context(repository))

        self.assertIsInstance(result, ToolFailure)
        self.assertEqual(result.error.code, "TOOL_EXECUTION_FAILED")
        self.assertNotIn("internal-storage-secret", result.error.message)


if __name__ == "__main__":
    unittest.main()
