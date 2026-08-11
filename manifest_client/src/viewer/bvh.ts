import { BufferGeometry, Mesh } from "three";
import { acceleratedRaycast, computeBoundsTree, disposeBoundsTree } from "three-mesh-bvh";

/**
 * Accelerated raycasting for large meshes (three-mesh-bvh).
 *
 * Without this, three.js's default raycasting brute-forces every triangle
 * against the pointer ray on every pointer move — fine for small meshes,
 * but for the 503k-triangle fixture that's up to half a million ray-
 * triangle tests per pointermove event. Confirmed via profiling: a real
 * session showed "Event: pointerover" consuming 74.9% of total time, almost
 * entirely inside raycasting, not geometry construction or rendering. A BVH
 * turns that into an O(log n) tree traversal instead.
 *
 * Side-effect import: patches BufferGeometry/Mesh prototypes once, globally,
 * before any raycasting happens. Safe for geometries that never call
 * computeBoundsTree() — acceleratedRaycast falls back to the standard
 * algorithm when no bounds tree is present.
 */
BufferGeometry.prototype.computeBoundsTree = computeBoundsTree;
BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree;
Mesh.prototype.raycast = acceleratedRaycast;

/**
 * Below this triangle count, building a BVH costs more than the brute-force
 * raycast it would ever save — not worth it for small, cheap-to-hover parts.
 */
export const BVH_TRIANGLE_THRESHOLD = 5000;

export function shouldUseBvh(triangleCount: number): boolean {
  return triangleCount >= BVH_TRIANGLE_THRESHOLD;
}

/** Builds a bounds tree for large geometries only; no-ops for small ones. */
export function attachBoundsTree(geometry: BufferGeometry, triangleCount: number): void {
  if (shouldUseBvh(triangleCount)) {
    geometry.computeBoundsTree();
  }
}

/** Safe to call even if attachBoundsTree never ran (no bounds tree present). */
export function detachBoundsTree(geometry: BufferGeometry): void {
  if (geometry.boundsTree) {
    geometry.disposeBoundsTree();
  }
}
