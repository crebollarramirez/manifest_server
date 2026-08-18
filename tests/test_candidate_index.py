from __future__ import annotations

import unittest

from workers.agent_3d.failures import WorkflowFailure
from workers.agent_3d.tools.hashing import source_hash
from workers.agent_3d.tools.index.candidate_index import (
    CandidatePartIndex,
    lenient_part_index,
)


PART_ID = "11111111-1111-4111-8111-111111111111"
CANDIDATE_PATH = "project/candidates/cad/part/job/model.py"


def source(*features: str, length_default: str = "100.0") -> str:
    """Build a conforming candidate declaring ``features`` in dependency-free order."""

    blocks = "\n\n".join(
        "@cad_part(\n"
        f'    semantic_id="{name}",\n'
        f'    role="role_of_{name}",\n'
        '    library="cadquery",\n'
        '    parameters=("bracket_length_mm",),\n'
        "    depends_on=(),\n"
        f'    search_keys=("{name}",),\n'
        ")\n"
        f"def build_{name}(params: ModelParams):\n"
        '    return cq.Workplane("XY").box(params.bracket_length_mm, 20, 5)'
        for name in features
    )
    calls = "\n".join(f"    {name} = build_{name}(params)" for name in features)
    returned = features[0] if features else "cq.Workplane(\"XY\")"
    return (
        "from cadquery_runtime import cad_part, cq, dataclass\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class ModelParams:\n"
        f"    bracket_length_mm: float = {length_default}\n"
        "\n\n"
        f"{blocks}\n"
        "\n\n"
        "def build_model(params: ModelParams):\n"
        f"{calls}\n"
        f"    return {returned}\n"
    )


# What the candidate looks like between create_feature and the
# edit_cad_build_model call that wires the feature in -- a normal intermediate
# state that the project extractor's contract rejects.
UNWIRED_SOURCE = source("mounting_plate").replace(
    "    mounting_plate = build_mounting_plate(params)\n    return mounting_plate",
    '    return cq.Workplane("XY")',
)

UNPARSEABLE_SOURCE = "def build_model(params:\n"


class FakeRepository:
    """Minimal storage stand-in that counts reads and can be made to fail."""

    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})
        self.reads: list[str] = []

    def read_text(self, path: str) -> str:
        self.reads.append(path)
        try:
            return self.files[path]
        except KeyError as exc:
            raise WorkflowFailure(
                "SOURCE_MISSING", f"{path} was not found in project storage."
            ) from exc


def candidate_index(repository: FakeRepository) -> CandidatePartIndex:
    return CandidatePartIndex(
        repository=repository,
        part_id=PART_ID,
        part_name="Bracket",
        candidate_path=CANDIDATE_PATH,
    )


def semantic_ids(record: dict | None) -> list[str]:
    return [feature["semantic_id"] for feature in (record or {}).get("cad_parts", [])]


class CandidatePartIndexDerivationTests(unittest.TestCase):
    def test_the_record_is_derived_from_the_exact_candidate_bytes(self):
        content = source("mounting_plate")
        index = candidate_index(FakeRepository({CANDIDATE_PATH: content}))

        record = index.part_index()

        self.assertEqual(semantic_ids(record), ["mounting_plate"])
        self.assertEqual(record["content_hash"], source_hash(content))
        self.assertEqual(index.source_sha256, source_hash(content))
        self.assertEqual(index.mode, "strict")

    def test_the_record_carries_the_metadata_a_step_needs_without_reading_source(self):
        index = candidate_index(FakeRepository({CANDIDATE_PATH: source("mounting_plate")}))

        feature = index.part_index()["cad_parts"][0]

        self.assertEqual(feature["function_name"], "build_mounting_plate")
        self.assertEqual(feature["role"], "role_of_mounting_plate")
        self.assertEqual(feature["parameters"], ["bracket_length_mm"])
        self.assertEqual(feature["depends_on"], [])

    def test_a_candidate_that_cannot_be_read_yields_no_record(self):
        index = candidate_index(FakeRepository())

        self.assertIsNone(index.part_index())
        self.assertIsNone(index.source_sha256)

    def test_source_that_will_not_parse_yields_no_record(self):
        index = candidate_index(FakeRepository({CANDIDATE_PATH: UNPARSEABLE_SOURCE}))

        self.assertIsNone(index.part_index())
        self.assertEqual(index.mode, "none")


