from __future__ import annotations

from typing import Any

from .contracts import (
    EditContext,
    ResolvedEditTarget,
    WorkflowFailure,
)
from .targets import collect_target_spans, resolve_feature_body_span, source_hash


def _conversation(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized = [
        {"role": str(message["role"]), "content": str(message["content"])}
        for message in messages
        if (
            isinstance(message, dict)
            and message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
            and message["content"].strip()
        )
    ]
    return normalized[-8:]


def build_edit_context(
    getter,
    *,
    request: str,
    messages: list[dict[str, Any]],
    target: ResolvedEditTarget,
) -> EditContext:
    freshness = getter.freshness()
    if freshness["status"] != "fresh":
        raise WorkflowFailure(
            "STALE_INDEX",
            "The project index changed before context retrieval.",
            details=freshness,
        )
    source = getter.sources.get(target.part_id)
    part = getter.parts.get(target.part_id)
    if source is None or part is None:
        raise WorkflowFailure(
            "TARGET_NOT_FOUND",
            "The resolved CAD part is missing from the fresh Getter.",
        )

    spans = collect_target_spans(
        source.content,
        part_id=target.part_id,
        semantic_ids=target.semantic_ids,
    )
    target_parts: list[dict[str, Any]] = []
    source_chunks: list[dict[str, Any]] = []
    parameters: dict[str, dict[str, Any]] = {}
    dependencies: dict[str, dict[str, Any]] = {}
    for semantic_id in target.semantic_ids:
        feature = getter.get_part(target.part_id, semantic_id)
        if feature is None:
            raise WorkflowFailure(
                "TARGET_NOT_FOUND",
                f'Indexed semantic feature "{semantic_id}" is missing.',
            )
        target_parts.append(
            {
                "semantic_id": semantic_id,
                "role": feature["role"],
                "function_name": feature["function_name"],
            }
        )
        body_span = resolve_feature_body_span(spans, semantic_id)
        target_parts[-1]["function_body_target_id"] = body_span.target.target_id
        target_parts[-1]["function_body_fingerprint"] = source_hash(
            source.content[body_span.start : body_span.end]
        )
        source_chunks.append(
            {
                "semantic_id": semantic_id,
                "file_path": part["file_path"],
                "source": getter.get_part_source(target.part_id, semantic_id),
            }
        )
        for parameter in getter.get_part_parameters(target.part_id, semantic_id):
            parameters[str(parameter["name"])] = parameter
        for dependency in getter.get_dependencies(target.part_id, semantic_id):
            dependencies[str(dependency["semantic_id"])] = dependency

    return EditContext(
        request=request,
        conversation=_conversation(messages),
        part_id=target.part_id,
        part_name=target.part_name,
        file_path=part["file_path"],
        file_hash=source.content_hash,
        semantic_ids=target.semantic_ids,
        target_parts=target_parts,
        source_chunks=source_chunks,
        parameters=list(parameters.values()),
        dependencies=list(dependencies.values()),
        allowed_targets=[
            span.target for span in sorted(spans.values(), key=lambda item: item.start)
        ],
    )
