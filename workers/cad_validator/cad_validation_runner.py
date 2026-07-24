from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path


WORKER_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute a generated CadQuery model.")
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--params", required=True, type=Path)
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


def main() -> int:
    args = parse_args()
    with args.params.open("r", encoding="utf-8") as params_file:
        params_data = json.load(params_file)
    if not isinstance(params_data, dict):
        raise TypeError("params.json must contain a JSON object.")

    module = import_model_module(args.model)
    params = module.ModelParams(**params_data)
    module.build_model(params)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
