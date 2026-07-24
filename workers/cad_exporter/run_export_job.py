from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import cadquery as cq


BUCKET = "3dProjects"
SUPPORTED_JOB_TYPES = ("export_cad", "export_mesh")
WORKER_DIR = Path(__file__).resolve().parent
BLENDER_RUNNER = WORKER_DIR / "blender_export_runner.py"
DEFAULT_BLENDER_TIMEOUT_SECONDS = 300


class SupersededJob(RuntimeError):
    """Raised when a job no longer matches the current source revision."""


def download_file(supabase, storage_path: str, local_path: Path):
    local_path.parent.mkdir(parents=True, exist_ok=True)
    data = supabase.storage.from_(BUCKET).download(storage_path)
    local_path.write_bytes(data)


def upload_file(supabase, local_path: Path, storage_path: str, content_type: str):
    with local_path.open("rb") as file_handle:
        supabase.storage.from_(BUCKET).upload(
            path=storage_path,
            file=file_handle,
            file_options={
                "content-type": content_type,
                "upsert": "true",
            },
        )


def import_model_module(model_path: Path):
    spec = importlib.util.spec_from_file_location("generated_model", model_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load generated model from {model_path}.")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(model_path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def verify_source_hash(model_path: Path, expected_hash: object) -> None:
    if expected_hash is None:
        return
    expected = str(expected_hash)
    actual = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if actual != expected:
        raise SupersededJob(
            "CAD export was cancelled because model.py changed after validation."
        )


def project_storage_path(project_id: str, *parts: str) -> str:
    """Build a Storage path within a single project's namespace."""
    return "/".join((project_id, *parts))


def part_source_storage_path(
    project_id: str,
    part_type: str,
    part_id: str,
    *parts: str,
) -> str:
    """Build a path within a CAD or mesh part's source directory."""
    if part_type not in {"cad", "mesh"}:
        raise ValueError(f"Unsupported part type: {part_type}")
    return project_storage_path(project_id, "parts", part_type, part_id, *parts)


def cad_part_storage_path(project_id: str, part_id: str, *parts: str) -> str:
    """Build a path within a project's CadQuery source directory."""
    return part_source_storage_path(project_id, "cad", part_id, *parts)


def mesh_part_storage_path(project_id: str, part_id: str, *parts: str) -> str:
    """Build a path within a project's Blender source directory."""
    return part_source_storage_path(project_id, "mesh", part_id, *parts)


def part_export_storage_path(project_id: str, part_id: str, *parts: str) -> str:
    """Build a path within a part's exported artifact directory."""
    return project_storage_path(project_id, "exports", part_id, *parts)


def run_cad_export_job(supabase, job: dict, workdir: Path) -> None:
    project_id = str(job["project_id"])
    part_id = str(job["part_id"])
    source_dir = workdir / "source"
    export_dir = workdir / "exports"
    model_path = source_dir / "model.py"
    params_path = source_dir / "params.json"

    download_file(
        supabase,
        cad_part_storage_path(project_id, part_id, "model.py"),
        model_path,
    )
    download_file(
        supabase,
        cad_part_storage_path(project_id, part_id, "params.json"),
        params_path,
    )
    verify_source_hash(model_path, job.get("source_sha256"))

    module = import_model_module(model_path)
    with params_path.open("r", encoding="utf-8") as params_file:
        params_data = json.load(params_file)
    if not isinstance(params_data, dict):
        raise TypeError("params.json must contain a JSON object.")

    params = module.ModelParams(**params_data)
    model = module.build_model(params)
    export_dir.mkdir(parents=True, exist_ok=True)
    step_path = export_dir / "model.step"
    stl_path = export_dir / "model.stl"
    cq.exporters.export(model, str(step_path))
    cq.exporters.export(model, str(stl_path))

    upload_file(
        supabase,
        step_path,
        part_export_storage_path(project_id, part_id, "model.step"),
        "model/step",
    )
    upload_file(
        supabase,
        stl_path,
        part_export_storage_path(project_id, part_id, "model.stl"),
        "model/stl",
    )


def blender_timeout_seconds() -> int:
    value = os.environ.get(
        "BLENDER_JOB_TIMEOUT_SECONDS",
        str(DEFAULT_BLENDER_TIMEOUT_SECONDS),
    )
    try:
        timeout = int(value)
    except ValueError as exc:
        raise ValueError("BLENDER_JOB_TIMEOUT_SECONDS must be an integer.") from exc
    if timeout <= 0:
        raise ValueError("BLENDER_JOB_TIMEOUT_SECONDS must be greater than zero.")
    return timeout


def sanitized_blender_environment(workdir: Path) -> dict[str, str]:
    environment = os.environ.copy()
    sensitive_names = {
        "OPENAI_API_KEY",
        "SUPABASE_ANON_KEY",
        "SUPABASE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_URL",
    }
    for name in tuple(environment):
        if name in sensitive_names or name.endswith(("_TOKEN", "_SECRET")):
            environment.pop(name, None)

    home_dir = workdir / "home"
    temp_dir = workdir / "tmp"
    home_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    environment["HOME"] = str(home_dir)
    environment["TMPDIR"] = str(temp_dir)
    environment["PYTHONPATH"] = str(WORKER_DIR)
    return environment


def blender_command(
    model_path: Path,
    params_path: Path,
    stl_path: Path,
    glb_path: Path,
) -> list[str]:
    blender_name = os.environ.get("BLENDER_BIN", "blender")
    blender_path = shutil.which(blender_name)
    if blender_path is None:
        raise RuntimeError(f"Blender executable was not found: {blender_name}")
    return [
        blender_path,
        "--background",
        "--factory-startup",
        "--python",
        str(BLENDER_RUNNER),
        "--",
        "--model",
        str(model_path),
        "--params",
        str(params_path),
        "--stl",
        str(stl_path),
        "--glb",
        str(glb_path),
    ]


def run_blender(command: list[str], workdir: Path) -> None:
    try:
        result = subprocess.run(
            command,
            cwd=workdir,
            env=sanitized_blender_environment(workdir),
            capture_output=True,
            text=True,
            timeout=blender_timeout_seconds(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(
            value for value in (exc.stdout, exc.stderr) if isinstance(value, str)
        )
        raise RuntimeError(
            f"Blender export timed out after {blender_timeout_seconds()} seconds.\n"
            f"{output[-8000:]}"
        ) from exc

    if result.returncode != 0:
        output = "\n".join(value for value in (result.stdout, result.stderr) if value)
        raise RuntimeError(
            f"Blender export failed with exit code {result.returncode}.\n{output[-8000:]}"
        )


def validate_mesh_artifacts(stl_path: Path, glb_path: Path) -> None:
    import numpy as np
    import trimesh

    for path in (stl_path, glb_path):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Blender did not produce a non-empty {path.name} artifact.")

    mesh = trimesh.load(stl_path, force="mesh", process=False)
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    if vertices.size == 0 or faces.size == 0:
        raise ValueError("Generated STL contains no vertices or faces.")
    if not np.isfinite(vertices).all() or not np.isfinite(mesh.bounds).all():
        raise ValueError("Generated STL contains non-finite geometry.")

    extents = np.asarray(mesh.extents)
    if np.count_nonzero(extents > 1e-12) < 2:
        raise ValueError("Generated STL has no meaningful geometric size.")

    face_areas = np.asarray(mesh.area_faces)
    if not np.isfinite(face_areas).all() or not np.any(face_areas > 1e-12):
        raise ValueError("Generated STL contains only degenerate faces.")


def run_mesh_export_job(supabase, job: dict, workdir: Path) -> None:
    project_id = str(job["project_id"])
    part_id = str(job["part_id"])
    source_dir = workdir / "source"
    export_dir = workdir / "exports"
    model_path = source_dir / "model.py"
    params_path = source_dir / "params.json"
    stl_path = export_dir / "model.stl"
    glb_path = export_dir / "model.glb"

    download_file(
        supabase,
        mesh_part_storage_path(project_id, part_id, "model.py"),
        model_path,
    )
    download_file(
        supabase,
        mesh_part_storage_path(project_id, part_id, "params.json"),
        params_path,
    )
    export_dir.mkdir(parents=True, exist_ok=True)
    run_blender(
        blender_command(model_path, params_path, stl_path, glb_path),
        workdir,
    )
    validate_mesh_artifacts(stl_path, glb_path)

    upload_file(
        supabase,
        stl_path,
        part_export_storage_path(project_id, part_id, "model.stl"),
        "model/stl",
    )
    upload_file(
        supabase,
        glb_path,
        part_export_storage_path(project_id, part_id, "model.glb"),
        "model/gltf-binary",
    )


def run_export_job(supabase, job: dict) -> None:
    job_id = str(job["id"])
    job_type = str(job.get("type", ""))
    workdir = Path(f"/tmp/jobs/{job_id}")
    shutil.rmtree(workdir, ignore_errors=True)

    try:
        if job_type == "export_cad":
            run_cad_export_job(supabase, job, workdir)
        elif job_type == "export_mesh":
            run_mesh_export_job(supabase, job, workdir)
        else:
            raise ValueError(f"Unsupported generation job type: {job_type or '<missing>'}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
