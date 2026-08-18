"""Bucket name and object access for the geometry layer.

``workers/commons/storage.py`` owns this constant for the pip-packaged workers,
but ``cad_validator`` is a flat conda image that cannot import it -- see the
``COPY`` list in this worker's Dockerfile. Declaring it once here is what stops
the literal from being re-typed at each call site, which is how
``geometry_check_job`` came to hardcode it.
"""

from __future__ import annotations

BUCKET = "3dProjects"


class ObjectStore:
    """Thin, testable wrapper over the one Supabase bucket this system uses."""

    def __init__(self, supabase, bucket: str = BUCKET) -> None:
        self._supabase = supabase
        self._bucket = bucket

    def download(self, storage_path: str) -> bytes:
        return bytes(self._supabase.storage.from_(self._bucket).download(storage_path))

    def upload(self, storage_path: str, payload: bytes, content_type: str) -> None:
        self._supabase.storage.from_(self._bucket).upload(
            path=storage_path,
            file=payload,
            file_options={"content-type": content_type, "upsert": "true"},
        )


def cad_part_storage_path(project_id: str, part_id: str, filename: str) -> str:
    return f"{project_id}/parts/cad/{part_id}/{filename}"
