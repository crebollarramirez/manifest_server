from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path


WORKER_DIR = Path(__file__).resolve().parent

# Same sibling-import dance as geometry_check_runner: this runs in an
# isolated interpreter, which does not prepend the script's own directory to
# sys.path.
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

try:
    from .geometry import GeometryExtractionError, build_geometry
except ImportError:
    from geometry import GeometryExtractionError, build_geometry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute a generated CadQuery model.")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    # Where to write the native B-rep. Full validation produces the artifact
    # too, so the candidate lifecycle -- not agent reasoning -- is what makes a
    # candidate's geometry exist. The parent uploads whatever lands here.
    parser.add_argument("--brep", required=False, type=Path, default=None)
    return parser.parse_args()


def import_model_module(model_path: Path):
    spec = importlib.util.spec_from_file_location("validated_cad_model", model_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load generated model from {model_path}.")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(WORKER_DIR))
    sys.path.insert(0, str(model_path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
        sys.path.pop(0)
    return module


def write_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, sort_keys=True),
        encoding="utf-8",
    )


def exception_result(exc: Exception, model_path: Path) -> dict:
    frames = traceback.extract_tb(exc.__traceback__)
    model_frames = [
        frame
        for frame in frames
        if Path(frame.filename).resolve() == model_path.resolve()
    ]
    frame = model_frames[-1] if model_frames else None
    diagnostic = {
        "error_code": "IMPORT_ERROR"
        if isinstance(exc, (ImportError, ModuleNotFoundError))
        else "CADQUERY_RUNTIME_ERROR",
        "message": str(exc) or type(exc).__name__,
        "stage": "cadquery_runtime",
        "file_path": "model.py",
        "related_symbols": [frame.name] if frame is not None else [],
        "exception_type": type(exc).__name__,
        "traceback_summary": " -> ".join(
            f"{Path(item.filename).name}:{item.lineno} in {item.name}"
            for item in frames[-5:]
        ),
    }
    if frame is not None:
        diagnostic["line"] = frame.lineno
        diagnostic["function_name"] = frame.name
    return {
        "status": "failed",
        "stage": "cadquery_runtime",
        "repairable_hint": not isinstance(exc, (ImportError, ModuleNotFoundError)),
        "diagnostics": [diagnostic],
        "build_artifacts": None,
    }


def geometry_result(model: object, brep_path: Path | None = None) -> dict:
    """Normalize, serialize, and measure one build result for full validation.

    Geometry travels the same extractor -> analyzer path a geometry check uses,
    against the same normalized root that gets serialized. Before this, full
    validation measured the build independently, so the two answers to "what
    did this candidate build" came from two implementations and neither was
    grounded in a shape anything had kept.
    """

    try:
        built = build_geometry(model, brep_path)
    except GeometryExtractionError as exc:
        return {
            "status": "failed",
            "stage": "geometry",
            "repairable_hint": True,
            "diagnostics": [
                {
                    "error_code": exc.error_code,
                    "message": exc.message,
                    "stage": "geometry",
                    "file_path": "model.py",
                    "function_name": "build_model",
                    "related_symbols": ["build_model"],
                }
            ],
            "build_artifacts": None,
        }

    snapshot = built.snapshot
    return {
        "status": "passed",
        "stage": "completed",
        "repairable_hint": False,
        "diagnostics": list(snapshot.get("diagnostics") or []),
        # The model is already built and measured, so reporting the full
        # measurement -- rather than only the solid count -- is what lets the
        # agent loop learn what a step produced from the validation it already
        # runs, instead of spending a whole model round calling check_geometry.
        #
        # These key names are consumed by orchestrator._geometry_summary and are
        # deliberately unchanged: the internal representation moved, the roster
        # the agent reads did not.
        "build_artifacts": {
            "solid_count": snapshot.get("solid_count"),
            "result_type": built.result_type,
            "volume_mm3": snapshot.get("volume_mm3"),
            "bounding_box": snapshot.get("bounding_box"),
            "center_of_mass": snapshot.get("center_of_mass"),
            "face_count": snapshot.get("face_count"),
            "edge_count": snapshot.get("edge_count"),
            # How the shape is oriented, not just how far it reaches. This is
            # the half a bounding box cannot express, and it travels this path
            # so a step opens already knowing it.
            "planar_faces": snapshot.get("planar_faces"),
            "non_planar_face_count": snapshot.get("non_planar_face_count"),
            "sharp_edge_count": snapshot.get("sharp_edge_count"),
        },
        # The snapshot and artifact descriptor the parent persists. Kept beside
        # build_artifacts rather than merged into it so the agent-facing roster
        # shape stays exactly what it was.
        "geometry": snapshot,
        "geometry_artifact": built.artifact,
    }


def execute(args: argparse.Namespace) -> dict:
    with args.params.open("r", encoding="utf-8") as params_file:
        params_data = json.load(params_file)
    if not isinstance(params_data, dict):
        raise TypeError("params.json must contain a JSON object.")

    module = import_model_module(args.model)
    params = module.ModelParams(**params_data)
    return geometry_result(module.build_model(params), args.brep)


def main() -> int:
    args = parse_args()
    try:
        result = execute(args)
    except Exception as exc:
        result = exception_result(exc, args.model)
        traceback.print_exc()
    write_result(args.result, result)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
