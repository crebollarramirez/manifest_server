from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path

import bpy

from blender_runtime import prepare_scene


EXPORTABLE_OBJECT_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and export a Blender model.")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--stl", required=True, type=Path)
    parser.add_argument("--glb", required=True, type=Path)
    script_args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(script_args)


def import_model_module(model_path: Path):
    spec = importlib.util.spec_from_file_location("generated_blender_model", model_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load generated model from {model_path}.")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(model_path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def collect_export_objects(model_objects: object) -> list[bpy.types.Object]:
    if not isinstance(model_objects, list) or not model_objects:
        raise TypeError("build_model(params) must return a non-empty list of Blender objects.")

    selected: list[bpy.types.Object] = []
    seen: set[int] = set()

    def add_object(obj: bpy.types.Object) -> None:
        identity = obj.as_pointer()
        if identity in seen:
            return
        seen.add(identity)
        if obj.name not in bpy.data.objects:
            raise ValueError(f'Returned object "{obj.name}" is not registered in Blender.')
        if obj.type in EXPORTABLE_OBJECT_TYPES:
            selected.append(obj)
        for child in sorted(obj.children, key=lambda item: item.name):
            add_object(child)

    for value in model_objects:
        if not isinstance(value, bpy.types.Object):
            raise TypeError("build_model(params) returned a value that is not a Blender object.")
        add_object(value)

    if not selected:
        raise ValueError("build_model(params) returned no exportable geometry objects.")
    return selected


def select_objects(objects: list[bpy.types.Object]) -> None:
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    for obj in objects:
        obj.hide_set(False)
        obj.hide_viewport = False
        obj.hide_render = False
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def operator_is_registered(operator: object) -> bool:
    """Check registration because bpy.ops reports missing operators as attributes."""
    try:
        operator.get_rna_type()
    except (AttributeError, KeyError, RuntimeError):
        return False
    return True


def export_stl(output_path: Path) -> None:
    modern_exporter = bpy.ops.wm.stl_export
    if operator_is_registered(modern_exporter):
        result = modern_exporter(
            filepath=str(output_path),
            export_selected_objects=True,
            global_scale=1000.0,
            apply_modifiers=True,
        )
    else:
        legacy_exporter = bpy.ops.export_mesh.stl
        addon_error: Exception | None = None
        if not operator_is_registered(legacy_exporter):
            try:
                bpy.ops.preferences.addon_enable(module="io_mesh_stl")
            except (AttributeError, RuntimeError) as exc:
                addon_error = exc

        if not operator_is_registered(legacy_exporter):
            detail = f" Add-on error: {addon_error}" if addon_error else ""
            raise RuntimeError(
                "Blender has no registered STL exporter. Ensure the native Blender "
                f"io_mesh_stl add-on is installed.{detail}"
            )

        result = legacy_exporter(
            filepath=str(output_path),
            use_selection=True,
            global_scale=1000.0,
            use_scene_unit=False,
            use_mesh_modifiers=True,
        )
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender STL export did not finish: {result}")


def export_glb(output_path: Path) -> None:
    result = bpy.ops.export_scene.gltf(
        filepath=str(output_path),
        export_format="GLB",
        use_selection=True,
        export_apply=True,
    )
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender GLB export did not finish: {result}")


def main() -> int:
    args = parse_args()
    args.stl.parent.mkdir(parents=True, exist_ok=True)
    args.glb.parent.mkdir(parents=True, exist_ok=True)

    prepare_scene()
    module = import_model_module(args.model)
    if not hasattr(module, "ModelParams") or not callable(getattr(module, "build_model", None)):
        raise AttributeError("Generated model must define ModelParams and build_model(params).")

    with args.params.open("r", encoding="utf-8") as params_file:
        params_data = json.load(params_file)
    if not isinstance(params_data, dict):
        raise TypeError("params.json must contain a JSON object.")

    params = module.ModelParams(**params_data)
    export_objects = collect_export_objects(module.build_model(params))
    select_objects(export_objects)
    export_stl(args.stl)
    export_glb(args.glb)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
