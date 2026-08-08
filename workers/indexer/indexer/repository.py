from __future__ import annotations

import json
from typing import Any

from workers.commons.storage import (
    BUCKET,
    INDEX_FILENAME,
    INITIAL_CAD_SOURCE,
    cad_source_storage_path,
    index_storage_path,
    is_uninitialized_cad_source,
)

from .models import SourceFile

__all__ = [
    "BUCKET",
    "INDEX_FILENAME",
    "INITIAL_CAD_SOURCE",
    "SupabaseProjectRepository",
    "cad_source_storage_path",
    "index_storage_path",
    "is_uninitialized_cad_source",
]


class SupabaseProjectRepository:
    def __init__(self, supabase: Any):
        self.supabase = supabase

    def project_name(self, project_id: str) -> str:
        response = (
            self.supabase.table("projects")
            .select("project_name")
            .eq("id", project_id)
            .limit(1)
            .execute()
        )
        if not response.data:
            raise ValueError(f'Project "{project_id}" no longer exists.')
        return str(response.data[0]["project_name"])

    def list_cad_sources(self, project_id: str) -> list[SourceFile]:
        response = (
            self.supabase.table("parts")
            .select("id, part_name")
            .eq("project_id", project_id)
            .eq("part_type", "cad")
            .order("part_name")
            .order("id")
            .execute()
        )
        sources: list[SourceFile] = []
        for part in response.data or []:
            part_id = str(part["id"])
            path = cad_source_storage_path(project_id, part_id)
            raw_source = self.supabase.storage.from_(BUCKET).download(path)
            try:
                content = raw_source.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"{path} is not valid UTF-8.") from exc
            if is_uninitialized_cad_source(content):
                continue
            sources.append(
                SourceFile.from_content(
                    part_id=part_id,
                    part_name=str(part["part_name"]),
                    storage_path=path,
                    content=content,
                )
            )
        return sources

    def cad_source(self, project_id: str, part_id: str) -> SourceFile:
        response = (
            self.supabase.table("parts")
            .select("id, part_name")
            .eq("project_id", project_id)
            .eq("id", part_id)
            .eq("part_type", "cad")
            .limit(1)
            .execute()
        )
        if not response.data:
            raise ValueError(
                f'CAD part "{part_id}" does not exist in project "{project_id}".'
            )
        part = response.data[0]
        path = cad_source_storage_path(project_id, part_id)
        raw_source = self.supabase.storage.from_(BUCKET).download(path)
        try:
            content = raw_source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{path} is not valid UTF-8.") from exc
        return SourceFile.from_content(
            part_id=part_id,
            part_name=str(part["part_name"]),
            storage_path=path,
            content=content,
        )

    def read_index(self, project_id: str) -> dict | None:
        directory = f"{project_id}/index"
        entries = self.supabase.storage.from_(BUCKET).list(directory)
        names = {
            str(entry.get("name"))
            for entry in entries
            if isinstance(entry, dict)
        }
        if INDEX_FILENAME not in names:
            return None
        raw_index = self.supabase.storage.from_(BUCKET).download(
            index_storage_path(project_id)
        )
        parsed = json.loads(raw_index.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("Stored semantic index must be a JSON object.")
        return parsed

    def write_index(self, project_id: str, index: dict) -> str:
        path = index_storage_path(project_id)
        payload = (
            json.dumps(index, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        ).encode("utf-8")
        self.supabase.storage.from_(BUCKET).upload(
            path=path,
            file=payload,
            file_options={
                "content-type": "application/json",
                "upsert": "true",
            },
        )
        return path

    def create_getter(self, project_id: str):
        from .getter import IndexGetter

        def load() -> tuple[dict, list[SourceFile]]:
            index = self.read_index(project_id)
            if index is None:
                raise ValueError(f'Project "{project_id}" does not have an index.')
            return index, self.list_cad_sources(project_id)

        index, sources = load()
        return IndexGetter(index, sources, loader=load)
