from __future__ import annotations

import textwrap
import unittest

from workers.cad_validator.cad_ast_validator import validate_cad_source


def model_source(decorator_fields: str) -> str:
    fields = textwrap.indent(textwrap.dedent(decorator_fields).strip(), "    ")
    return (
        "@dataclass(frozen=True)\n"
        "class ModelParams:\n"
        "    width_mm: float = 10.0\n"
        "\n"
        "@cad_part(\n"
        f"{fields}\n"
        ")\n"
        "def build_body(params: ModelParams):\n"
        '    return cq.Workplane("XY").box(\n'
        "        params.width_mm,\n"
        "        params.width_mm,\n"
        "        params.width_mm,\n"
        "    )\n"
        "\n"
        "def build_model(params: ModelParams):\n"
        "    return build_body(params)\n"
    )


class CadPartDecoratorTests(unittest.TestCase):
    def test_accepts_six_field_semantic_decorator(self):
        source = model_source(
            """
            semantic_id="body",
            role="primary_body",
            library="cadquery",
            parameters=("width_mm",),
            depends_on=(),
            search_keys=("body", "cube"),
            """
        )

        report = validate_cad_source(source)

        self.assertTrue(report["valid"], report)

    def test_rejects_legacy_and_extra_fields(self):
        source = model_source(
            """
            id="body",
            role="primary_body",
            library="cadquery",
            editable=True,
            protected_regions=(),
            parameters=("width_mm",),
            depends_on=(),
            consumes_tags=(),
            produces_tags=(),
            search_keys=("body",),
            """
        )

        report = validate_cad_source(source)

        self.assertFalse(report["valid"])
        errors = report["checks"]["decorator_fields"]["errors"]
        self.assertEqual(errors[0]["code"], "decorator_fields")
        self.assertIn(
            "semantic_id, role, library, parameters, depends_on, search_keys",
            errors[0]["message"],
        )

    def test_validates_dependencies_against_semantic_ids(self):
        source = model_source(
            """
            semantic_id="body",
            role="primary_body",
            library="cadquery",
            parameters=("width_mm",),
            depends_on=("missing_part",),
            search_keys=("body",),
            """
        )

        report = validate_cad_source(source)

        errors = report["checks"]["decorator_fields"]["errors"]
        self.assertEqual(errors[0]["code"], "unknown_dependencies")
        self.assertIn("semantic_ids", errors[0]["message"])


if __name__ == "__main__":
    unittest.main()
