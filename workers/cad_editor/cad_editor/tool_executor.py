from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from workers.indexer.indexer.semantic_graph import (
    effective_parameter_references,
    graph_context,
)

from .applier import apply_edit_plan
from .context_builder import build_edit_context
from .contracts import EditPlan, ResolvedEditTarget, WorkflowFailure
from .targets import (
    collect_target_spans,
    resolve_feature_body_span,
    semantic_ids_in_source,
    source_hash,
)
from .tool_contracts import (
    AddCadFeature,
    AddModelParameter,
    AddPrivateHelper,
    ConfirmNoChange,
    DeleteCadFeature,
    DeleteModelParameter,
    DeletePrivateHelper,
    ReplaceBuildModelBody,
    ReplaceCadFeatureBody,
    ReplaceFunctionBody,
    ReplaceParameterField,
    ToolPlan,
    UpdateCadPartMetadata,
    WriteInitialModel,
)


RUNTIME_IMPORT = "from cadquery_runtime import cad_part, cq, dataclass\n"
PART_START = re.compile(r"^(?P<indent>\s*)# PART-START: (?P<name>[A-Za-z_]\w*)\s*$")
PART_END = re.compile(r"^(?P<indent>\s*)# PART-END: (?P<name>[A-Za-z_]\w*)\s*$")
OWNED_START = re.compile(
    r"^(?P<indent>\s*)# CAD-AGENT-START: "
    r"(?P<kind>model_parameter|private_helper):(?P<name>[A-Za-z_]\w*)\s*$"
)
OWNED_END = re.compile(
    r"^(?P<indent>\s*)# CAD-AGENT-END: "
    r"(?P<kind>model_parameter|private_helper):(?P<name>[A-Za-z_]\w*)\s*$"
)


@dataclass(frozen=True)
class InventoryTarget:
    target_id: str
    kind: str
    name: str
    semantic_id: str | None
    start: int
    end: int
    source: str
    fingerprint: str
    deletable: bool
    details: dict[str, Any]

    def public(self, part_id: str) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "kind": self.kind,
            "part_id": part_id,
            "name": self.name,
            "semantic_id": self.semantic_id,
            "source": self.source,
            "target_fingerprint": self.fingerprint,
            "deletable": self.deletable,
            "details": self.details,
        }


