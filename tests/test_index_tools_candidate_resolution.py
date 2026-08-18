from __future__ import annotations

import json
import unittest

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.tools import (
    IndexGetFeatureTool,
    IndexSearchTool,
    ToolExecutionContext,
    ToolServices,
)
from workers.agent_3d.tools.index.candidate_index import CandidatePartIndex

from tests.test_candidate_index import UNPARSEABLE_SOURCE, UNWIRED_SOURCE, source


PROJECT_ID = "22222222-2222-4222-8222-222222222222"
PART_ID = "11111111-1111-4111-8111-111111111111"
OTHER_PART_ID = "99999999-9999-4999-8999-999999999999"
EDIT_JOB_ID = "33333333-3333-4333-8333-333333333333"
INDEX_PATH = f"{PROJECT_ID}/index/semantic_index.json"
CANDIDATE_PATH = f"{PROJECT_ID}/candidates/cad/{PART_ID}/{EDIT_JOB_ID}/model.py"


def accepted_index(*, role: str = "stale_accepted_role") -> str:
    """The persisted project index: one feature on the edited part, one elsewhere.

    ``mounting_plate`` appears here with a deliberately different role from the
    candidate's, so a test can tell which source answered a lookup.
    """

    return json.dumps(
        {
            "schema_version": 1,
            "project_id": PROJECT_ID,
            "parts": [
                {
                    "part_id": PART_ID,
                    "part_name": "Bracket",
                    "cad_parts": [
                        {
                            "semantic_id": "mounting_plate",
                            "function_name": "build_mounting_plate",
                            "role": role,
                            "parameters": [],
                            "depends_on": [],
                            "search_keys": ["mounting_plate"],
                        }
                    ],
                    "model_params": [],
                },
                {
                    "part_id": OTHER_PART_ID,
                    "part_name": "Soap Holder",
                    "cad_parts": [
                        {
                            "semantic_id": "holder_floor",
                            "function_name": "build_holder_floor",
                            "role": "supporting_floor",
                            "parameters": [],
                            "depends_on": [],
                            "search_keys": ["holder"],
                        }
                    ],
                    "model_params": [],
                },
            ],
        }
    )


class FakeRepository:
    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})

    def read_text(self, path: str) -> str:
        try:
            return self.files[path]
        except KeyError as exc:
            raise WorkflowFailure(
                "SOURCE_MISSING", f"{path} was not found in project storage."
            ) from exc


def editing_context(
    repository: FakeRepository, *, part_id: str = PART_ID
) -> ToolExecutionContext:
    """A context shaped like the agent loop's: a live candidate for the edited part."""

    return ToolExecutionContext(
        run_id="run-1",
        project_id=PROJECT_ID,
        part_id=part_id,
        candidate_id=EDIT_JOB_ID,
        services=ToolServices(
            repository=repository,
            candidate_index=CandidatePartIndex(
                repository=repository,
                part_id=PART_ID,
                part_name="Bracket",
                candidate_path=CANDIDATE_PATH,
            ),
        ),
    )


def planning_context(
    repository: FakeRepository, *, part_id: str = PART_ID
) -> ToolExecutionContext:
    """A context shaped like the planning agent's: no candidate exists yet."""

    return ToolExecutionContext(
        run_id="run-1",
        project_id=PROJECT_ID,
        part_id=part_id,
        candidate_id=None,
        services=ToolServices(repository=repository),
    )


async def get_feature(context: ToolExecutionContext, semantic_id: str):
    return await IndexGetFeatureTool().run({"semantic_id": semantic_id}, context)


async def search(context: ToolExecutionContext, query: str, limit: int = 5):
    return await IndexSearchTool().run({"query": query, "limit": limit}, context)


class CurrentPartResolvesAgainstTheCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_feature_a_previous_step_created_is_retrievable(self):
        # The whole point: the accepted index has never heard of support_arm,
        # because this job has not committed. A later step must still read it.
        repository = FakeRepository(
            {
                INDEX_PATH: accepted_index(),
                CANDIDATE_PATH: source("mounting_plate", "support_arm"),
            }
        )

        result = await get_feature(editing_context(repository), "support_arm")

        self.assertTrue(result.ok)
        self.assertEqual(result.data.status, "ok")
        self.assertEqual(result.data.target.semantic_id, "support_arm")
        self.assertEqual(result.data.target.role, "role_of_support_arm")

    async def test_a_feature_a_previous_step_created_is_searchable(self):
        repository = FakeRepository(
            {
                INDEX_PATH: accepted_index(),
                CANDIDATE_PATH: source("mounting_plate", "support_arm"),
            }
        )

        result = await search(editing_context(repository), "support arm")

        self.assertEqual(result.data.status, "ok")
        self.assertIn(
            "support_arm", [match.semantic_id for match in result.data.matches]
        )

    async def test_the_candidate_overrides_a_stale_accepted_record(self):
        repository = FakeRepository(
            {
                INDEX_PATH: accepted_index(role="stale_accepted_role"),
                CANDIDATE_PATH: source("mounting_plate"),
            }
        )

        result = await get_feature(editing_context(repository), "mounting_plate")

        self.assertEqual(result.data.target.role, "role_of_mounting_plate")

    async def test_a_feature_deleted_in_the_candidate_is_no_longer_found(self):
        repository = FakeRepository(
            {INDEX_PATH: accepted_index(), CANDIDATE_PATH: source("support_arm")}
        )

        result = await get_feature(editing_context(repository), "mounting_plate")

        self.assertEqual(result.data.status, "not_found")

    async def test_parameters_come_from_the_candidate(self):
        repository = FakeRepository(
            {INDEX_PATH: accepted_index(), CANDIDATE_PATH: source("mounting_plate")}
        )

        result = await get_feature(editing_context(repository), "mounting_plate")

        self.assertEqual(
            [parameter.name for parameter in result.data.parameters],
            ["bracket_length_mm"],
        )

    async def test_a_feature_not_yet_wired_into_build_model_is_still_readable(self):
        # Mid-step, between create_feature and edit_cad_build_model. The
        # accepted index cannot answer, and the strict extractor refuses the
        # source -- but the feature is really there.
        repository = FakeRepository(
            {INDEX_PATH: accepted_index(), CANDIDATE_PATH: UNWIRED_SOURCE}
        )

        result = await get_feature(editing_context(repository), "mounting_plate")

        self.assertEqual(result.data.status, "ok")
        self.assertEqual(result.data.target.role, "role_of_mounting_plate")

    async def test_reads_track_the_candidate_as_it_changes_within_a_job(self):
        repository = FakeRepository(
            {INDEX_PATH: accepted_index(), CANDIDATE_PATH: source("mounting_plate")}
        )
        context = editing_context(repository)
        self.assertEqual(
            (await get_feature(context, "support_arm")).data.status, "not_found"
        )

        repository.files[CANDIDATE_PATH] = source("mounting_plate", "support_arm")

        self.assertEqual((await get_feature(context, "support_arm")).data.status, "ok")

    async def test_search_and_get_feature_agree_on_what_exists(self):
        repository = FakeRepository(
            {
                INDEX_PATH: accepted_index(),
                CANDIDATE_PATH: source("mounting_plate", "support_arm"),
            }
        )
        context = editing_context(repository)

        found = {
            match.semantic_id
            for match in (await search(context, "role of", limit=10)).data.matches
        }

        self.assertEqual(found, {"mounting_plate", "support_arm"})
        for semantic_id in found:
            with self.subTest(semantic_id=semantic_id):
                self.assertEqual(
                    (await get_feature(context, semantic_id)).data.status, "ok"
                )


class OtherSourcesStillUseTheAcceptedIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_planning_has_no_candidate_and_reads_the_accepted_index(self):
        repository = FakeRepository(
            {
                INDEX_PATH: accepted_index(role="stale_accepted_role"),
                # Present, and deliberately ignored: with no candidate in
                # context there is no edit job whose candidate this would be.
                CANDIDATE_PATH: source("mounting_plate"),
            }
        )

        result = await get_feature(planning_context(repository), "mounting_plate")

        self.assertEqual(result.data.target.role, "stale_accepted_role")

    async def test_a_part_that_is_not_being_edited_reads_the_accepted_index(self):
        repository = FakeRepository(
            {INDEX_PATH: accepted_index(), CANDIDATE_PATH: source("mounting_plate")}
        )

        result = await get_feature(
            planning_context(repository, part_id=OTHER_PART_ID), "holder_floor"
        )

        self.assertEqual(result.data.status, "ok")
        self.assertEqual(result.data.target.part_name, "Soap Holder")

    async def test_a_candidate_that_will_not_parse_falls_back_to_the_accepted_index(self):
        repository = FakeRepository(
            {INDEX_PATH: accepted_index(), CANDIDATE_PATH: UNPARSEABLE_SOURCE}
        )

        result = await get_feature(editing_context(repository), "mounting_plate")

        self.assertEqual(result.data.status, "ok")
        self.assertEqual(result.data.target.role, "stale_accepted_role")

    async def test_no_candidate_and_no_index_reports_the_index_as_unavailable(self):
        repository = FakeRepository()

        self.assertEqual(
            (
                await get_feature(editing_context(repository), "mounting_plate")
            ).data.status,
            "unavailable",
        )
        self.assertEqual(
            (await search(editing_context(repository), "mounting")).data.status,
            "unavailable",
        )

    async def test_search_scores_only_the_edited_part(self):
        repository = FakeRepository(
            {INDEX_PATH: accepted_index(), CANDIDATE_PATH: source("mounting_plate")}
        )

        result = await search(editing_context(repository), "holder floor")

        self.assertEqual(result.data.status, "no_match")


if __name__ == "__main__":
    unittest.main()
