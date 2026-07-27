from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .extractor import extract_part_index
from .models import SourceFile


def build_project_index(
    project_id: str,
    project_name: str,
    sources: list[SourceFile],
    *,
    now: Callable[[], datetime] | None = None,
) -> dict:
    ordered_sources = sorted(
        sources,
        key=lambda source: (source.part_name.casefold(), source.part_id),
    )
    parts = [extract_part_index(source) for source in ordered_sources]
    current_time = (now or (lambda: datetime.now(timezone.utc)))()
    generated_at = current_time.astimezone(timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )
    return {
        "schema_version": 1,
        "project_id": project_id,
        "project_name": project_name,
        "generated_at": generated_at,
        "files": [
            {
                "part_id": source.part_id,
                "part_name": source.part_name,
                "path": source.storage_path,
                "content_hash": source.content_hash,
            }
            for source in ordered_sources
        ],
        "parts": parts,
    }


def index_summary(index: dict, index_path: str) -> dict:
    parts = index["parts"]
    return {
        "schema_version": 1,
        "index_path": index_path,
        "files_indexed": len(index["files"]),
        "cad_parts_indexed": len(parts),
        "functions_found": sum(len(part["functions"]) for part in parts),
        "parameters_found": sum(len(part["model_params"]) for part in parts),
        "semantic_features_found": sum(len(part["cad_parts"]) for part in parts),
        "dependencies_found": sum(
            len(feature["depends_on"])
            for part in parts
            for feature in part["cad_parts"]
        ),
    }
