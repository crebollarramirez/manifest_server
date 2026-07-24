# runtime/cadquery_runtime.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

import cadquery as cq


def cad_part(**metadata: Any):
    """
    No-op decorator used for semantic indexing.

    The indexer can read this metadata with Python AST.
    The function itself remains normal CadQuery code.
    """
    def decorator(func: Callable):
        func.__cad_part__ = metadata
        return func

    return decorator


__all__ = [
    "cq",
    "dataclass",
    "cad_part",
]