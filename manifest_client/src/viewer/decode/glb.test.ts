import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { decodeGlb } from "./glb";

const SPACESHIP_FIXTURE = fileURLToPath(
  new URL(
    "../../../public/fixtures/11111111-1111-4111-8111-111111111111/exports/33333333-3333-4333-8333-333333333333/model.glb",
    import.meta.url,
  ),
);

/** Minimal GLB builder mirroring scripts/generate-fixtures.py (single primitive). */
function buildGlb(options?: { translation?: [number, number, number] }): ArrayBuffer {
  const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
  const normals = new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]);
  const indices = new Uint16Array([0, 1, 2, 0]); // padded to 4-byte multiple

  const positionBytes = new Uint8Array(positions.buffer);
  const normalBytes = new Uint8Array(normals.buffer);
  const indexBytes = new Uint8Array(indices.buffer);
  const bin = new Uint8Array(
    positionBytes.length + normalBytes.length + indexBytes.length,
  );
  bin.set(positionBytes, 0);
  bin.set(normalBytes, positionBytes.length);
  bin.set(indexBytes, positionBytes.length + normalBytes.length);

  const json = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [
      {
        mesh: 0,
        ...(options?.translation ? { translation: options.translation } : {}),
      },
    ],
    meshes: [
      {
        primitives: [
          { attributes: { POSITION: 0, NORMAL: 1 }, indices: 2, material: 0 },
        ],
      },
    ],
    materials: [
      {
        pbrMetallicRoughness: {
          baseColorFactor: [0.72, 0.2, 0.16, 1],
          metallicFactor: 0.4,
          roughnessFactor: 0.5,
        },
      },
    ],
    buffers: [{ byteLength: bin.length }],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: positionBytes.length, target: 34962 },
      {
        buffer: 0,
        byteOffset: positionBytes.length,
        byteLength: normalBytes.length,
        target: 34962,
      },
      {
        buffer: 0,
        byteOffset: positionBytes.length + normalBytes.length,
        byteLength: indexBytes.length,
        target: 34963,
      },
    ],
    accessors: [
      {
        bufferView: 0,
        componentType: 5126,
        count: 3,
        type: "VEC3",
        min: [0, 0, 0],
        max: [1, 1, 0],
      },
      { bufferView: 1, componentType: 5126, count: 3, type: "VEC3" },
      { bufferView: 2, componentType: 5123, count: 3, type: "SCALAR" },
    ],
  };

  let jsonBytes = new TextEncoder().encode(JSON.stringify(json));
  const jsonPadding = (4 - (jsonBytes.length % 4)) % 4;
  if (jsonPadding) {
    const padded = new Uint8Array(jsonBytes.length + jsonPadding);
    padded.set(jsonBytes);
    padded.fill(0x20, jsonBytes.length);
    jsonBytes = padded;
  }

  const total = 12 + 8 + jsonBytes.length + 8 + bin.length;
  const out = new ArrayBuffer(total);
  const view = new DataView(out);
  const bytes = new Uint8Array(out);
  view.setUint32(0, 0x46546c67, true); // 'glTF'
  view.setUint32(4, 2, true);
  view.setUint32(8, total, true);
  view.setUint32(12, jsonBytes.length, true);
  view.setUint32(16, 0x4e4f534a, true); // 'JSON'
  bytes.set(jsonBytes, 20);
  view.setUint32(20 + jsonBytes.length, bin.length, true);
  view.setUint32(24 + jsonBytes.length, 0x004e4942, true); // 'BIN\0'
  bytes.set(bin, 28 + jsonBytes.length);
  return out;
}

describe("decodeGlb", () => {
  it("parses geometry, indices, and the authored material (never overridden)", () => {
    const decoded = decodeGlb(buildGlb());
    expect(decoded.triangleCount).toBe(1);
    expect(Array.from(decoded.indices ?? [])).toEqual([0, 1, 2]);
    expect(decoded.authoredMaterial).not.toBeNull();
    expect(decoded.authoredMaterial?.baseColorFactor[0]).toBeCloseTo(0.72);
    expect(decoded.authoredMaterial?.roughnessFactor).toBeCloseTo(0.5);
    expect(decoded.bounds.max[0]).toBeCloseTo(1);
  });

  it("applies node transforms to positions (Blender exports whole scenes)", () => {
    const decoded = decodeGlb(buildGlb({ translation: [5, 0, 0] }));
    expect(decoded.positions[0]).toBeCloseTo(5);
    expect(decoded.bounds.min[0]).toBeCloseTo(5);
    expect(decoded.bounds.max[0]).toBeCloseTo(6);
    // Normals are direction-only: unaffected by translation.
    expect(decoded.normals[2]).toBeCloseTo(1);
  });

  it("rejects non-GLB input loudly", () => {
    expect(() => decodeGlb(new TextEncoder().encode("solid x").buffer)).toThrow(
      /magic/,
    );
  });

  it.skipIf(!existsSync(SPACESHIP_FIXTURE))(
    "parses the generated spaceship fixture with its authored hull material",
    () => {
      const bytes = readFileSync(SPACESHIP_FIXTURE);
      const decoded = decodeGlb(
        bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
      );
      expect(decoded.triangleCount).toBe(16);
      expect(decoded.authoredMaterial?.baseColorFactor[0]).toBeCloseTo(0.72);
      // Wing tips at ±30 on X.
      expect(decoded.bounds.min[0]).toBeCloseTo(-30);
      expect(decoded.bounds.max[0]).toBeCloseTo(30);
    },
  );
});
