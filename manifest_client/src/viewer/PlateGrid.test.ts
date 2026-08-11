import { describe, expect, it } from "vitest";
import { gridLayoutPosition } from "./PlateGrid";

/**
 * This layout math is load-bearing for more than rendering now: PreviewLayer
 * uses it to compute where to pan the camera when a part is focused, so it
 * must agree exactly with where PlateGrid actually places each plate.
 */
describe("gridLayoutPosition", () => {
  it("centers a single plate at the origin", () => {
    expect(gridLayoutPosition(0, 1)).toEqual([0, 0, 0]);
  });

  it("arranges a 2x2 layout symmetrically around the origin", () => {
    const positions = [0, 1, 2, 3].map((i) => gridLayoutPosition(i, 4));
    const xs = positions.map((p) => p[0]);
    const zs = positions.map((p) => p[2]);
    // Symmetric: for every position, its mirror image is also present.
    for (const x of xs) expect(xs).toContain(-x);
    for (const z of zs) expect(zs).toContain(-z);
    // Y is always 0 — the grid lies flat.
    expect(positions.every((p) => p[1] === 0)).toBe(true);
  });

  it("never places two plates at the same position", () => {
    const total = 7;
    const positions = Array.from({ length: total }, (_, i) => gridLayoutPosition(i, total));
    const unique = new Set(positions.map((p) => p.join(",")));
    expect(unique.size).toBe(total);
  });

  it("is deterministic — same index and total always produce the same position", () => {
    expect(gridLayoutPosition(3, 9)).toEqual(gridLayoutPosition(3, 9));
  });
});
