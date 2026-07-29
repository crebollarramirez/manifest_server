import { describe, expect, it, vi } from "vitest";
import {
  BufferGeometry,
  Group,
  Mesh,
  MeshStandardMaterial,
  Texture,
} from "three";
import { disposeMaterial, disposeObject3D } from "./dispose";

describe("disposeMaterial", () => {
  it("disposes the material and any attached textures", () => {
    const material = new MeshStandardMaterial();
    const texture = new Texture();
    material.map = texture;
    const materialSpy = vi.spyOn(material, "dispose");
    const textureSpy = vi.spyOn(texture, "dispose");
    disposeMaterial(material);
    expect(materialSpy).toHaveBeenCalledOnce();
    expect(textureSpy).toHaveBeenCalledOnce();
  });
});

describe("disposeObject3D", () => {
  it("disposes every mesh geometry and material in the subtree", () => {
    const root = new Group();
    const geometryA = new BufferGeometry();
    const geometryB = new BufferGeometry();
    const materialA = new MeshStandardMaterial();
    const materialB = new MeshStandardMaterial();
    const child = new Group();
    child.add(new Mesh(geometryB, materialB));
    root.add(new Mesh(geometryA, materialA), child);

    const spies = [geometryA, geometryB, materialA, materialB].map((target) =>
      vi.spyOn(target, "dispose"),
    );
    disposeObject3D(root);
    for (const spy of spies) expect(spy).toHaveBeenCalledOnce();
  });

  it("handles material arrays", () => {
    const materials = [new MeshStandardMaterial(), new MeshStandardMaterial()];
    const mesh = new Mesh(new BufferGeometry(), materials);
    const spies = materials.map((m) => vi.spyOn(m, "dispose"));
    disposeObject3D(mesh);
    for (const spy of spies) expect(spy).toHaveBeenCalledOnce();
  });
});
