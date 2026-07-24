from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import cadquery as cq


def cad_part(**metadata: Any):
    """Attach semantic metadata without changing generated function behavior."""

    def decorator(func: Callable):
        func.__cad_part__ = metadata
        return func

    return decorator


__all__ = ["cq", "dataclass", "cad_part"]
