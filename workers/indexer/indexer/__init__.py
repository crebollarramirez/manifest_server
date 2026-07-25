"""Static project-wide indexing and retrieval for CadQuery source."""

from .getter import IndexGetter
from .index_builder import build_project_index
from .models import IndexingError, SourceFile

__all__ = [
    "IndexGetter",
    "IndexingError",
    "SourceFile",
    "build_project_index",
]
