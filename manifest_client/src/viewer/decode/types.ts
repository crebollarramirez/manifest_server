/** Decode pipeline contract: everything heavy happens in the worker; the main
 * thread receives transferable buffers and does one cheap BufferGeometry
 * construction + GPU upload. */

export type GeometryKind = "stl" | "glb";

export type Bounds = {
  min: [number, number, number];
  max: [number, number, number];
};

/** Material authored inside a GLB. Never overridden by the client (Phase 2 rule). */
export type AuthoredMaterial = {
  baseColorFactor: [number, number, number, number];
  metallicFactor: number;
  roughnessFactor: number;
};

export type DecodedGeometry = {
  positions: Float32Array;
  normals: Float32Array;
  /** Null for STL (inherently non-indexed, flat-shaded). */
  indices: Uint32Array | null;
  bounds: Bounds;
  triangleCount: number;
  authoredMaterial: AuthoredMaterial | null;
};

export type DecodeRequest = {
  id: number;
  kind: GeometryKind;
  buffer: ArrayBuffer;
};

export type DecodeResponse =
  | { id: number; ok: true; geometry: DecodedGeometry }
  | { id: number; ok: false; error: string };

export function computeBounds(positions: Float32Array): Bounds {
  if (positions.length === 0) {
    return { min: [0, 0, 0], max: [0, 0, 0] };
  }
  const min: [number, number, number] = [Infinity, Infinity, Infinity];
  const max: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < positions.length; i += 3) {
    for (let axis = 0; axis < 3; axis += 1) {
      const value = positions[i + axis]!;
      if (value < min[axis]!) min[axis] = value;
      if (value > max[axis]!) max[axis] = value;
    }
  }
  return { min, max };
}
