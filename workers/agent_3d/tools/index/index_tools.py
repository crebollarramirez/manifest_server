from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import Field

from ...failures import WorkflowFailure
from ..base import AgentTool, StrictToolModel, ToolExecutionContext


def _records(value: Any) -> list[dict[str, Any]]:
    """Return only dictionary items when an index field is represented as a list."""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    """Return a string value, replacing absent or non-string index data with ``""``."""

    return value if isinstance(value, str) else ""


def _strings(value: Any) -> list[str]:
    """Return only string items when an index field is represented as a list."""

    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _normalized(value: str) -> str:
    """Lowercase text and normalize separators and repeated whitespace for matching."""

    return re.sub(r"\s+", " ", re.sub(r"[_-]+", " ", value.lower())).strip()


def _relevance(query: str, candidate: str) -> float:
    """Score a candidate label against a query using exact, substring, and token matches.

    Scores range from zero to one. Exact normalized matches rank highest;
    substring matches rank next; otherwise the score is based on the fraction of
    candidate tokens present in the query.
    """

    normalized_query = _normalized(query)
    normalized_candidate = _normalized(candidate)
    if not normalized_query or not normalized_candidate:
        return 0
    if normalized_query == normalized_candidate:
        return 1
    if normalized_candidate in normalized_query:
        return 0.96
    if normalized_query in normalized_candidate:
        return 0.92
    query_tokens = set(normalized_query.split(" "))
    candidate_tokens = set(normalized_candidate.split(" "))
    overlap = len(candidate_tokens & query_tokens)
    return (overlap / len(candidate_tokens)) * 0.9 if candidate_tokens else 0


def _index(context: ToolExecutionContext) -> dict[str, Any]:
    """Load and validate the semantic index for the context's project.

    Missing, malformed, non-object, or cross-project indexes are translated into
    planning-specific ``WorkflowFailure`` values that the public tools can report
    as an unavailable index.
    """

    try:
        raw = context.services.repository.read_text(
            f"{context.project_id}/index/semantic_index.json"
        )
    except WorkflowFailure as exc:
        if exc.code == "SOURCE_MISSING":
            raise WorkflowFailure(
                "PLANNING_INDEX_UNAVAILABLE",
                "The project semantic index is unavailable.",
            ) from exc
        raise
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise WorkflowFailure(
            "PLANNING_INDEX_INVALID",
            "The project semantic index is not valid JSON.",
        ) from exc
    if not isinstance(parsed, dict):
        raise WorkflowFailure(
            "PLANNING_INDEX_INVALID",
            "The project semantic index must be a JSON object.",
        )
    indexed_project = _text(parsed.get("project_id"))
    if indexed_project and indexed_project != context.project_id:
        raise WorkflowFailure(
            "PLANNING_INDEX_INVALID",
            "The semantic index belongs to a different project.",
        )
    return parsed


def _parts(index: dict[str, Any]) -> list[dict[str, Any]]:
    """Return sanitized part records with predictable fields needed by index tools."""

    return [
        {
            **part,
            "part_id": _text(part.get("part_id")),
            "part_name": _text(part.get("part_name")),
            "cad_parts": _records(part.get("cad_parts")),
            "model_params": _records(part.get("model_params")),
        }
        for part in _records(index.get("parts"))
    ]


class IndexSearchInput(StrictToolModel):
    """Arguments for searching semantic CAD features within the selected part.

    ``query`` is the agent's descriptive search text. ``limit`` caps the number
    of highest-ranked matches returned, from one through ten.
    """

    query: str = Field(min_length=1, max_length=1_000)
    limit: int = Field(ge=1, le=10)


class IndexSearchMatch(StrictToolModel):
    """A semantic feature match returned by ``IndexSearchTool``.

    The part and semantic identifiers let the caller locate the feature, while
    ``role`` gives human-readable context and ``score`` indicates relative match
    strength on the normalized zero-to-one scale.
    """

    part_id: str
    part_name: str
    semantic_id: str
    role: str
    score: float


class IndexSearchOutput(StrictToolModel):
    """Result of a semantic feature search in the selected project part.

    ``status`` distinguishes returned matches, a valid empty search, and an
    unavailable semantic index. ``message`` is populated only for availability
    issues; ``query`` always echoes the validated search text.
    """

    status: Literal["ok", "no_match", "unavailable"]
    query: str
    matches: list[IndexSearchMatch]
    message: str | None = None


class FeatureTarget(StrictToolModel):
    """Identity and searchable metadata for one exact semantic CAD feature."""

    part_id: str
    part_name: str
    semantic_id: str
    role: str
    search_keys: list[str]


class FeatureParameter(StrictToolModel):
    """Name and declared type of a model parameter used by a feature."""

    name: str
    type_name: str


class FeatureRelationship(StrictToolModel):
    """A dependency or dependent feature identified by semantic ID and role."""

    semantic_id: str
    role: str


class IndexGetFeatureInput(StrictToolModel):
    """Arguments for retrieving context for one semantic feature in the selected part."""

    semantic_id: str = Field(min_length=1, max_length=500)


class IndexGetFeatureOutput(StrictToolModel):
    """Detailed semantic-index context for a requested feature.

    A successful result supplies the target, its referenced model parameters,
    upstream dependencies, and downstream dependents. ``not_found`` and
    ``unavailable`` results use empty relationship lists and no target.
    """

    status: Literal["ok", "not_found", "unavailable"]
    target: FeatureTarget | None
    parameters: list[FeatureParameter]
    dependencies: list[FeatureRelationship]
    dependents: list[FeatureRelationship]
    message: str | None = None


