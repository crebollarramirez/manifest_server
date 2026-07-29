import { Mesh, Texture, type Material, type Object3D } from "three";

/**
 * Explicit GPU resource release (performance budget: geometry memory bounded;
 * replaced resources are disposed, never leaked to GC).
 */

export function disposeMaterial(material: Material): void {
  for (const value of Object.values(material)) {
    if (value instanceof Texture) value.dispose();
  }
  material.dispose();
}

export function disposeObject3D(root: Object3D): void {
  root.traverse((child) => {
    if (!(child instanceof Mesh)) return;
    child.geometry.dispose();
    const materials = Array.isArray(child.material)
      ? child.material
      : [child.material];
    for (const material of materials) disposeMaterial(material);
  });
}
