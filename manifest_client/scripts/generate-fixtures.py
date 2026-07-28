#!/usr/bin/env python3
"""Generate exporter-shaped geometry fixtures for the frontend.

Layout mirrors the verified export path (CONTRACT.md section 2):
    public/fixtures/<project_id>/exports/<part_id>/model.*

Parts (KEEP IN SYNC with src/api/fixtureIds.ts):
    bracket       cad   model.stl + model.step   (real CadQuery export of
                                                  manifest_server/3dModel.py
                                                  when cadquery is available;
                                                  procedural fallback otherwise)
    spaceship     mesh  model.stl + model.glb    (GLB carries an authored
                                                  material, exercising the
                                                  "never override GLB
                                                  materials" path)
    dense-sphere  cad   model.stl                (>= 500k triangles; the
                                                  performance fixture)

No third-party dependencies are required for the fallback path: binary STL
and GLB are written directly.
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
CAD_PART_ID = "22222222-2222-4222-8222-222222222222"
MESH_PART_ID = "33333333-3333-4333-8333-333333333333"
LARGE_PART_ID = "44444444-4444-4444-8444-444444444444"

CLIENT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = CLIENT_ROOT.parent
FIXTURES_ROOT = CLIENT_ROOT / "public" / "fixtures" / PROJECT_ID / "exports"

Vec = tuple[float, float, float]
Tri = tuple[Vec, Vec, Vec]


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(a: Vec, b: Vec) -> Vec:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def normalize(v: Vec) -> Vec:
    length = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if length == 0:
        return (0.0, 0.0, 1.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def write_binary_stl(path: Path, triangles: list[Tri]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(b"manifest fixture".ljust(80, b"\0"))
        fh.write(struct.pack("<I", len(triangles)))
        for a, b, c in triangles:
            normal = normalize(cross(sub(b, a), sub(c, a)))
            fh.write(struct.pack("<3f", *normal))
            for vertex in (a, b, c):
                fh.write(struct.pack("<3f", *vertex))
            fh.write(struct.pack("<H", 0))


def box_triangles(cx: float, cy: float, cz: float, sx: float, sy: float, sz: float) -> list[Tri]:
    x0, x1 = cx - sx / 2, cx + sx / 2
    y0, y1 = cy - sy / 2, cy + sy / 2
    z0, z1 = cz - sz / 2, cz + sz / 2
    p = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    quads = [
        (0, 3, 2, 1),  # bottom (z0), outward -z
        (4, 5, 6, 7),  # top (z1), outward +z
        (0, 1, 5, 4),  # front (y0)
        (2, 3, 7, 6),  # back (y1)
        (0, 4, 7, 3),  # left (x0)
        (1, 2, 6, 5),  # right (x1)
    ]
    tris: list[Tri] = []
    for i0, i1, i2, i3 in quads:
        tris.append((p[i0], p[i1], p[i2]))
        tris.append((p[i0], p[i2], p[i3]))
    return tris


def bracket_triangles() -> list[Tri]:
    """L-bracket (mm scale) approximating the wall-mount fixture shape."""
    plate = box_triangles(0, 0, 30, 60, 8, 60)
    arm = box_triangles(0, 24, 6, 60, 40, 12)
    return plate + arm


def sphere_triangles(radius: float, lat_segments: int, lon_segments: int) -> list[Tri]:
    def point(lat: int, lon: int) -> Vec:
        theta = math.pi * lat / lat_segments
        phi = 2 * math.pi * lon / lon_segments
        return (
            radius * math.sin(theta) * math.cos(phi),
            radius * math.sin(theta) * math.sin(phi),
            radius * math.cos(theta),
        )

    tris: list[Tri] = []
    for lat in range(lat_segments):
        for lon in range(lon_segments):
            a = point(lat, lon)
            b = point(lat + 1, lon)
            c = point(lat + 1, lon + 1)
            d = point(lat, lon + 1)
            if lat != 0:
                tris.append((a, b, c))
            if lat != lat_segments - 1:
                tris.append((a, c, d))
    return tris


def spaceship_geometry() -> tuple[list[Vec], list[tuple[int, int, int]]]:
    """Low-poly 'spaceship': an elongated octahedron with wing vertices."""
    vertices: list[Vec] = [
        (0, 0, 40),    # nose
        (0, 0, -25),   # tail
        (10, 0, 0), (-10, 0, 0), (0, 6, 0), (0, -6, 0),  # hull ring
        (30, 0, -12), (-30, 0, -12),                     # wing tips
    ]
    faces = [
        (0, 2, 4), (0, 4, 3), (0, 3, 5), (0, 5, 2),
        (1, 4, 2), (1, 3, 4), (1, 5, 3), (1, 2, 5),
        (2, 6, 4), (2, 5, 6), (6, 5, 1), (6, 1, 4),
        (3, 4, 7), (3, 7, 5), (7, 4, 1), (7, 1, 5),
    ]
    return vertices, faces


def indexed_to_triangles(vertices: list[Vec], faces: list[tuple[int, int, int]]) -> list[Tri]:
    return [(vertices[i], vertices[j], vertices[k]) for i, j, k in faces]


def vertex_normals(vertices: list[Vec], faces: list[tuple[int, int, int]]) -> list[Vec]:
    sums = [(0.0, 0.0, 0.0) for _ in vertices]
    for i, j, k in faces:
        n = cross(sub(vertices[j], vertices[i]), sub(vertices[k], vertices[i]))
        for idx in (i, j, k):
            sums[idx] = (sums[idx][0] + n[0], sums[idx][1] + n[1], sums[idx][2] + n[2])
    return [normalize(v) for v in sums]


def write_glb(
    path: Path,
    vertices: list[Vec],
    faces: list[tuple[int, int, int]],
    base_color: tuple[float, float, float, float],
) -> None:
    normals = vertex_normals(vertices, faces)
    position_bytes = b"".join(struct.pack("<3f", *v) for v in vertices)
    normal_bytes = b"".join(struct.pack("<3f", *n) for n in normals)
    index_values = [index for face in faces for index in face]
    index_bytes = b"".join(struct.pack("<I", index) for index in index_values)

    views = []
    blob = b""
    for data, target in (
        (position_bytes, 34962),
        (normal_bytes, 34962),
        (index_bytes, 34963),
    ):
        views.append(
            {
                "buffer": 0,
                "byteOffset": len(blob),
                "byteLength": len(data),
                "target": target,
            }
        )
        blob += data
        blob += b"\0" * (-len(blob) % 4)

    mins = [min(v[axis] for v in vertices) for axis in range(3)]
    maxs = [max(v[axis] for v in vertices) for axis in range(3)]
    gltf = {
        "asset": {"version": "2.0", "generator": "manifest fixture generator"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "spaceship"}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "NORMAL": 1},
                        "indices": 2,
                        "material": 0,
                    }
                ]
            }
        ],
        "materials": [
            {
                "name": "authored-hull",
                "pbrMetallicRoughness": {
                    "baseColorFactor": list(base_color),
                    "metallicFactor": 0.4,
                    "roughnessFactor": 0.5,
                },
            }
        ],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": views,
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(vertices),
                "type": "VEC3",
                "min": mins,
                "max": maxs,
            },
            {
                "bufferView": 1,
                "componentType": 5126,
                "count": len(normals),
                "type": "VEC3",
            },
            {
                "bufferView": 2,
                "componentType": 5125,
                "count": len(index_values),
                "type": "SCALAR",
            },
        ],
    }

    json_bytes = json.dumps(gltf, separators=(",", ":")).encode()
    json_bytes += b" " * (-len(json_bytes) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(blob)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.write(struct.pack("<4sII", b"glTF", 2, total))
        fh.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))  # 'JSON'
        fh.write(json_bytes)
        fh.write(struct.pack("<II", len(blob), 0x004E4942))  # 'BIN\0'
        fh.write(blob)


def try_cadquery_bracket(out_dir: Path) -> bool:
    """Export the real 3dModel.py via CadQuery if the environment supports it."""
    try:
        import cadquery as cq  # noqa: F401
    except ImportError:
        return False
    runtime_dir = REPO_ROOT / "workers" / "cad_validator"
    model_path = REPO_ROOT / "3dModel.py"
    if not model_path.is_file():
        return False
    sys.path.insert(0, str(runtime_dir))
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("fixture_model", model_path)
        if spec is None or spec.loader is None:
            return False
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        model = module.build_model(module.ModelParams())
        out_dir.mkdir(parents=True, exist_ok=True)
        cq.exporters.export(model, str(out_dir / "model.step"))
        cq.exporters.export(model, str(out_dir / "model.stl"))
        return True
    except Exception as error:  # degrade loudly but non-fatally
        print(f"  cadquery path failed ({error!r}); using procedural fallback")
        return False
    finally:
        sys.path.remove(str(runtime_dir))


def main() -> None:
    bracket_dir = FIXTURES_ROOT / CAD_PART_ID
    mesh_dir = FIXTURES_ROOT / MESH_PART_ID
    large_dir = FIXTURES_ROOT / LARGE_PART_ID

    print("bracket (cad):")
    if try_cadquery_bracket(bracket_dir):
        print("  real CadQuery export of 3dModel.py -> model.step + model.stl")
    else:
        write_binary_stl(bracket_dir / "model.stl", bracket_triangles())
        placeholder = (
            "ISO-10303-21;\n"
            "/* PLACEHOLDER fixture: not a valid STEP body. The frontend never\n"
            "   parses STEP; this file only exists as a signed-URL target. */\n"
            "END-ISO-10303-21;\n"
        )
        (bracket_dir / "model.step").write_text(placeholder)
        print("  procedural L-bracket STL + placeholder STEP (cadquery unavailable)")

    print("spaceship (mesh):")
    vertices, faces = spaceship_geometry()
    write_binary_stl(mesh_dir / "model.stl", indexed_to_triangles(vertices, faces))
    write_glb(mesh_dir / "model.glb", vertices, faces, (0.72, 0.2, 0.16, 1.0))
    print("  model.stl + model.glb (authored baseColor material)")

    print("dense-sphere (cad, performance fixture):")
    triangles = sphere_triangles(40.0, 502, 502)
    write_binary_stl(large_dir / "model.stl", triangles)
    print(f"  model.stl with {len(triangles):,} triangles")

    for path in sorted(FIXTURES_ROOT.rglob("model.*")):
        size = path.stat().st_size
        print(f"{path.relative_to(CLIENT_ROOT)}  {size:,} bytes")


if __name__ == "__main__":
    main()
