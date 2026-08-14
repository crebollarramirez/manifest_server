from __future__ import annotations

import logging

from workers.indexer.indexer.getter import IndexGetter

from .contracts import PartInventoryItem
from .failures import ProjectPlanningFailure
from .repository import ProjectPlanningRepository


LOGGER = logging.getLogger(__name__)


def build_existing_parts_inventory(
    repository: ProjectPlanningRepository,
    project_id: str,
) -> list[PartInventoryItem]:
    sources = repository.projects.list_cad_sources(project_id)
    if not sources:
        return []  # brand-new/empty project: every part becomes kind="new"

    index = repository.projects.read_index(project_id)
    if index is None:
        raise ProjectPlanningFailure(
            "PROJECT_INDEX_MISSING",
            "The project has CAD parts but has never been indexed. "
            "Run build_index before planning.",
        )

    # loader= is omitted: this Getter is used for one planning call and
    # never needs .reload().
    getter = IndexGetter(index, sources)
    freshness = getter.freshness()
    if freshness["status"] != "fresh":
        raise ProjectPlanningFailure(
            "PROJECT_INDEX_STALE",
            "The project's semantic index is stale relative to its CAD "
            "sources. Run build_index before planning.",
            details=freshness,
        )

    items = [
        PartInventoryItem(
            part_id=str(part["part_id"]),
            part_name=str(part["part_name"]),
            part_type="cad",
            features=[
                {"semantic_id": feature["semantic_id"], "role": feature["role"]}
                for feature in part.get("cad_parts", [])
            ],
            parameters=[str(param["name"]) for param in part.get("model_params", [])],
        )
        for part in index.get("parts", [])
    ]
    LOGGER.info(
        "project planning existing_parts snapshot project_id=%s part_count=%s",
        project_id,
        len(items),
    )
    LOGGER.debug("project planning existing_parts detail=%s", items)
    return items
