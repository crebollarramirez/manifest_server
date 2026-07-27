from __future__ import annotations

from typing import Any

from .contracts import ResolvedEditTarget, WorkflowFailure


AUTO_RESOLVE_SCORE = 0.80
AUTO_RESOLVE_MARGIN = 0.10
MAX_CANDIDATES = 5


def _merge_matches(
    matches: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for match in matches:
        key = (str(match["part_id"]), str(match["semantic_id"]))
        previous = merged.get(key)
        if previous is None or float(match["score"]) > float(previous["score"]):
            merged[key] = match
    return sorted(
        merged.values(),
        key=lambda match: (
            -float(match["score"]),
            str(match["part_name"]).casefold(),
            str(match["semantic_id"]),
        ),
    )[:MAX_CANDIDATES]


def resolve_edit_target(
    getter,
    agent,
    request: str,
    *,
    requested_part_id: str | None = None,
) -> ResolvedEditTarget:
    if requested_part_id is not None:
        part = getter.parts.get(requested_part_id)
        if part is None:
            raise WorkflowFailure(
                "TARGET_NOT_FOUND",
                "The requested linked CAD part is missing from the fresh Getter.",
            )
        matches = getter.search_parts(
            request,
            MAX_CANDIDATES,
            part_id=requested_part_id,
        )
        if not matches:
            query_plan = agent.extract_search_queries(request)
            expanded: list[dict[str, Any]] = []
            for query in query_plan.queries:
                expanded.extend(
                    getter.search_parts(
                        query,
                        MAX_CANDIDATES,
                        part_id=requested_part_id,
                    )
                )
            matches = _merge_matches(expanded)
        semantic_ids = [
            str(feature["semantic_id"])
            for feature in part.get("cad_parts", [])
        ]
        return ResolvedEditTarget(
            part_id=requested_part_id,
            part_name=str(part["part_name"]),
            semantic_ids=semantic_ids,
            confidence=max(
                (float(match["score"]) for match in matches),
                default=1.0,
            ),
            reason=(
                "The linked CAD part is the authoritative edit target; "
                "all of its semantic features are available for safe edits "
                "and extension."
            ),
            candidates=matches,
        )

    matches = getter.search_parts(request, MAX_CANDIDATES)
    if not matches:
        query_plan = agent.extract_search_queries(request)
        expanded: list[dict[str, Any]] = []
        for query in query_plan.queries:
            expanded.extend(getter.search_parts(query, MAX_CANDIDATES))
        matches = _merge_matches(expanded)
    if not matches:
        raise WorkflowFailure(
            "NO_INDEXED_TARGET",
            "No indexed CAD feature matched the user request.",
        )

    top_score = float(matches[0]["score"])
    next_score = float(matches[1]["score"]) if len(matches) > 1 else 0.0
    if (
        top_score >= AUTO_RESOLVE_SCORE
        and top_score - next_score >= AUTO_RESOLVE_MARGIN
    ):
        top = matches[0]
        return ResolvedEditTarget(
            part_id=str(top["part_id"]),
            part_name=str(top["part_name"]),
            semantic_ids=[str(top["semantic_id"])],
            confidence=top_score,
            reason=(
                f'High-confidence {top["matched_field"]} match '
                f'for "{top["matched_value"]}".'
            ),
            candidates=matches,
        )

    selection = agent.select_targets(request, matches)
    selected_ids = list(dict.fromkeys(selection.semantic_ids))
    candidate_keys = {
        (str(match["part_id"]), str(match["semantic_id"])) for match in matches
    }
    if not selected_ids or any(
        (selection.part_id, semantic_id) not in candidate_keys
        for semantic_id in selected_ids
    ):
        raise WorkflowFailure(
            "INVALID_TARGET_SELECTION",
            "AI target selection referenced candidates outside the ranked results.",
        )
    part_names = {
        str(match["part_name"])
        for match in matches
        if str(match["part_id"]) == selection.part_id
    }
    if len(part_names) != 1:
        raise WorkflowFailure(
            "INVALID_TARGET_SELECTION",
            "AI target selection did not resolve one database part.",
        )
    selected_scores = [
        float(match["score"])
        for match in matches
        if (
            str(match["part_id"]) == selection.part_id
            and str(match["semantic_id"]) in selected_ids
        )
    ]
    return ResolvedEditTarget(
        part_id=selection.part_id,
        part_name=part_names.pop(),
        semantic_ids=selected_ids,
        confidence=min(selected_scores),
        reason=selection.reason,
        candidates=matches,
    )
