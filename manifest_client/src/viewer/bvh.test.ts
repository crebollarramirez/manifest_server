import { describe, expect, it } from "vitest";
import { BufferAttribute, BufferGeometry } from "three";
import { attachBoundsTree, BVH_TRIANGLE_THRESHOLD, detachBoundsTree, shouldUseBvh } from "./bvh";

function triangleGeometry(triangleCount: number): BufferGeometry {
  const geometry = new BufferGeometry();
  const positions = new Float32Array(triangleCount * 9);
  for (let t = 0; t < triangleCount; t += 1) {
    const base = t * 9;
    positions[base] = t;
    positions[base + 3] = t + 1;
    positions[base + 7] = 1;
  }
  geometry.setAttribute("position", new BufferAttribute(positions, 3));
  return geometry;
}

describe("shouldUseBvh", () => {
  it("is false below the threshold", () => {
    expect(shouldUseBvh(BVH_TRIANGLE_THRESHOLD - 1)).toBe(false);
  });

  it("is true at and above the threshold", () => {
    expect(shouldUseBvh(BVH_TRIANGLE_THRESHOLD)).toBe(true);
    expect(shouldUseBvh(503_004)).toBe(true);
  });
});

describe("attachBoundsTree / detachBoundsTree", () => {
  it("builds a bounds tree for a large mesh", () => {
    const geometry = triangleGeometry(BVH_TRIANGLE_THRESHOLD);
    expect(geometry.boundsTree).toBeUndefined();
    attachBoundsTree(geometry, BVH_TRIANGLE_THRESHOLD);
    expect(geometry.boundsTree).toBeDefined();
  });

  it("skips building a bounds tree for a small mesh (not worth the setup cost)", () => {
    const geometry = triangleGeometry(10);
    attachBoundsTree(geometry, 10);
    expect(geometry.boundsTree).toBeUndefined();
  });

  it("detachBoundsTree clears a tree that was built", () => {
    const geometry = triangleGeometry(BVH_TRIANGLE_THRESHOLD);
    attachBoundsTree(geometry, BVH_TRIANGLE_THRESHOLD);
    expect(geometry.boundsTree).toBeDefined();
    detachBoundsTree(geometry);
    // disposeBoundsTree() sets this to null, not undefined.
    expect(geometry.boundsTree).toBeFalsy();
  });

  it("detachBoundsTree is a safe no-op when no tree was ever built", () => {
    const geometry = triangleGeometry(10);
    expect(() => detachBoundsTree(geometry)).not.toThrow();
  });
});
