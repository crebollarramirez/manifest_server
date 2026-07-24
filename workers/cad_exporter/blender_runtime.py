from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector


MODEL_ROOT_COLLECTION = "generated_model"


def mesh_part(**metadata: Any):
    """Attach literal semantic metadata without changing function behavior."""

    def decorator(func: Callable):
        func.__mesh_part__ = metadata
        return func

    return decorator


def mm(value: float) -> float:
    """Convert millimeters to Blender scene units, where one unit is one meter."""
    return float(value) / 1000.0


def configure_scene_units() -> None:
    units = bpy.context.scene.unit_settings
    units.system = "METRIC"
    units.scale_length = 1.0
    units.length_unit = "MILLIMETERS"


def get_or_create_collection(name: str) -> bpy.types.Collection:
    """Return a stable generated-model collection, creating it when necessary."""
    root = bpy.data.collections.get(MODEL_ROOT_COLLECTION)
    if root is None:
        root = bpy.data.collections.new(MODEL_ROOT_COLLECTION)
        bpy.context.scene.collection.children.link(root)

    if name == MODEL_ROOT_COLLECTION:
        return root

    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if collection.name not in root.children:
        root.children.link(collection)
    return collection


def link_object(obj: bpy.types.Object, collection_name: str) -> bpy.types.Object:
    """Link an object only to its requested generated-model collection."""
    collection = get_or_create_collection(collection_name)
    if obj.name not in collection.objects:
        collection.objects.link(obj)
    for current in tuple(obj.users_collection):
        if current != collection:
            current.objects.unlink(obj)
    return obj


def prepare_scene() -> None:
    """Reset Blender's factory scene and prepare deterministic model collections."""
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in tuple(bpy.data.collections):
        bpy.data.collections.remove(collection)
    configure_scene_units()
    get_or_create_collection(MODEL_ROOT_COLLECTION)


__all__ = [
    "bpy",
    "bmesh",
    "dataclass",
    "Vector",
    "Matrix",
    "Euler",
    "mesh_part",
    "mm",
    "get_or_create_collection",
    "link_object",
]
