from __future__ import annotations

import hashlib
from dataclasses import dataclass


class IndexingError(ValueError):
    """Raised when project source cannot produce a valid semantic index."""


@dataclass(frozen=True)
class SourceFile:
    part_id: str
    part_name: str
    storage_path: str
    content: str
    content_hash: str

    @classmethod
    def from_content(
        cls,
        *,
        part_id: str,
        part_name: str,
        storage_path: str,
        content: str,
    ) -> SourceFile:
        return cls(
            part_id=part_id,
            part_name=part_name,
            storage_path=storage_path,
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
