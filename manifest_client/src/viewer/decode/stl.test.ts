import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { decodeStl } from "./stl";

const LARGE_FIXTURE = fileURLToPath(
  new URL(
    "../../../public/fixtures/11111111-1111-4111-8111-111111111111/exports/44444444-4444-4444-8444-444444444444/model.stl",
    import.meta.url,
  ),
);

/** Build a binary STL buffer for the given triangles (mirrors the exporter format). */
function binaryStl(triangles: number[][][]): ArrayBuffer {
  const buffer = new ArrayBuffer(84 + triangles.length * 50);
  const view = new DataView(buffer);
  view.setUint32(80, triangles.length, true);
  triangles.forEach((triangle, t) => {
    const base = 84 + t * 50 + 12; // stored normal left as zeros
    triangle.forEach((vertex, v) => {
      vertex.forEach((component, c) => {
        view.setFloat32(base + (v * 3 + c) * 4, component, true);
      });
    });
  });
  return buffer;
}

describe("decodeStl (binary)", () => {
  it("parses triangles and computes flat normals, ignoring stored normals", () => {
    // CCW triangle in the XY plane -> +Z normal.
    const decoded = decodeStl(
      binaryStl([
        [
          [0, 0, 0],
          [1, 0, 0],
          [0, 1, 0],
        ],
      ]),
    );
    expect(decoded.triangleCount).toBe(1);
    expect(decoded.indices).toBeNull();
    expect(Array.from(decoded.positions.slice(0, 3))).toEqual([0, 0, 0]);
    // Flat normal repeated for all three vertices.
    for (let vertex = 0; vertex < 3; vertex += 1) {
      expect(decoded.normals[vertex * 3 + 2]).toBeCloseTo(1);
    }
    expect(decoded.bounds).toEqual({ min: [0, 0, 0], max: [1, 1, 0] });
  });

  it("rejects a truncated binary STL", () => {
    const buffer = binaryStl([
      [
        [0, 0, 0],
        [1, 0, 0],
        [0, 1, 0],
      ],
    ]).slice(0, 100);
    expect(() => decodeStl(buffer)).toThrow();
  });

  it.skipIf(!existsSync(LARGE_FIXTURE))(
    "parses the 500k+-triangle performance fixture",
    () => {
      const bytes = readFileSync(LARGE_FIXTURE);
      const buffer = bytes.buffer.slice(
        bytes.byteOffset,
        bytes.byteOffset + bytes.byteLength,
      );
      const decoded = decodeStl(buffer);
      expect(decoded.triangleCount).toBeGreaterThanOrEqual(500_000);
      expect(decoded.positions.length).toBe(decoded.triangleCount * 9);
      // Sphere of radius 40: bounds must be finite and plausible.
      for (let axis = 0; axis < 3; axis += 1) {
        expect(decoded.bounds.min[axis]).toBeCloseTo(-40, 0);
        expect(decoded.bounds.max[axis]).toBeCloseTo(40, 0);
      }
    },
  );
});

describe("decodeStl (ascii)", () => {
  it("parses ASCII STL text (CadQuery may emit either format)", () => {
    const text = [
      "solid fixture",
      "  facet normal 0 0 1",
      "    outer loop",
      "      vertex 0 0 0",
      "      vertex 2 0 0",
      "      vertex 0 2 0",
      "    endloop",
      "  endfacet",
      "endsolid fixture",
    ].join("\n");
    const decoded = decodeStl(new TextEncoder().encode(text).buffer);
    expect(decoded.triangleCount).toBe(1);
    expect(decoded.bounds.max).toEqual([2, 2, 0]);
    expect(decoded.normals[2]).toBeCloseTo(1);
  });

  it("rejects ASCII STL with partial triangles", () => {
    const text = "solid x\nvertex 0 0 0\nvertex 1 0 0\nendsolid x";
    expect(() => decodeStl(new TextEncoder().encode(text).buffer)).toThrow(
      /whole triangles/,
    );
  });

  it("rejects garbage input", () => {
    expect(() => decodeStl(new TextEncoder().encode("not geometry").buffer)).toThrow(
      /recognizable/,
    );
  });
});
