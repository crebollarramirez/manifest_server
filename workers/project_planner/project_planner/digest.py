from __future__ import annotations

import hashlib
import json

from .contracts import AssemblySpec


def canonical_assembly_spec_json(spec: AssemblySpec) -> str:
    """Deterministic JSON text for an AssemblySpec's semantic content only.

    Excludes purely administrative fields (spec_id, schema_version,
    project_id) and every server-generated random identifier with no
    reproducibility across separate build_assembly_spec() calls (node_id,
    interface_id) -- cross-references to node_id are rewritten to the
    referenced node's stable semantic_ref instead, so the digest still
    captures the actual connectivity graph, addressed by name rather than
    random UUID. sort_keys=True makes the output independent of key
    ordering, mirroring workers/agent_3d/planning/agent_trace.py's
    content_sha256 pattern (json.dumps(..., sort_keys=True)) applied to a
    structure instead of a flat string.
    """
    payload = spec.model_dump(mode="json")
    payload.pop("spec_id", None)
    payload.pop("schema_version", None)
    payload.pop("project_id", None)

    node_id_to_ref = {node["node_id"]: node["semantic_ref"] for node in payload["nodes"]}
    for node in payload["nodes"]:
        node.pop("node_id", None)

    for interface in payload["interfaces"]:
        interface.pop("interface_id", None)
        for endpoint in (interface["endpoint_a"], interface["endpoint_b"]):
            endpoint["node_ref"] = node_id_to_ref[endpoint.pop("node_id")]

    for dependency in payload["execution_dependencies"]:
        dependency.pop("prerequisite_node_id", None)
        dependency.pop("dependent_node_id", None)
        # prerequisite_ref/dependent_ref already carry the stable refs.

    return json.dumps(payload, sort_keys=True)


def compute_definition_digest(spec: AssemblySpec) -> str:
    """SHA-256 hex digest of canonical_assembly_spec_json(spec). Mirrors
    workers/agent_3d/tools/hashing.py's source_hash /
    agent_trace.py's content_sha256 -- the same hashlib.sha256(...).hexdigest()
    call, just over a canonicalized structure instead of raw text."""
    canonical = canonical_assembly_spec_json(spec)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
