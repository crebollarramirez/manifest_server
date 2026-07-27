from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path

import cadquery as cq


WORKER_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute a generated CadQuery model.")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
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


def geometry_result(model: object) -> dict:
    if isinstance(model, cq.Workplane):
        solids = model.solids().vals()
        result_type = "cadquery.Workplane"
    elif isinstance(model, cq.Shape):
        solids = model.Solids()
        result_type = f"cadquery.{type(model).__name__}"
    else:
        return {
            "status": "failed",
            "stage": "geometry",
            "repairable_hint": True,
            "diagnostics": [
                {
                    "error_code": "BUILD_MODEL_RETURN_ERROR",
                    "message": (
                        "build_model must return a CadQuery Workplane or Shape; "
                        f"received {type(model).__name__}."
                    ),
                    "stage": "geometry",
                    "file_path": "model.py",
                    "function_name": "build_model",
                    "related_symbols": ["build_model"],
                }
            ],
            "build_artifacts": None,
        }

    if not solids:
        return {
            "status": "failed",
            "stage": "geometry",
            "repairable_hint": True,
            "diagnostics": [
                {
                    "error_code": "GEOMETRY_BUILD_ERROR",
                    "message": "build_model returned geometry with no solids.",
                    "stage": "geometry",
                    "file_path": "model.py",
                    "function_name": "build_model",
                    "related_symbols": ["build_model"],
                }
            ],
            "build_artifacts": None,
        }
    return {
        "status": "passed",
        "stage": "completed",
        "repairable_hint": False,
        "diagnostics": [],
        "build_artifacts": {
            "solid_count": len(solids),
            "result_type": result_type,
        },
    }


def execute(args: argparse.Namespace) -> dict:
    with args.params.open("r", encoding="utf-8") as params_file:
        params_data = json.load(params_file)
    if not isinstance(params_data, dict):
        raise TypeError("params.json must contain a JSON object.")

    module = import_model_module(args.model)
    params = module.ModelParams(**params_data)
    return geometry_result(module.build_model(params))


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