class CandidatePartIndexFreshnessTests(unittest.TestCase):
    def test_an_unchanged_candidate_is_read_but_not_re_derived(self):
        repository = FakeRepository({CANDIDATE_PATH: source("mounting_plate")})
        index = candidate_index(repository)

        first = index.part_index()
        second = index.part_index()

        # Same object, so the second call re-read the candidate to confirm the
        # hash but did no parsing work.
        self.assertIs(first, second)
        self.assertEqual(repository.reads, [CANDIDATE_PATH, CANDIDATE_PATH])

    def test_a_changed_candidate_is_re_derived_before_anything_is_returned(self):
        repository = FakeRepository({CANDIDATE_PATH: source("mounting_plate")})
        index = candidate_index(repository)
        self.assertEqual(semantic_ids(index.part_index()), ["mounting_plate"])

        grown = source("mounting_plate", "support_arm")
        repository.files[CANDIDATE_PATH] = grown

        self.assertEqual(
            semantic_ids(index.part_index()), ["mounting_plate", "support_arm"]
        )
        self.assertEqual(index.source_sha256, source_hash(grown))

    def test_a_record_built_from_superseded_bytes_is_never_served(self):
        # The freshness guarantee stated as its own case: whatever comes back
        # describes the bytes currently stored, never an earlier version --
        # even when the current bytes are the harder ones to read.
        repository = FakeRepository({CANDIDATE_PATH: source("mounting_plate")})
        index = candidate_index(repository)
        index.part_index()
        stale_sha256 = index.source_sha256

        repository.files[CANDIDATE_PATH] = UNWIRED_SOURCE
        record = index.part_index()

        self.assertNotEqual(index.source_sha256, stale_sha256)
        self.assertEqual(index.source_sha256, source_hash(UNWIRED_SOURCE))
        self.assertEqual(record["content_hash"], source_hash(UNWIRED_SOURCE))

    def test_unparseable_bytes_do_not_leave_the_previous_record_standing(self):
        repository = FakeRepository({CANDIDATE_PATH: source("mounting_plate")})
        index = candidate_index(repository)
        index.part_index()

        repository.files[CANDIDATE_PATH] = UNPARSEABLE_SOURCE

        self.assertIsNone(index.part_index())

    def test_candidate_and_index_hashes_agree_whenever_a_record_is_served(self):
        repository = FakeRepository({CANDIDATE_PATH: source("mounting_plate")})
        index = candidate_index(repository)

        for content in (source("mounting_plate"), UNWIRED_SOURCE, source("a", "b")):
            repository.files[CANDIDATE_PATH] = content
            with self.subTest(content=content[:40]):
                self.assertIsNotNone(index.part_index())
                self.assertEqual(index.candidate_sha256, index.source_sha256)

    def test_refresh_re_derives_even_when_the_candidate_has_not_moved(self):
        repository = FakeRepository({CANDIDATE_PATH: source("mounting_plate")})
        index = candidate_index(repository)
        first = index.part_index()

        second = index.refresh()

        self.assertIsNot(first, second)
        self.assertEqual(semantic_ids(second), ["mounting_plate"])

    def test_refresh_reports_a_candidate_that_became_readable_again(self):
        repository = FakeRepository({CANDIDATE_PATH: UNWIRED_SOURCE})
        index = candidate_index(repository)
        index.part_index()
        self.assertEqual(index.mode, "lenient")

        repository.files[CANDIDATE_PATH] = source("mounting_plate")

        self.assertEqual(semantic_ids(index.refresh()), ["mounting_plate"])
        self.assertEqual(index.mode, "strict")

    def test_refresh_on_an_unreadable_candidate_yields_no_record(self):
        index = candidate_index(FakeRepository())

        self.assertIsNone(index.refresh())


