from __future__ import annotations

from typing import Any, Iterable

from .contracts import (
    CandidateSource,
    EditContext,
    EditPlan,
    RepairContext,
    ResolvedEditTarget,
    WorkflowFailure,
)
from .targets import collect_target_spans


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


def build_repair_context(
    getter,
    *,
    request: str,
    messages: list[dict[str, Any]],
    target: ResolvedEditTarget,
    candidate: CandidateSource,
    previous_plan: EditPlan,
    validation_result: dict[str, Any],
    related_function_names: Iterable[str],
    related_parameter_queries: Iterable[str],
) -> RepairContext:
    extra_functions = list(dict.fromkeys(related_function_names))
    spans = collect_target_spans(
        candidate.content,
        part_id=target.part_id,
        semantic_ids=target.semantic_ids,
        extra_function_names=extra_functions,
    )
    failed_chunks = [
        {
            "target_id": span.target.target_id,
            "kind": span.target.kind,
            "name": span.target.name,
            "source": span.target.source,
        }
        for span in sorted(spans.values(), key=lambda item: item.start)
        if span.target.kind == "function_body"
    ]

    accepted_contexts = [
        getter.get_context(target.part_id, semantic_id)
        for semantic_id in target.semantic_ids
        if getter.get_part(target.part_id, semantic_id) is not None
    ]
    related_results: list[dict[str, Any]] = []
    for function_name in extra_functions:
        function = getter.get_function(target.part_id, function_name)
        if function is not None:
            related_results.append({"kind": "function", **function})
    for query in dict.fromkeys(related_parameter_queries):
        related_results.extend(
            {"kind": "parameter", **result}
            for result in getter.search_parameters(
                query,
                part_id=target.part_id,
            )
        )

    return RepairContext(
        original_request=request,
        conversation=_conversation(messages),
        previous_plan=previous_plan.model_dump(mode="json"),
        failed_candidate_hash=candidate.content_hash,
        failed_candidate_chunks=failed_chunks,
        validation_result=validation_result,
        accepted_source_context={"targets": accepted_contexts},
        related_index_results=related_results,
        allowed_targets=[
            span.target for span in sorted(spans.values(), key=lambda item: item.start)
        ],
    )
