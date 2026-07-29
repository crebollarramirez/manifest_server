import { describe, expect, it } from "vitest";
import { Color } from "three";
import { applyInteractionState, createPartMaterial } from "./useMaterial";
import { paletteColor } from "./palette";
import type { DecodedGeometry } from "../decode/types";
import { FIXTURE_CAD_PART_ID } from "../../api/fixtureIds";

function stlDecoded(): DecodedGeometry {
  return {
    positions: new Float32Array(9),
    normals: new Float32Array(9),
    indices: null,
    bounds: { min: [0, 0, 0], max: [1, 1, 1] },
    triangleCount: 1,
    authoredMaterial: null,
  };
}

function glbDecoded(): DecodedGeometry {
  return {
    ...stlDecoded(),
    indices: new Uint32Array([0, 1, 2]),
    authoredMaterial: {
      baseColorFactor: [0.72, 0.2, 0.16, 1],
      metallicFactor: 0.4,
      roughnessFactor: 0.5,
    },
  };
}

describe("createPartMaterial", () => {
  it("gives STL parts the stable palette color with a flat-shaded neutral base", () => {
    const material = createPartMaterial(FIXTURE_CAD_PART_ID, stlDecoded());
    const expected = new Color(paletteColor(FIXTURE_CAD_PART_ID));
    expect(material.color.equals(expected)).toBe(true);
    expect(material.flatShading).toBe(true);
    expect(material.metalness).toBeCloseTo(0.15);
    expect(material.roughness).toBeCloseTo(0.7);
  });

  it("passes authored GLB material factors through untouched (never overridden)", () => {
    const material = createPartMaterial(FIXTURE_CAD_PART_ID, glbDecoded());
    expect(material.color.r).toBeCloseTo(0.72);
    expect(material.color.g).toBeCloseTo(0.2);
    expect(material.color.b).toBeCloseTo(0.16);
    expect(material.metalness).toBeCloseTo(0.4);
    expect(material.roughness).toBeCloseTo(0.5);
    expect(material.flatShading).toBe(false);
  });
});

describe("applyInteractionState", () => {
  it("mutates the same material instance — never recreates", () => {
    const material = createPartMaterial(FIXTURE_CAD_PART_ID, stlDecoded());
    const before = material;
    applyInteractionState(material, { hovered: true, selected: false });
    expect(material).toBe(before);
  });

  it("lifts emissive for hover, more for selection; selection outranks hover", () => {
    const material = createPartMaterial(FIXTURE_CAD_PART_ID, stlDecoded());
    expect(material.emissiveIntensity).toBe(0);

    expect(applyInteractionState(material, { hovered: true, selected: false })).toBe(true);
    const hoverIntensity = material.emissiveIntensity;
    expect(hoverIntensity).toBeGreaterThan(0);

    expect(applyInteractionState(material, { hovered: true, selected: true })).toBe(true);
    expect(material.emissiveIntensity).toBeGreaterThan(hoverIntensity);

    expect(applyInteractionState(material, { hovered: false, selected: false })).toBe(true);
    expect(material.emissiveIntensity).toBe(0);
  });

  it("reports no change when the state is already applied (no wasted frames)", () => {
    const material = createPartMaterial(FIXTURE_CAD_PART_ID, stlDecoded());
    expect(applyInteractionState(material, { hovered: false, selected: false })).toBe(false);
    applyInteractionState(material, { hovered: true, selected: false });
    expect(applyInteractionState(material, { hovered: true, selected: false })).toBe(false);
  });

  it("glows in the material's own color (authored GLB hue preserved)", () => {
    const material = createPartMaterial(FIXTURE_CAD_PART_ID, glbDecoded());
    applyInteractionState(material, { hovered: false, selected: true });
    expect(material.emissive.r).toBeCloseTo(material.color.r);
    expect(material.emissive.g).toBeCloseTo(material.color.g);
    expect(material.emissive.b).toBeCloseTo(material.color.b);
  });
});