class BuildModelSourceTests(unittest.TestCase):
    """The assembly function is carried verbatim, not summarized.

    A step may replace ``build_model`` wholesale, and nothing else in the
    roster says how the features currently compose into one solid.
    """

    def test_the_assembly_function_is_returned_verbatim(self):
        index = candidate_index(FakeRepository({CANDIDATE_PATH: source("mounting_plate")}))
        index.part_index()

        self.assertEqual(
            index.build_model_source(),
            "def build_model(params: ModelParams):\n"
            "    mounting_plate = build_mounting_plate(params)\n"
            "    return mounting_plate",
        )

    def test_it_tracks_the_candidate_as_the_assembly_changes(self):
        repository = FakeRepository({CANDIDATE_PATH: source("mounting_plate")})
        index = candidate_index(repository)
        index.part_index()

        repository.files[CANDIDATE_PATH] = source("mounting_plate", "support_arm")
        index.part_index()

        self.assertIn("support_arm = build_support_arm(params)", index.build_model_source())

    def test_it_is_available_for_source_only_the_lenient_scan_can_read(self):
        # The window where seeing the assembly matters most: the feature
        # exists but is not wired in yet.
        index = candidate_index(FakeRepository({CANDIDATE_PATH: UNWIRED_SOURCE}))
        index.part_index()

        self.assertEqual(index.mode, "lenient")
        self.assertEqual(
            index.build_model_source(),
            'def build_model(params: ModelParams):\n    return cq.Workplane("XY")',
        )

    def test_a_part_with_no_assembly_function_reports_no_source(self):
        index = candidate_index(
            FakeRepository(
                {
                    CANDIDATE_PATH: UNWIRED_SOURCE.replace(
                        "def build_model(params: ModelParams):", "def _unused():"
                    )
                }
            )
        )
        index.part_index()

        self.assertEqual(index.build_model_source(), "")

    def test_unreadable_source_reports_no_assembly(self):
        index = candidate_index(FakeRepository({CANDIDATE_PATH: UNPARSEABLE_SOURCE}))
        index.part_index()

        self.assertEqual(index.build_model_source(), "")


class LenientFallbackTests(unittest.TestCase):
    def test_a_feature_not_yet_wired_into_build_model_is_still_reported(self):
        # create_feature writes the feature; edit_cad_build_model wires it in.
        # Between those two calls the source is non-conforming by design, and
        # the feature demonstrably exists -- so it has to be readable.
        index = candidate_index(FakeRepository({CANDIDATE_PATH: UNWIRED_SOURCE}))

        record = index.part_index()

        self.assertEqual(semantic_ids(record), ["mounting_plate"])
        self.assertEqual(index.mode, "lenient")

    def test_the_lenient_record_matches_the_strict_record_field_for_field(self):
        conforming = source("mounting_plate")
        strict = candidate_index(FakeRepository({CANDIDATE_PATH: conforming})).part_index()
        lenient = lenient_part_index(
            conforming,
            part_id=PART_ID,
            part_name="Bracket",
            storage_path=CANDIDATE_PATH,
            content_hash=source_hash(conforming),
        )

        self.assertEqual(set(lenient), set(strict))
        self.assertEqual(lenient["model_params"], strict["model_params"])
        self.assertEqual(lenient["functions"], strict["functions"])
        self.assertEqual(lenient["build_model"], strict["build_model"])
        # The strict record additionally infers parameter_references, which no
        # consumer of this record reads.
        for actual, expected in zip(lenient["cad_parts"], strict["cad_parts"]):
            self.assertEqual(actual, {k: v for k, v in expected.items() if k in actual})

    def test_non_literal_decorator_values_become_empty_rather_than_an_error(self):
        record = lenient_part_index(
            "from cadquery_runtime import cad_part, cq, dataclass\n"
            "\n"
            "@dataclass(frozen=True)\n"
            "class ModelParams:\n"
            "    pass\n"
            "\n"
            "@cad_part(\n"
            "    semantic_id=NAME,\n"
            '    role="role",\n'
            '    library="cadquery",\n'
            "    parameters=(),\n"
            "    depends_on=(),\n"
            '    search_keys=("a",),\n'
            ")\n"
            "def build_thing(params: ModelParams):\n"
            "    return None\n",
            part_id=PART_ID,
            part_name="Bracket",
            storage_path=CANDIDATE_PATH,
            content_hash="abc",
        )

        self.assertEqual(record["cad_parts"][0]["semantic_id"], "")
        self.assertEqual(record["cad_parts"][0]["role"], "role")
        self.assertEqual(record["cad_parts"][0]["function_name"], "build_thing")

    def test_a_source_with_no_build_model_still_reports_its_features(self):
        record = lenient_part_index(
            UNWIRED_SOURCE.replace("def build_model(params: ModelParams):", "def _unused():"),
            part_id=PART_ID,
            part_name="Bracket",
            storage_path=CANDIDATE_PATH,
            content_hash="abc",
        )

        self.assertEqual(semantic_ids(record), ["mounting_plate"])
        self.assertIsNone(record["build_model"])

    def test_unparseable_source_produces_no_record(self):
        self.assertIsNone(
            lenient_part_index(
                UNPARSEABLE_SOURCE,
                part_id=PART_ID,
                part_name="Bracket",
                storage_path=CANDIDATE_PATH,
                content_hash="abc",
            )
        )


if __name__ == "__main__":
    unittest.main()
