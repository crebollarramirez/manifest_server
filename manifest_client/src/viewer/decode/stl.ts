import { computeBounds, type DecodedGeometry } from "./types";

/**
 * STL decoder (binary + ASCII). Runs in the geometry worker.
 *
 * - Format detection never trusts the header alone: some binary STLs begin
 *   with "solid". Binary wins when the byte length matches 84 + 50n
 *   (Amendment A6: never trust Content-Type either).
 * - Output is non-indexed with computed flat normals — stored facet normals
 *   are frequently zero or garbage in the wild, so they are ignored.
 */

const BINARY_HEADER_BYTES = 84;
const BINARY_TRIANGLE_BYTES = 50;

export function decodeStl(buffer: ArrayBuffer): DecodedGeometry {
  if (isBinaryStl(buffer)) return decodeBinaryStl(buffer);
  const text = new TextDecoder().decode(buffer);
  if (text.trimStart().startsWith("solid")) return decodeAsciiStl(text);
  throw new Error("Not a recognizable binary or ASCII STL file.");
}

function isBinaryStl(buffer: ArrayBuffer): boolean {
  if (buffer.byteLength < BINARY_HEADER_BYTES) return false;
  const count = new DataView(buffer).getUint32(80, true);
  return (
    buffer.byteLength === BINARY_HEADER_BYTES + count * BINARY_TRIANGLE_BYTES
  );
}

function decodeBinaryStl(buffer: ArrayBuffer): DecodedGeometry {
  const view = new DataView(buffer);
  const triangleCount = view.getUint32(80, true);
  const positions = new Float32Array(triangleCount * 9);
  for (let t = 0; t < triangleCount; t += 1) {
    // Skip the 12-byte stored normal; vertices start at +12.
    const base = BINARY_HEADER_BYTES + t * BINARY_TRIANGLE_BYTES + 12;
    for (let component = 0; component < 9; component += 1) {
      positions[t * 9 + component] = view.getFloat32(base + component * 4, true);
    }
  }
  return fromFlatPositions(positions, triangleCount);
}

function decodeAsciiStl(text: string): DecodedGeometry {
  const values: number[] = [];
  const pattern = /vertex\s+([-+\d.eE]+)\s+([-+\d.eE]+)\s+([-+\d.eE]+)/g;
  for (const match of text.matchAll(pattern)) {
    const x = Number(match[1]);
    const y = Number(match[2]);
    const z = Number(match[3]);
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) {
      throw new Error("ASCII STL contains a non-finite vertex.");
    }
    values.push(x, y, z);
  }
  if (values.length === 0 || values.length % 9 !== 0) {
    throw new Error("ASCII STL does not contain whole triangles.");
  }
  const positions = new Float32Array(values);
  return fromFlatPositions(positions, positions.length / 9);
}

/** Compute flat (per-face) normals and bounds for non-indexed triangles. */
function fromFlatPositions(
  positions: Float32Array,
  triangleCount: number,
): DecodedGeometry {
  const normals = new Float32Array(positions.length);
  for (let t = 0; t < triangleCount; t += 1) {
    const i = t * 9;
    const ax = positions[i]!, ay = positions[i + 1]!, az = positions[i + 2]!;
    const bx = positions[i + 3]!, by = positions[i + 4]!, bz = positions[i + 5]!;
    const cx = positions[i + 6]!, cy = positions[i + 7]!, cz = positions[i + 8]!;
    const ux = bx - ax, uy = by - ay, uz = bz - az;
    const vx = cx - ax, vy = cy - ay, vz = cz - az;
    let nx = uy * vz - uz * vy;
    let ny = uz * vx - ux * vz;
    let nz = ux * vy - uy * vx;
    const length = Math.sqrt(nx * nx + ny * ny + nz * nz);
    if (length > 0) {
      nx /= length;
      ny /= length;
      nz /= length;
    } else {
      nz = 1;
    }
    for (let vertex = 0; vertex < 3; vertex += 1) {
      normals[i + vertex * 3] = nx;
      normals[i + vertex * 3 + 1] = ny;
      normals[i + vertex * 3 + 2] = nz;
    }
  }
  return {
    positions,
    normals,
    indices: null,
    bounds: computeBounds(positions),
    triangleCount,
    authoredMaterial: null,
  };
}
