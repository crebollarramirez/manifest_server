from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from .models import SourceFile


MIN_SEARCH_SCORE = 0.45
FIELD_WEIGHTS = {
    "part_name": 0.8,
    "semantic_id": 1.0,
    "search_keys": 1.0,
    "role": 0.97,
    "function_name": 0.95,
    "parameters": 0.95,
}


def _normalize(value: str) -> str:
    normalized = value.casefold().replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", normalized).strip()


def _score_value(query: str, candidate: str) -> float:
    normalized_query = _normalize(query)
    normalized_candidate = _normalize(candidate)
    if not normalized_query or not normalized_candidate:
        return 0.0
    if normalized_query == normalized_candidate:
        return 1.0
    if normalized_candidate in normalized_query:
        return 0.96
    if normalized_query in normalized_candidate:
        return 0.92

    query_tokens = set(normalized_query.split())
    candidate_tokens = set(normalized_candidate.split())
    overlap = len(query_tokens & candidate_tokens)
    token_score = overlap / len(candidate_tokens) if candidate_tokens else 0.0
    similarity = SequenceMatcher(
        None,
        normalized_query,
        normalized_candidate,
    ).ratio()
    if len(candidate_tokens) > 1 and len(query_tokens) >= len(candidate_tokens):
        query_words = normalized_query.split()
        width = len(candidate_tokens)
        similarity = max(
            similarity,
            *(
                SequenceMatcher(
                    None,
                    " ".join(query_words[index:index + width]),
                    normalized_candidate,
                ).ratio()
                for index in range(len(query_words) - width + 1)
            ),
        )
    return max(token_score * 0.9, similarity * 0.9)


class IndexGetter:
    def __init__(self, index: dict, sources: list[SourceFile]):
        self.index = index
        self.sources = {source.part_id: source for source in sources}
        self.parts = {
            str(part["part_id"]): part
            for part in index.get("parts", [])
        }

    def freshness(self) -> dict[str, Any]:
        indexed = {
            str(record["part_id"]): (
                str(record["part_name"]),
                str(record["content_hash"]),
            )
            for record in self.index.get("files", [])
        }
        current = {
            source.part_id: (source.part_name, source.content_hash)
            for source in self.sources.values()
        }
        if indexed == current:
            return {"status": "fresh"}
        return {
            "status": "stale_index",
            "message": "Run /index <project_id> before retrieving source.",
            "added_part_ids": sorted(current.keys() - indexed.keys()),
            "removed_part_ids": sorted(indexed.keys() - current.keys()),
            "changed_part_ids": sorted(
                part_id
                for part_id in current.keys() & indexed.keys()
                if current[part_id] != indexed[part_id]
            ),
        }

    def search_parts(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for part in self.index.get("parts", []):
            for feature in part.get("cad_parts", []):
                fields: list[tuple[str, str]] = [
                    ("part_name", str(part["part_name"])),
                    ("semantic_id", str(feature["semantic_id"])),
                    ("role", str(feature["role"])),
                    ("function_name", str(feature["function_name"])),
                ]
                fields.extend(
                    ("parameters", str(value))
                    for value in feature.get("parameters", [])
                )
                fields.extend(
                    ("search_keys", str(value))
                    for value in feature.get("search_keys", [])
                )

                scored = [
                    (
                        _score_value(query, value) * FIELD_WEIGHTS[field],
                        field,
                        value,
                    )
                    for field, value in fields
                ]
                score, matched_field, matched_value = max(scored)
                if score < MIN_SEARCH_SCORE:
                    continue
                matches.append(
                    {
                        "part_id": part["part_id"],
                        "part_name": part["part_name"],
                        "semantic_id": feature["semantic_id"],
                        "function_name": feature["function_name"],
                        "score": round(score, 4),
                        "matched_field": matched_field,
                        "matched_value": matched_value,
                    }
                )
        matches.sort(
            key=lambda match: (
                -match["score"],
                str(match["part_name"]).casefold(),
                str(match["semantic_id"]),
            )
        )
        return matches[:limit]

    def get_part(self, part_id: str, semantic_id: str) -> dict | None:
        part = self.parts.get(part_id)
        if not part:
            return None
        return next(
            (
                feature
                for feature in part.get("cad_parts", [])
                if feature["semantic_id"] == semantic_id
            ),
            None,
        )

    def get_part_source(self, part_id: str, semantic_id: str) -> str:
        feature = self.get_part(part_id, semantic_id)
        source = self.sources.get(part_id)
        if feature is None or source is None:
            raise KeyError(f"Unknown indexed CAD part {part_id}:{semantic_id}.")
        lines = source.content.splitlines()
        start = int(feature["decorator_line_start"]) - 1
        end = int(feature["function_line_end"])
        return "\n".join(lines[start:end])

    def get_parameter(self, part_id: str, parameter_name: str) -> dict | None:
        part = self.parts.get(part_id)
        if not part:
            return None
        return next(
            (
                parameter
                for parameter in part.get("model_params", [])
                if parameter["name"] == parameter_name
            ),
            None,
        )

    def get_part_parameters(self, part_id: str, semantic_id: str) -> list[dict]:
        feature = self.get_part(part_id, semantic_id)
        if feature is None:
            return []
        return [
            parameter
            for name in feature.get("parameters", [])
            if (parameter := self.get_parameter(part_id, name)) is not None
        ]

    def get_dependencies(self, part_id: str, semantic_id: str) -> list[dict]:
        feature = self.get_part(part_id, semantic_id)
        if feature is None:
            return []
        dependencies: list[dict] = []
        for dependency_id in feature.get("depends_on", []):
            dependency = self.get_part(part_id, dependency_id)
            if dependency is not None:
                dependencies.append(
                    {
                        "semantic_id": dependency["semantic_id"],
                        "role": dependency["role"],
                        "function_name": dependency["function_name"],
                    }
                )
        return dependencies

    def get_context(self, part_id: str, semantic_id: str) -> dict:
        feature = self.get_part(part_id, semantic_id)
        part = self.parts.get(part_id)
        source = self.sources.get(part_id)
        if feature is None or part is None or source is None:
            raise KeyError(f"Unknown indexed CAD part {part_id}:{semantic_id}.")
        return {
            "target": {
                "part_id": part_id,
                "part_name": part["part_name"],
                "semantic_id": feature["semantic_id"],
                "role": feature["role"],
                "function_name": feature["function_name"],
            },
            "source": self.get_part_source(part_id, semantic_id),
            "parameters": self.get_part_parameters(part_id, semantic_id),
            "dependencies": self.get_dependencies(part_id, semantic_id),
            "file_hash": source.content_hash,
        }

    def test_request(self, query: str, limit: int = 5) -> dict:
        freshness = self.freshness()
        if freshness["status"] != "fresh":
            return {
                "schema_version": 1,
                "query": query,
                **freshness,
            }
        matches = self.search_parts(query, limit)
        if not matches:
            return {
                "schema_version": 1,
                "status": "no_match",
                "query": query,
                "matches": [],
                "context": None,
            }
        top = matches[0]
        return {
            "schema_version": 1,
            "status": "ok",
            "query": query,
            "matches": matches,
            "context": self.get_context(
                str(top["part_id"]),
                str(top["semantic_id"]),
            ),
        }
