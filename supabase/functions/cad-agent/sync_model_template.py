from __future__ import annotations

from pathlib import Path


FUNCTION_DIR = Path(__file__).resolve().parent
MODEL_TEMPLATES = (
    (
        FUNCTION_DIR.parents[2] / "3dModel.py",
        FUNCTION_DIR / "model_template.ts",
        "DEFAULT_MODEL_BODY",
    ),
    (
        FUNCTION_DIR / "mesh_model_template.py",
        FUNCTION_DIR / "mesh_model_template.ts",
        "DEFAULT_MESH_MODEL_BODY",
    ),
)


def main() -> None:
    for source_path, target_path, export_name in MODEL_TEMPLATES:
        model_source = source_path.read_text(encoding="utf-8").strip()
        module_source = (
            f"// Generated from {source_path.name} by sync_model_template.py.\n"
            "// Regenerate this module before deploying when the starter model changes.\n"
            f"export const {export_name} =\n  {model_source!r};\n"
        )
        target_path.write_text(module_source, encoding="utf-8")


if __name__ == "__main__":
    main()