class IndexSearchTool(AgentTool[IndexSearchInput, IndexSearchOutput]):
    """Search the selected part's semantic index for CAD features relevant to text.

    This read-only discovery tool ranks each feature using its part name,
    semantic ID, role, function name, parameters, and search keys. Callers use
    the returned semantic IDs to request exact context with ``IndexGetFeatureTool``.
    """

    tool_id = "index_search"
    version = 1
    description = (
        "Search semantic CAD features in the selected part. Use this to identify "
        "relevant semantic IDs before requesting exact feature context. Requires a "
        "fresh project index and does not read or modify CAD source."
    )
    input_model = IndexSearchInput
    output_model = IndexSearchOutput

    async def execute(
        self,
        tool_input: IndexSearchInput,
        context: ToolExecutionContext,
    ) -> IndexSearchOutput:
        """Rank matching features from the selected part and return up to ``limit``.

        Index availability failures are returned in the output schema so the
        agent can distinguish them from an ordinary no-match result.
        """

        try:
            matches: list[IndexSearchMatch] = []
            for part in _parts(_index(context)):
                if part["part_id"] != context.part_id:
                    continue
                for feature in part["cad_parts"]:
                    fields = [
                        part["part_name"],
                        _text(feature.get("semantic_id")),
                        _text(feature.get("role")),
                        _text(feature.get("function_name")),
                        *_strings(feature.get("parameters")),
                        *_strings(feature.get("search_keys")),
                    ]
                    score = max(
                        (_relevance(tool_input.query, value) for value in fields),
                        default=0,
                    )
                    semantic_id = _text(feature.get("semantic_id"))
                    if not semantic_id or score < 0.25:
                        continue
                    matches.append(
                        IndexSearchMatch(
                            part_id=part["part_id"],
                            part_name=part["part_name"],
                            semantic_id=semantic_id,
                            role=_text(feature.get("role")),
                            score=round(score, 4),
                        )
                    )
            matches.sort(
                key=lambda match: (
                    -match.score,
                    match.part_name,
                    match.semantic_id,
                )
            )
            matches = matches[: tool_input.limit]
            return IndexSearchOutput(
                status="ok" if matches else "no_match",
                query=tool_input.query,
                matches=matches,
            )
        except WorkflowFailure as exc:
            if not exc.code.startswith("PLANNING_INDEX_"):
                raise
            return IndexSearchOutput(
                status="unavailable",
                query=tool_input.query,
                matches=[],
                message=str(exc),
            )


class IndexGetFeatureTool(
    AgentTool[IndexGetFeatureInput, IndexGetFeatureOutput]
):
    """Retrieve relationship and parameter context for one semantic CAD feature.

    This read-only lookup is intentionally exact: it searches only the part in
    the execution context and only the supplied semantic ID. It is the follow-up
    tool for a feature discovered through ``IndexSearchTool``.
    """

    tool_id = "index_get_feature"
    version = 1
    description = (
        "Retrieve detailed context for one exact semantic CAD feature in the "
        "selected part. Use this after identifying its semantic ID. This tool does "
        "not perform broad search or modify project source."
    )
    input_model = IndexGetFeatureInput
    output_model = IndexGetFeatureOutput

    async def execute(
        self,
        tool_input: IndexGetFeatureInput,
        context: ToolExecutionContext,
    ) -> IndexGetFeatureOutput:
        """Resolve the requested feature and assemble its local index context.

        The result includes only parameters referenced by the feature plus
        relationships whose referenced features exist in the selected part.
        Planning-index failures are represented as an unavailable result.
        """

        try:
            part = next(
                (
                    candidate
                    for candidate in _parts(_index(context))
                    if candidate["part_id"] == context.part_id
                ),
                None,
            )
            feature = next(
                (
                    candidate
                    for candidate in (part or {}).get("cad_parts", [])
                    if _text(candidate.get("semantic_id")) == tool_input.semantic_id
                ),
                None,
            )
            if part is None or feature is None:
                return IndexGetFeatureOutput(
                    status="not_found",
                    target=None,
                    parameters=[],
                    dependencies=[],
                    dependents=[],
                )
            dependency_ids = _strings(feature.get("depends_on"))
            parameter_names = set(_strings(feature.get("parameters")))
            features_by_id = {
                _text(candidate.get("semantic_id")): candidate
                for candidate in part["cad_parts"]
            }
            return IndexGetFeatureOutput(
                status="ok",
                target=FeatureTarget(
                    part_id=part["part_id"],
                    part_name=part["part_name"],
                    semantic_id=_text(feature.get("semantic_id")),
                    role=_text(feature.get("role")),
                    search_keys=_strings(feature.get("search_keys")),
                ),
                parameters=[
                    FeatureParameter(
                        name=_text(parameter.get("name")),
                        type_name=_text(parameter.get("type_name")),
                    )
                    for parameter in part["model_params"]
                    if _text(parameter.get("name")) in parameter_names
                ],
                dependencies=[
                    FeatureRelationship(
                        semantic_id=dependency_id,
                        role=_text(features_by_id[dependency_id].get("role")),
                    )
                    for dependency_id in dependency_ids
                    if dependency_id in features_by_id
                ],
                dependents=[
                    FeatureRelationship(
                        semantic_id=_text(candidate.get("semantic_id")),
                        role=_text(candidate.get("role")),
                    )
                    for candidate in part["cad_parts"]
                    if tool_input.semantic_id in _strings(candidate.get("depends_on"))
                ],
            )
        except WorkflowFailure as exc:
            if not exc.code.startswith("PLANNING_INDEX_"):
                raise
            return IndexGetFeatureOutput(
                status="unavailable",
                target=None,
                parameters=[],
                dependencies=[],
                dependents=[],
                message=str(exc),
            )
