from __future__ import annotations

from .getter import IndexGetter
from .index_builder import build_project_index, index_summary
from .models import SourceFile
from .repository import SupabaseProjectRepository, index_storage_path


def _source_signature(sources: list[SourceFile]) -> dict[str, tuple[str, str]]:
    return {
        source.part_id: (source.part_name, source.content_hash)
        for source in sources
    }


def build_index_job(
    repository: SupabaseProjectRepository,
    project_id: str,
) -> dict:
    project_name = repository.project_name(project_id)
    sources = repository.list_cad_sources(project_id)
    index = build_project_index(project_id, project_name, sources)

    current_sources = repository.list_cad_sources(project_id)
    if _source_signature(sources) != _source_signature(current_sources):
        raise RuntimeError(
            "CAD sources changed while indexing; the previous index was preserved."
        )

    path = repository.write_index(project_id, index)
    return index_summary(index, path)


def test_getter_job(
    repository: SupabaseProjectRepository,
    project_id: str,
    request_text: str,
) -> dict:
    index = repository.read_index(project_id)
    if index is None:
        return {
            "schema_version": 1,
            "status": "missing_index",
            "query": request_text,
            "index_path": index_storage_path(project_id),
            "message": "Run /index <project_id> before testing the Getter.",
        }
    if index.get("schema_version") != 1:
        raise ValueError("Stored semantic index has an unsupported schema version.")
    if str(index.get("project_id")) != project_id:
        raise ValueError("Stored semantic index belongs to a different project.")
    sources = repository.list_cad_sources(project_id)
    return IndexGetter(index, sources).test_request(request_text)


def process_index_job(
    repository: SupabaseProjectRepository,
    job: dict,
) -> dict:
    project_id = str(job["project_id"])
    if job["type"] == "build_index":
        return build_index_job(repository, project_id)
    if job["type"] == "test_getter":
        request_text = str(job.get("request_text") or "").strip()
        if not request_text:
            raise ValueError("Getter test job is missing request_text.")
        return test_getter_job(repository, project_id, request_text)
    raise ValueError(f'Unsupported index job type "{job["type"]}".')