def _fingerprint(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _private_function_names(source: str) -> list[str]:
    tree = ast.parse(source)
    return [
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("_")
        and not node.name.startswith("__")
    ]


def _feature_names(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    features: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "cad_part"
            ):
                continue
            semantic_id = next(
                (
                    str(keyword.value.value)
                    for keyword in decorator.keywords
                    if keyword.arg == "semantic_id"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ),
                None,
            )
            if semantic_id:
                features[semantic_id] = node.name
    return features


def _marker_targets(source: str, part_id: str) -> dict[str, InventoryTarget]:
    lines = source.splitlines(keepends=True)
    offsets = _line_offsets(source)
    open_markers: dict[tuple[str, str], tuple[int, str]] = {}
    targets: dict[str, InventoryTarget] = {}
    feature_names = _feature_names(source)
    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        part_start = PART_START.match(stripped)
        part_end = PART_END.match(stripped)
        owned_start = OWNED_START.match(stripped)
        owned_end = OWNED_END.match(stripped)
        if part_start:
            open_markers[("cad_feature", part_start.group("name"))] = (
                index,
                part_start.group("indent"),
            )
            continue
        if owned_start:
            open_markers[(owned_start.group("kind"), owned_start.group("name"))] = (
                index,
                owned_start.group("indent"),
            )
            continue

        closing: tuple[str, str, str] | None = None
        if part_end:
            closing = ("cad_feature", part_end.group("name"), part_end.group("indent"))
        elif owned_end:
            closing = (
                owned_end.group("kind"),
                owned_end.group("name"),
                owned_end.group("indent"),
            )
        if closing is None:
            continue
        kind, name, indentation = closing
        opened = open_markers.pop((kind, name), None)
        if opened is None or opened[1] != indentation:
            raise WorkflowFailure(
                "INVALID_ACCEPTED_SOURCE",
                f"Ownership markers for {kind}:{name} are not balanced.",
            )
        start_line = opened[0]
        start = offsets[start_line]
        end = offsets[index + 1]
        block = source[start:end]
        target_id = (
            f"{part_id}:cad_feature:{name}"
            if kind == "cad_feature"
            else f"{part_id}:owned_{kind}:{name}"
        )
        semantic_id = name if kind == "cad_feature" else None
        targets[target_id] = InventoryTarget(
            target_id=target_id,
            kind=f"owned_{kind}" if kind != "cad_feature" else kind,
            name=feature_names.get(name, name) if kind == "cad_feature" else name,
            semantic_id=semantic_id,
            start=start,
            end=end,
            source=block.rstrip("\n"),
            fingerprint=_fingerprint(block),
            deletable=True,
            details={"ownership": "cad-agent"},
        )
    if open_markers:
        marker = next(iter(open_markers))
        raise WorkflowFailure(
            "INVALID_ACCEPTED_SOURCE",
            f"Ownership marker {marker[0]}:{marker[1]} is not closed.",
        )
    return targets


def target_inventory(
    source: str,
    *,
    part_id: str,
    semantic_ids: list[str],
) -> dict[str, InventoryTarget]:
    spans = collect_target_spans(
        source,
        part_id=part_id,
        semantic_ids=semantic_ids,
        extra_function_names=_private_function_names(source),
    )
    inventory = {
        target_id: InventoryTarget(
            target_id=target_id,
            kind=span.target.kind,
            name=span.target.name,
            semantic_id=span.target.semantic_id,
            start=span.start,
            end=span.end,
            source=source[span.start : span.end].rstrip("\n"),
            fingerprint=_fingerprint(source[span.start : span.end]),
            deletable=False,
            details=dict(span.target.details),
        )
        for target_id, span in spans.items()
    }
    inventory.update(_marker_targets(source, part_id))
    return inventory


def planner_inventory(
    inventory: dict[str, InventoryTarget],
    *,
    semantic_ids: list[str],
    parameters: list[dict[str, Any]],
    dependency_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a compact, explicit inventory for create-versus-update planning."""
    graph_nodes = {
        str(node["semantic_id"]): node
        for node in (dependency_graph or {}).get("nodes", [])
    }
    features: list[dict[str, Any]] = []
    for semantic_id in semantic_ids:
        metadata = next(
            (
                item
                for item in inventory.values()
                if item.kind == "cad_part_metadata"
                and item.semantic_id == semantic_id
            ),
            None,
        )
        body = next(
            (
                item
                for item in inventory.values()
                if item.kind == "function_body"
                and item.semantic_id == semantic_id
            ),
            None,
        )
        if metadata is None or body is None:
            raise WorkflowFailure(
                "INVALID_ACCEPTED_SOURCE",
                f'CAD feature semantic ID "{semantic_id}" is missing planner metadata.',
            )
        features.append(
            {
                "semantic_id": semantic_id,
                "role": metadata.details.get("role"),
                "function_name": body.name,
                "function_body_fingerprint": body.fingerprint,
                "parameters": list(metadata.details.get("parameters") or []),
                "parameter_references": list(
                    graph_nodes.get(semantic_id, {}).get(
                        "parameter_references",
                        metadata.details.get("parameters") or [],
                    )
                ),
                "depends_on": list(metadata.details.get("depends_on") or []),
            }
        )
    build_model = next(
        (item for item in inventory.values() if item.kind == "build_model_body"),
        None,
    )
    if build_model is None:
        raise WorkflowFailure(
            "INVALID_ACCEPTED_SOURCE",
            "build_model has no editable planner target.",
        )
    planner_parameters: list[dict[str, Any]] = []
    for parameter in parameters:
        name = str(parameter.get("name") or "")
        editable_targets = [
            item
            for item in inventory.values()
            if item.kind == "model_parameter" and item.name == name
        ]
        if len(editable_targets) != 1:
            raise WorkflowFailure(
                "INVALID_ACCEPTED_SOURCE",
                f'ModelParams field "{name}" does not have exactly one editable model_parameter target.',
            )
        editable = editable_targets[0]
        planner_parameters.append(
            {
                "name": name,
                "type": parameter.get("type_name"),
                "default": parameter.get("default_source"),
                "target_id": editable.target_id,
                "target_fingerprint": editable.fingerprint,
            }
        )
    return {
        "existing_features": features,
        "existing_parameters": planner_parameters,
        "allowed_dependencies": list(semantic_ids),
        "build_model_target": {
            "target_id": build_model.target_id,
            "target_fingerprint": build_model.fingerprint,
            "source": build_model.source,
        },
    }


def planner_impact_candidates(
    dependency_graph: dict[str, Any],
    inventory: dict[str, InventoryTarget],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for node in dependency_graph.get("nodes", []):
        semantic_id = str(node.get("semantic_id") or "")
        body = next(
            (
                item
                for item in inventory.values()
                if item.kind == "function_body"
                and item.semantic_id == semantic_id
            ),
            None,
        )
        metadata = next(
            (
                item
                for item in inventory.values()
                if item.kind == "cad_part_metadata"
                and item.semantic_id == semantic_id
            ),
            None,
        )
        if body is None or metadata is None:
            continue
        candidates.append(
            {
                **node,
                "function_body_fingerprint": body.fingerprint,
                "metadata_target_id": metadata.target_id,
                "metadata_target_fingerprint": metadata.fingerprint,
            }
        )
    return candidates


def _annotate_initial_model(model_body: str) -> str:
    if "CAD-AGENT-" in model_body or "PART-START:" in model_body or "PART-END:" in model_body:
        raise WorkflowFailure(
            "INVALID_TOOL_PLAN",
            "Initial model source cannot supply server-owned provenance markers.",
        )
    normalized_body = model_body.strip()
    try:
        body_tree = ast.parse(normalized_body)
    except SyntaxError as exc:
        raise WorkflowFailure(
            "INVALID_TOOL_PLAN",
            f"Initial model body is invalid Python: {exc.msg}",
        ) from exc
    if any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(body_tree)):
        raise WorkflowFailure(
            "INVALID_TOOL_PLAN",
            "Initial model body cannot add imports.",
        )
    model_params = [
        node
        for node in body_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ModelParams"
    ]
    build_models = [
        node
        for node in body_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_model"
    ]
    if len(model_params) != 1 or len(build_models) != 1:
        raise WorkflowFailure(
            "INVALID_TOOL_PLAN",
            "Initial model body requires exactly one ModelParams and build_model.",
        )
    fields = [
        node
        for node in model_params[0].body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    if not fields:
        raise WorkflowFailure(
            "INVALID_TOOL_PLAN",
            "Initial ModelParams requires at least one annotated field.",
        )
    features: list[tuple[ast.FunctionDef, str]] = []
    for node in body_tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "cad_part"
            ):
                continue
            semantic_id = next(
                (
                    str(keyword.value.value)
                    for keyword in decorator.keywords
                    if keyword.arg == "semantic_id"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ),
                None,
            )
            if semantic_id:
                features.append((node, semantic_id))
    if not features:
        raise WorkflowFailure(
            "INVALID_TOOL_PLAN",
            "Initial model body requires at least one cad_part feature.",
        )

    body = normalized_body + "\n"
    offsets = _line_offsets(body)
    changes: list[tuple[int, str]] = []
    for field in fields:
        name = field.target.id
        indentation = " " * field.col_offset
        changes.append(
            (
                offsets[field.lineno - 1],
                f"{indentation}# CAD-AGENT-START: model_parameter:{name}\n",
            )
        )
        changes.append(
            (
                offsets[field.end_lineno or field.lineno],
                f"{indentation}# CAD-AGENT-END: model_parameter:{name}\n",
            )
        )
    for node in body_tree.body:
        if not (
            isinstance(node, ast.FunctionDef)
            and node.name.startswith("_")
            and not node.name.startswith("__")
        ):
            continue
        changes.append(
            (
                offsets[node.lineno - 1],
                f"# CAD-AGENT-START: private_helper:{node.name}\n",
            )
        )
        changes.append(
            (
                offsets[node.end_lineno or node.lineno],
                f"# CAD-AGENT-END: private_helper:{node.name}\n",
            )
        )
    for function, semantic_id in features:
        start_line = min(
            [function.lineno, *(decorator.lineno for decorator in function.decorator_list)]
        )
        changes.append((offsets[start_line - 1], f"# PART-START: {semantic_id}\n"))
        changes.append(
            (
                offsets[function.end_lineno or function.lineno],
                f"# PART-END: {semantic_id}\n",
            )
        )
    for offset, insertion in sorted(changes, reverse=True):
        body = body[:offset] + insertion + body[offset:]
    candidate = RUNTIME_IMPORT + "\n" + body
    ast.parse(candidate)
    return candidate


def _validate_system_contract(source: str) -> None:
    if not source.startswith(RUNTIME_IMPORT):
        raise WorkflowFailure(
            "SYSTEM_SOURCE_MODIFIED",
            "The system-owned cadquery_runtime import was changed.",
        )
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise WorkflowFailure(
            "INVALID_EDIT_PLAN",
            f"Applied candidate is invalid Python: {exc.msg}",
        ) from exc
    imports = [
        node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    if len(imports) != 1 or not isinstance(imports[0], ast.ImportFrom):
        raise WorkflowFailure(
            "SYSTEM_SOURCE_MODIFIED",
            "CAD source may contain only the system-owned runtime import.",
        )
    classes = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ModelParams"
    ]
    builds = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_model"
    ]
    if len(classes) != 1 or len(builds) != 1:
        raise WorkflowFailure(
            "REQUIRED_CONTRACT_DELETED",
            "ModelParams and build_model must each exist exactly once.",
        )


def _legacy_plan(plan: ToolPlan, operations: list[Any], semantic_ids: list[str]) -> EditPlan:
    payloads: list[dict[str, Any]] = []
    for operation in operations:
        payload = operation.model_dump(mode="json")
        payload["operation"] = payload.pop("tool")
        payload.pop("target_fingerprint", None)
        payloads.append(payload)
    return EditPlan.model_validate(
        {
            "summary": plan.summary,
            "target_semantic_ids": semantic_ids,
            "operations": payloads,
        }
    )


def _changed_symbols(plan: ToolPlan, inventory: dict[str, InventoryTarget]) -> list[str]:
    values: list[str] = []
    for operation in plan.operations:
        for attribute in ("name", "function_name", "semantic_id"):
            value = getattr(operation, attribute, None)
            if isinstance(value, str) and value not in values:
                values.append(value)
        target_id = getattr(operation, "target_id", None)
        if target_id in inventory:
            value = inventory[target_id].name
            if value not in values:
                values.append(value)
    return values


def _normalize_feature_operations(
    operations: list[Any],
    *,
    inventory: dict[str, InventoryTarget],
    part_id: str,
) -> list[Any]:
    """Resolve semantic feature edits to the current function-body target."""
    normalized: list[Any] = []
    for operation in operations:
        if isinstance(operation, ReplaceCadFeatureBody):
            try:
                target = resolve_feature_body_span(inventory, operation.semantic_id)
            except WorkflowFailure as error:
                if error.code != "TARGET_NOT_FOUND":
                    raise
                raise WorkflowFailure(
                    "FEATURE_NOT_FOUND_FOR_REPLACEMENT",
                    f'CAD feature semantic ID "{operation.semantic_id}" does not exist; create it with add_cad_feature.',
                    details={
                        "semantic_id": operation.semantic_id,
                        "suggested_operation": "add_cad_feature",
                        "repairable_hint": True,
                    },
                ) from error
            normalized.append(
                ReplaceFunctionBody(
                    tool="replace_function_body",
                    target_id=target.target_id,
                    target_fingerprint=operation.target_fingerprint,
                    replacement_source=operation.replacement_source,
                )
            )
            continue

        target_id = getattr(operation, "target_id", None)
        target = inventory.get(target_id) if target_id else None
        if isinstance(operation, ReplaceFunctionBody) and target is not None:
            if target.kind == "cad_feature":
                semantic_id = target.semantic_id or target.name
                feature_body = resolve_feature_body_span(inventory, semantic_id)
                raise WorkflowFailure(
                    "FEATURE_REPLACEMENT_TARGET_INVALID",
                    "replace_function_body cannot target a cad_feature provenance "
                    f'block; use replace_cad_feature_body with semantic ID "{semantic_id}".',
                    details={
                        "invalid_target_id": target.target_id,
                        "semantic_id": semantic_id,
                        "suggested_operation": "replace_cad_feature_body",
                        "suggested_target_fingerprint": feature_body.fingerprint,
                        "repairable_hint": True,
                    },
                )
        normalized.append(operation)
    return normalized


def _preflight_feature_plan(
    operations: list[Any],
    *,
    inventory: dict[str, InventoryTarget],
) -> None:
    existing_semantic_ids = {
        item.semantic_id
        for item in inventory.values()
        if item.kind == "function_body" and item.semantic_id
    }
    additions = [
        operation for operation in operations if isinstance(operation, AddCadFeature)
    ]
    for operation in additions:
        if operation.semantic_id in existing_semantic_ids:
            raise WorkflowFailure(
                "FEATURE_ALREADY_EXISTS_FOR_CREATE",
                f'CAD feature semantic ID "{operation.semantic_id}" already exists; modify it with replace_cad_feature_body.',
                details={
                    "semantic_id": operation.semantic_id,
                    "suggested_operation": "replace_cad_feature_body",
                    "repairable_hint": True,
                },
            )
    if not additions:
        return
    build_updates = [
        operation
        for operation in operations
        if isinstance(operation, ReplaceBuildModelBody)
    ]
    missing = [
        operation.semantic_id
        for operation in additions
        if not any(
            re.search(
                rf"\b{re.escape(operation.function_name)}\s*\(",
                update.replacement_source,
            )
            for update in build_updates
        )
    ]
    if missing:
        raise WorkflowFailure(
            "NEW_FEATURE_NOT_ASSEMBLED",
            "Every new CAD feature must be invoked by the same plan's build_model replacement: "
            + ", ".join(missing),
            details={
                "semantic_ids": missing,
                "suggested_operation": "replace_build_model_body",
                "repairable_hint": True,
            },
        )


def _source_dependency_graph(
    source: str,
    *,
    inventory: dict[str, InventoryTarget],
) -> dict[str, Any]:
    metadata_targets = [
        target
        for target in inventory.values()
        if target.kind == "cad_part_metadata" and target.semantic_id
    ]
    functions = [
        node
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    references = effective_parameter_references(functions)
    features = [
        {
            "semantic_id": target.semantic_id,
            "function_name": next(
                (
                    body.name
                    for body in inventory.values()
                    if body.kind == "function_body"
                    and body.semantic_id == target.semantic_id
                ),
                target.semantic_id,
            ),
            "role": target.details.get("role"),
            "parameters": list(target.details.get("parameters") or []),
            "parameter_references": references.get(
                next(
                    (
                        body.name
                        for body in inventory.values()
                        if body.kind == "function_body"
                        and body.semantic_id == target.semantic_id
                    ),
                    "",
                ),
                [],
            ),
            "depends_on": list(target.details.get("depends_on") or []),
        }
        for target in metadata_targets
    ]
    return graph_context(features)


def _validate_impact_review(
    source: str,
    plan: ToolPlan,
    *,
    inventory: dict[str, InventoryTarget],
) -> None:
    if plan.schema_version == 1:
        return
    reviews = list(plan.impact_review or [])
    review_ids = [review.semantic_id for review in reviews]
    if len(review_ids) != len(set(review_ids)):
        raise WorkflowFailure(
            "IMPACT_REVIEW_INVALID",
            "ToolPlan impact_review must contain each semantic ID at most once.",
            details={"repairable_hint": True},
        )

    graph = _source_dependency_graph(source, inventory=inventory)
    nodes = {
        str(node["semantic_id"]): node
        for node in graph["nodes"]
    }
    impacted: set[str] = set()
    modified: set[str] = set()
    changed_parameters: set[str] = set()

    for operation in plan.operations:
        if isinstance(operation, ReplaceCadFeatureBody):
            modified.add(operation.semantic_id)
        target_id = getattr(operation, "target_id", None)
        target = inventory.get(target_id) if target_id else None
        if isinstance(operation, ReplaceParameterField) and target is not None:
            changed_parameters.add(target.name)
        elif isinstance(operation, DeleteModelParameter) and target is not None:
            changed_parameters.add(target.name)
        elif isinstance(operation, (UpdateCadPartMetadata, DeleteCadFeature)):
            if target is not None and target.semantic_id:
                modified.add(target.semantic_id)
        elif isinstance(operation, ReplaceFunctionBody):
            if target is not None and target.semantic_id:
                modified.add(target.semantic_id)

    roots = set(modified)
    for parameter in changed_parameters:
        roots.update(graph["parameter_consumers"].get(parameter, []))
    for root in roots:
        if root not in nodes:
            continue
        impacted.add(root)
        impacted.update(nodes[root]["transitive_dependents"])

    supplied = set(review_ids)
    missing = sorted(impacted - supplied)
    extra = sorted(supplied - impacted)
    if missing or extra:
        raise WorkflowFailure(
            "IMPACT_REVIEW_INCOMPLETE",
            "ToolPlan impact_review must exactly cover the server-derived impact set; "
            f"missing={missing}, extra={extra}.",
            details={
                "missing_semantic_ids": missing,
                "extra_semantic_ids": extra,
                "suggested_operation": "review_dependency_impact",
                "repairable_hint": True,
            },
        )
    for review in reviews:
        is_modified = review.semantic_id in modified
        if review.decision == "modified" and not is_modified:
            raise WorkflowFailure(
                "IMPACT_REVIEW_INVALID",
                f'Impact review marks "{review.semantic_id}" modified without a matching feature operation.',
                details={"repairable_hint": True},
            )
        if review.decision == "verified_compatible" and is_modified:
            raise WorkflowFailure(
                "IMPACT_REVIEW_INVALID",
                f'Impact review must mark explicitly edited feature "{review.semantic_id}" as modified.',
                details={"repairable_hint": True},
            )


class CadToolExecutor:
    def __init__(self, repository: Any):
        self.repository = repository

    def prepare_context(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = dict(job.get("input") or {})
        project_id = str(job["project_id"])
        requested_part_id = payload.get("requested_part_id")
        workflow_mode = str(payload.get("workflow_mode") or "edit")
        request = str(payload.get("request_text") or "").strip()
        messages = list(payload.get("messages") or [])

        if requested_part_id is not None:
            source_file = self.repository.source(project_id, str(requested_part_id))
            if workflow_mode == "initial_design":
                if source_file.content != RUNTIME_IMPORT:
                    raise WorkflowFailure(
                        "INITIAL_SOURCE_CHANGED",
                        "The linked part is no longer the exact blank CAD marker.",
                    )
                return {
                    "schema_version": 1,
                    "source_state": "blank",
                    "part_id": source_file.part_id,
                    "part_name": source_file.part_name,
                    "storage_path": source_file.storage_path,
                    "base_source_sha256": source_file.content_hash,
                    "semantic_ids": [],
                    "allowed_targets": [],
                    "deletable_targets": [],
                    "existing_features": [],
                    "existing_parameters": [],
                    "allowed_dependencies": [],
                    "build_model_target": None,
                    "dependency_graph": {
                        "nodes": [],
                        "parameter_consumers": {},
                    },
                    "impact_candidates": [],
                    "metadata_warnings": [],
                    "conversation": messages[-8:],
                    "reason": "Linked blank CAD part selected for initial design.",
                    "confidence": 1,
                }

        getter = self.repository.getter(project_id)
        if getter.freshness()["status"] != "fresh":
            raise WorkflowFailure("STALE_INDEX", "Project index is not fresh.")
        if requested_part_id is not None:
            part = getter.parts.get(str(requested_part_id))
            if part is None:
                raise WorkflowFailure(
                    "TARGET_NOT_FOUND",
                    "The linked CAD part is missing from the fresh index.",
                )
            semantic_ids = [
                str(feature["semantic_id"]) for feature in part.get("cad_parts", [])
            ]
            target = ResolvedEditTarget(
                part_id=str(requested_part_id),
                part_name=str(part["part_name"]),
                semantic_ids=semantic_ids,
                confidence=1,
                reason="The linked CAD part is the authoritative target.",
                candidates=[],
            )
        else:
            matches = getter.search_parts(request, 5)
            if not matches:
                raise WorkflowFailure(
                    "NO_INDEXED_TARGET",
                    "No indexed CAD feature matched the request; link a CAD part explicitly.",
                )
            top = matches[0]
            next_score = float(matches[1]["score"]) if len(matches) > 1 else 0
            if float(top["score"]) < 0.80 or float(top["score"]) - next_score < 0.10:
                raise WorkflowFailure(
                    "AMBIGUOUS_TARGET",
                    "The request matches multiple CAD parts; link the intended part explicitly.",
                    details={"candidates": matches},
                )
            part_id = str(top["part_id"])
            graph = getter.dependency_graph(part_id)
            selected_semantic_id = str(top["semantic_id"])
            selected_node = next(
                node
                for node in graph["nodes"]
                if node["semantic_id"] == selected_semantic_id
            )
            expanded_ids = {
                selected_semantic_id,
                *selected_node["transitive_dependencies"],
                *selected_node["transitive_dependents"],
            }
            part = getter.parts[part_id]
            ordered_ids = [
                str(feature["semantic_id"])
                for feature in part.get("cad_parts", [])
                if str(feature["semantic_id"]) in expanded_ids
            ]
            target = ResolvedEditTarget(
                part_id=part_id,
                part_name=str(top["part_name"]),
                semantic_ids=ordered_ids,
                confidence=float(top["score"]),
                reason="The fresh Getter produced one high-confidence target.",
                candidates=matches,
            )
        context = build_edit_context(
            getter,
            request=request,
            messages=messages,
            target=target,
        ).model_dump(mode="json")
        source = getter.sources[target.part_id]
        inventory = target_inventory(
            source.content,
            part_id=target.part_id,
            semantic_ids=target.semantic_ids,
        )
        dependency_graph = getter.dependency_graph(target.part_id)
        context.update(
            {
                "schema_version": 1,
                "storage_path": source.storage_path,
                "base_source_sha256": source.content_hash,
                "allowed_targets": [
                    item.public(target.part_id)
                    for item in sorted(inventory.values(), key=lambda value: value.start)
                    if not item.deletable
                ],
                "deletable_targets": [
                    item.public(target.part_id)
                    for item in sorted(inventory.values(), key=lambda value: value.start)
                    if item.deletable
                ],
                "confidence": target.confidence,
                "reason": target.reason,
                "candidates": target.candidates,
                "dependency_graph": dependency_graph,
                "metadata_warnings": list(
                    getter.parts[target.part_id].get("metadata_warnings", [])
                ),
                "impact_candidates": planner_impact_candidates(
                    dependency_graph,
                    inventory,
                ),
                **planner_inventory(
                    inventory,
                    semantic_ids=target.semantic_ids,
                    parameters=list(
                        getter.parts[target.part_id].get("model_params", [])
                    ),
                    dependency_graph=dependency_graph,
                ),
            }
        )
        return context

    def prepare_repair_context(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = dict(job.get("input") or {})
        candidate_path = str(payload.get("candidate_path") or "")
        candidate_hash = str(payload.get("candidate_sha256") or "")
        previous_plan = payload.get("previous_plan")
        repair_source = str(payload.get("repair_source") or "")
        if not isinstance(previous_plan, dict) or not previous_plan:
            raise WorkflowFailure(
                "REPAIR_CONTEXT_INCOMPLETE",
                "Validation repair requires the exact previous ToolPlan.",
            )
        if repair_source != "validation":
            raise WorkflowFailure(
                "REPAIR_CONTEXT_INCOMPLETE",
                "Candidate repair must identify validation as its source.",
            )
        expected_prefix = (
            f"{job['project_id']}/candidates/cad/{job['part_id']}/"
            f"{job['edit_job_id']}/attempt-"
        )
        if not candidate_path.startswith(expected_prefix) or not candidate_path.endswith(
            "/model.py"
        ):
            raise WorkflowFailure(
                "OUT_OF_SCOPE_CANDIDATE",
                "Repair candidate path does not belong to this edit job.",
            )
        source = self.repository.read_text(candidate_path)
        if source_hash(source) != candidate_hash:
            raise WorkflowFailure(
                "CANDIDATE_HASH_MISMATCH",
                "Repair candidate no longer matches its recorded hash.",
            )
        semantic_ids = semantic_ids_in_source(source)
        inventory = target_inventory(
            source,
            part_id=str(job["part_id"]),
            semantic_ids=semantic_ids,
        )
        dependency_graph = _source_dependency_graph(source, inventory=inventory)
        metadata_warnings = []
        for node in dependency_graph["nodes"]:
            declared = set(node["parameters"])
            inferred = set(node["parameter_references"])
            if declared != inferred:
                metadata_warnings.append(
                    {
                        "code": "parameter_metadata_mismatch",
                        "semantic_id": node["semantic_id"],
                        "missing_parameters": sorted(inferred - declared),
                        "stale_parameters": sorted(declared - inferred),
                    }
                )
        part = self.repository.source(str(job["project_id"]), str(job["part_id"]))
        return {
            "schema_version": 1,
            "part_id": str(job["part_id"]),
            "part_name": part.part_name,
            "storage_path": candidate_path,
            "base_source_sha256": candidate_hash,
            "candidate_source": source,
            "semantic_ids": semantic_ids,
            "allowed_targets": [
                item.public(str(job["part_id"]))
                for item in sorted(inventory.values(), key=lambda value: value.start)
                if not item.deletable
            ],
            "deletable_targets": [
                item.public(str(job["part_id"]))
                for item in sorted(inventory.values(), key=lambda value: value.start)
                if item.deletable
            ],
            "dependency_graph": dependency_graph,
            "impact_candidates": planner_impact_candidates(
                dependency_graph,
                inventory,
            ),
            "metadata_warnings": metadata_warnings,
            "previous_plan": previous_plan,
            "repair_source": repair_source,
            "validation": dict(payload.get("validation") or {}),
        }

    def apply_plan(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = dict(job.get("input") or {})
        try:
            plan = ToolPlan.model_validate(payload.get("plan"))
        except ValidationError as exc:
            raise WorkflowFailure(
                "INVALID_TOOL_PLAN",
                f"CAD tool plan does not match schema version 1: {exc.errors()[0]['msg']}",
            ) from exc
        workflow_mode = str(payload.get("workflow_mode") or "edit")
        project_id = str(job["project_id"])
        part_id = str(job["part_id"])
        edit_job_id = str(job["edit_job_id"])
        attempt = int(job["attempt"])
        base_path = str(payload.get("base_storage_path") or "")
        canonical_path = self.repository.canonical_source_path(project_id, part_id)
        candidate_prefix = f"{project_id}/candidates/cad/{part_id}/{edit_job_id}/"
        if base_path != canonical_path and not base_path.startswith(candidate_prefix):
            raise WorkflowFailure(
                "OUT_OF_SCOPE_SOURCE",
                "CAD tool plan base source does not belong to its part and edit job.",
            )
        source = self.repository.read_text(base_path)
        actual_hash = source_hash(source)
        if actual_hash != plan.base_source_sha256:
            raise WorkflowFailure(
                "SOURCE_CHANGED",
                "CAD tool plan base source is stale.",
                details={
                    "expected_hash": plan.base_source_sha256,
                    "actual_hash": actual_hash,
                },
            )
        if plan.target_part_id != part_id:
            raise WorkflowFailure(
                "OUT_OF_SCOPE_TOOL_PLAN",
                "CAD tool plan targets another part.",
            )

        initial_operations = [
            operation
            for operation in plan.operations
            if isinstance(operation, WriteInitialModel)
        ]
        if workflow_mode == "initial_design":
            if len(plan.operations) != 1 or len(initial_operations) != 1:
                raise WorkflowFailure(
                    "INVALID_TOOL_PLAN",
                    "Initial design requires one write_initial_model operation.",
                )
            if plan.schema_version == 2 and plan.impact_review:
                raise WorkflowFailure(
                    "IMPACT_REVIEW_INVALID",
                    "Initial design has no accepted-source features to review.",
                )
            candidate = _annotate_initial_model(initial_operations[0].model_body)
            changed_symbols = [
                "ModelParams",
                *semantic_ids_in_source(candidate),
                "build_model",
            ]
            normalization_notes = []
        else:
            if initial_operations:
                raise WorkflowFailure(
                    "INVALID_TOOL_PLAN",
                    "Established CAD source cannot use whole-model replacement.",
                )
            semantic_ids = semantic_ids_in_source(source)
            inventory = target_inventory(
                source,
                part_id=part_id,
                semantic_ids=semantic_ids,
            )
            confirmations = [
                operation
                for operation in plan.operations
                if isinstance(operation, ConfirmNoChange)
            ]
            if confirmations:
                confirmation = confirmations[0]
                evidence_ids = [item.semantic_id for item in confirmation.evidence]
                if len(evidence_ids) != len(set(evidence_ids)):
                    raise WorkflowFailure(
                        "NO_CHANGE_EVIDENCE_INVALID",
                        "confirm_no_change evidence must contain each semantic ID at most once.",
                    )
                verified_evidence: list[dict[str, str]] = []
                for evidence in confirmation.evidence:
                    try:
                        target = resolve_feature_body_span(
                            inventory, evidence.semantic_id
                        )
                    except WorkflowFailure as error:
                        if error.code != "TARGET_NOT_FOUND":
                            raise
                        raise WorkflowFailure(
                            "NO_CHANGE_EVIDENCE_INVALID",
                            f'No current CAD feature has semantic ID "{evidence.semantic_id}".',
                        ) from error
                    if target.fingerprint != evidence.target_fingerprint:
                        raise WorkflowFailure(
                            "NO_CHANGE_EVIDENCE_INVALID",
                            f'CAD feature "{evidence.semantic_id}" changed after the no-change review.',
                        )
                    verified_evidence.append(
                        {
                            "semantic_id": evidence.semantic_id,
                            "target_fingerprint": target.fingerprint,
                            "reason": evidence.reason,
                        }
                    )
                _validate_system_contract(source)
                return {
                    "schema_version": 1,
                    "outcome": "no_change",
                    "base_source_sha256": actual_hash,
                    "changed_symbols": [],
                    "semantic_ids": semantic_ids,
                    "evidence": verified_evidence,
                    "summary": plan.summary,
                    "reason": confirmation.reason,
                }
            _validate_impact_review(source, plan, inventory=inventory)
            _preflight_feature_plan(list(plan.operations), inventory=inventory)
            operations = _normalize_feature_operations(
                list(plan.operations), inventory=inventory, part_id=part_id
            )
            targeted = [
                operation
                for operation in operations
                if hasattr(operation, "target_id")
            ]
            targeted_ids = [operation.target_id for operation in targeted]
            if len(targeted_ids) != len(set(targeted_ids)):
                raise WorkflowFailure(
                    "OVERLAPPING_TOOL_OPERATIONS",
                    "A tool plan cannot target the same owned element more than once.",
                )
            for operation in targeted:
                target = inventory.get(operation.target_id)
                if target is None:
                    raise WorkflowFailure(
                        "OUT_OF_SCOPE_TOOL_PLAN",
                        f'Target "{operation.target_id}" is not in the bounded context.',
                    )
                if (
                    isinstance(operation, ReplaceParameterField)
                    and target.kind != "model_parameter"
                ):
                    editable = next(
                        (
                            item
                            for item in inventory.values()
                            if item.kind == "model_parameter"
                            and item.name == target.name
                        ),
                        None,
                    )
                    raise WorkflowFailure(
                        "PARAMETER_REPLACEMENT_TARGET_INVALID",
                        "replace_parameter_field must use the editable model_parameter target, "
                        "not its provenance-owned deletion target.",
                        details={
                            "parameter_name": target.name,
                            "suggested_operation": "replace_parameter_field",
                            "suggested_target_id": (
                                editable.target_id if editable is not None else None
                            ),
                            "repairable_hint": True,
                        },
                    )
                if target.fingerprint != operation.target_fingerprint:
                    raise WorkflowFailure(
                        "STALE_TARGET_FINGERPRINT",
                        f'Target "{operation.target_id}" changed after planning.',
                    )

            deletions = [
                operation
                for operation in operations
                if isinstance(
                    operation,
                    (DeleteModelParameter, DeletePrivateHelper, DeleteCadFeature),
                )
            ]
            modifications = [
                operation
                for operation in operations
                if isinstance(
                    operation,
                    (
                        ReplaceParameterField,
                        UpdateCadPartMetadata,
                        ReplaceFunctionBody,
                        ReplaceBuildModelBody,
                    ),
                )
            ]
            deleted_names = {inventory[operation.target_id].name for operation in deletions}
            for operation in modifications:
                target = inventory[operation.target_id]
                if target.name in deleted_names or (
                    target.semantic_id
                    and any(
                        inventory[deleted.target_id].semantic_id == target.semantic_id
                        for deleted in deletions
                    )
                ):
                    raise WorkflowFailure(
                        "OVERLAPPING_TOOL_OPERATIONS",
                        "A plan cannot modify and delete the same owned element.",
                    )
            non_delete = [
                operation
                for operation in operations
                if not isinstance(
                    operation,
                    (DeleteModelParameter, DeletePrivateHelper, DeleteCadFeature),
                )
            ]
            candidate = source
            normalization_notes = []
            if non_delete:
                legacy = _legacy_plan(plan, non_delete, semantic_ids)
                applied = apply_edit_plan(
                    source,
                    expected_hash=actual_hash,
                    part_id=part_id,
                    semantic_ids=semantic_ids,
                    plan=legacy,
                    extra_function_names=_private_function_names(source),
                )
                candidate = applied.content
                normalization_notes = applied.normalization_notes
            if deletions:
                current_semantic_ids = semantic_ids_in_source(candidate)
                current_inventory = target_inventory(
                    candidate,
                    part_id=part_id,
                    semantic_ids=current_semantic_ids,
                )
                changes: list[tuple[int, int]] = []
                for operation in deletions:
                    target = current_inventory.get(operation.target_id)
                    if target is None or not target.deletable:
                        raise WorkflowFailure(
                            "DELETE_NOT_OWNED",
                            "Only provenance-marked CAD elements may be deleted.",
                        )
                    expected_kind = (
                        "owned_model_parameter"
                        if isinstance(operation, DeleteModelParameter)
                        else "owned_private_helper"
                        if isinstance(operation, DeletePrivateHelper)
                        else "cad_feature"
                    )
                    if target.kind != expected_kind:
                        raise WorkflowFailure(
                            "INVALID_TOOL_PLAN",
                            f"{operation.tool} targets the wrong element kind.",
                        )
                    changes.append((target.start, target.end))
                for start, end in sorted(changes, reverse=True):
                    candidate = candidate[:start] + candidate[end:]
            changed_symbols = _changed_symbols(
                plan.model_copy(update={"operations": operations}), inventory
            )

        _validate_system_contract(candidate)
        candidate_hash = source_hash(candidate)
        candidate_path = self.repository.candidate_path(
            project_id,
            part_id,
            edit_job_id,
            attempt,
        )
        self.repository.write_text(candidate_path, candidate)
        self.repository.verify_text_hash(candidate_path, candidate_hash)
        return {
            "schema_version": 1,
            "candidate_path": candidate_path,
            "candidate_sha256": candidate_hash,
            "base_source_sha256": actual_hash,
            "changed_symbols": changed_symbols,
            "normalization_notes": normalization_notes,
            "semantic_ids": semantic_ids_in_source(candidate),
            "applied_operations": [
                operation.model_dump(mode="json") for operation in plan.operations
            ],
            "summary": plan.summary,
        }

    def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        kind = str(job["kind"])
        if kind == "prepare_context":
            return self.prepare_context(job)
        if kind == "prepare_repair_context":
            return self.prepare_repair_context(job)
        if kind == "apply_plan":
            return self.apply_plan(job)
        raise WorkflowFailure("UNKNOWN_TOOL_JOB", f'Unsupported CAD tool job "{kind}".')
