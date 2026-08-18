from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parent

# Invoked as ``python -I geometry_check_runner.py`` (isolated mode), which,
# unlike a normal invocation, does not prepend the script's own directory to
# sys.path -- so the sibling geometry package import needs it added explicitly
# before either import form below can succeed.
if str(WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(WORKER_DIR))

try:
    from .geometry import GeometryExtractionError, build_geometry, empty_snapshot
except ImportError:
    from geometry import GeometryExtractionError, build_geometry, empty_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute a generated CadQuery model and measure its geometry."
    )
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    # Where to write the native B-rep. The parent chooses the path and picks
    # the file up afterwards; nothing but JSON crosses back out of this
    # process, so the artifact travels on the shared filesystem the way
    # model.py and params.json already do.
    parser.add_argument("--brep", required=False, type=Path, default=None)
    return parser.parse_args()


def import_model_module(model_path: Path):
    spec = importlib.util.spec_from_file_location("checked_cad_model", model_path)
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
    path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")


def exception_result(exc: Exception, model_path: Path) -> dict:
    """Build a geometry-facts dict for a runtime exception, located when possible.

    Mirrors ``cad_validation_runner.exception_result``'s traceback walk: the
    last frame belonging to ``model_path`` (as opposed to CadQuery/OpenCascade
    internals, or this runner itself) is the candidate line actually
    responsible, so the message names the function and line rather than just
    the bare exception text -- without that, an error raised deep inside one
    feature's dependency chain is easy to misattribute to the wrong feature.
    """

    frames = traceback.extract_tb(exc.__traceback__)
    model_frames = [
        frame
        for frame in frames
        if Path(frame.filename).resolve() == model_path.resolve()
    ]
    frame = model_frames[-1] if model_frames else None
    message = str(exc) or type(exc).__name__
    if frame is not None:
        message = f"{message} (in {frame.name}, model.py:{frame.lineno})"
    return empty_snapshot(
        execution_ok=False,
        geometry_valid=None,
        error_message=message,
    )


def execute(args: argparse.Namespace) -> dict:
    with args.params.open("r", encoding="utf-8") as params_file:
        params_data = json.load(params_file)
    if not isinstance(params_data, dict):
        raise TypeError("params.json must contain a JSON object.")

    module = import_model_module(args.model)
    params = module.ModelParams(**params_data)
    try:
        built = build_geometry(module.build_model(params), args.brep)
    except GeometryExtractionError as exc:
        # A build that returns unusable geometry is a normal, expected outcome
        # for an in-progress edit -- reported, never raised.
        return empty_snapshot(
            execution_ok=True,
            geometry_valid=False,
            error_message=exc.message,
            solid_count=exc.solid_count,
        )
    result = dict(built.snapshot)
    result["geometry_artifact"] = built.artifact
    return result


def main() -> int:
    args = parse_args()
    try:
        result = execute(args)
    except Exception as exc:
        result = exception_result(exc, args.model)
        traceback.print_exc()
    write_result(args.result, result)
    return 0 if result["execution_ok"] and result.get("geometry_valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
