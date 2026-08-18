"""Persistence and cache lookup for derived geometry snapshots.

A snapshot is a compact, deterministic summary of one geometry artifact. This
module is the one place that knows the ``geometry_snapshots`` column vocabulary
and the one place that decides whether a measurement can be reused.
"""

from __future__ import annotations

from typing import Any

from .analyzer import SNAPSHOT_FIELDS
from .runtime import GEOMETRY_CHECKER_VERSION

TABLE = "geometry_snapshots"


def snapshot_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in SNAPSHOT_FIELDS}


class GeometrySnapshotStore:
    """Read and write the derived-observation layer."""

    def __init__(self, supabase) -> None:
        self._supabase = supabase

    def find(self, source_sha256: str) -> dict[str, Any] | None:
        """Return the cached snapshot row for one exact source hash.

        Keyed on (source_sha256, geometry_checker_version), so a snapshot
        measured under an older, smaller vocabulary is never served as if it
        answered the same questions -- and a candidate can only ever see a
        snapshot of source byte-identical to its own.
        """

        response = (
            self._supabase.table(TABLE)
            .select("*")
            .eq("source_sha256", source_sha256)
            .eq("geometry_checker_version", GEOMETRY_CHECKER_VERSION)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None

    def store(
        self,
        *,
        project_id: str,
        part_id: str,
        candidate_id: str | None,
        source_storage_path: str,
        source_sha256: str,
        snapshot: dict[str, Any],
        geometry_artifact_id: str | None = None,
    ) -> None:
        """Persist one derived snapshot, naming the artifact it observed.

        ``SNAPSHOT_FIELDS`` are written verbatim as column names. A name in that
        tuple with no column behind it is not a degraded write -- PostgREST
        rejects the whole insert with PGRST204, and that error escaping this
        handler is exactly how ``diagnostics`` once took the validator worker
        down. ``tests/test_geometry_check_migration.py`` pins the two together.
        """

        payload = {
            "project_id": project_id,
            "part_id": part_id,
            "edit_job_id": candidate_id,
            "source_storage_path": source_storage_path,
            "source_sha256": source_sha256,
            "geometry_checker_version": GEOMETRY_CHECKER_VERSION,
            "geometry_artifact_id": geometry_artifact_id,
            **{field: snapshot.get(field) for field in SNAPSHOT_FIELDS},
        }
        try:
            self._supabase.table(TABLE).insert(payload).execute()
        except Exception as exc:
            # A concurrent geometry check for the exact same (source_sha256,
            # geometry_checker_version) may have persisted this snapshot first --
            # that snapshot describes the same immutable content, so losing the
            # race is safe to ignore. Anything else is a real persistence failure.
            code = str(getattr(exc, "code", "") or "")
            if code != "23505" and "duplicate key" not in str(exc).lower():
                raise
