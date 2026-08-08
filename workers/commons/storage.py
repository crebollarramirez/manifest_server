from __future__ import annotations


BUCKET = "3dProjects"
INDEX_FILENAME = "semantic_index.json"
INITIAL_CAD_SOURCE = "from cadquery_runtime import cad_part, cq, dataclass\n"


def index_storage_path(project_id: str) -> str:
    return f"{project_id}/index/{INDEX_FILENAME}"


def cad_source_storage_path(project_id: str, part_id: str) -> str:
    return f"{project_id}/parts/cad/{part_id}/model.py"


def is_uninitialized_cad_source(content: str) -> bool:
    """Return whether a source is the system-owned blank CAD-part marker.

    Blank parts are intentionally not valid CadQuery programs yet. They become
    indexable only after the CAD Editor validates and commits their first model.
    """

    return content == INITIAL_CAD_SOURCE
