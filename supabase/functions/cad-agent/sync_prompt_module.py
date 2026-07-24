from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROMPT_MODULES = (
    (ROOT / "CAD_SYSTEM_PROMPT.md", ROOT / "prompt.ts", "SYSTEM_PROMPT"),
    (ROOT / "MESH_SYSTEM_PROMPT.md", ROOT / "mesh_prompt.ts", "MESH_SYSTEM_PROMPT"),
)


def main() -> None:
    for source_path, target_path, export_name in PROMPT_MODULES:
        prompt_text = source_path.read_text(encoding="utf-8").strip()
        module_source = (
            f"// Generated from {source_path.name} by sync_prompt_module.py.\n"
            "// Edit the Markdown file, then rerun this script.\n"
            f"export const {export_name} =\n  {prompt_text!r};\n"
        )
        target_path.write_text(module_source, encoding="utf-8")


if __name__ == "__main__":
    main()
