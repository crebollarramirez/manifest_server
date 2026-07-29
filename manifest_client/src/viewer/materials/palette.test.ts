import { describe, expect, it } from "vitest";
import { PART_PALETTE, paletteColor, paletteIndex } from "./palette";
import {
  FIXTURE_BLANK_PART_ID,
  FIXTURE_CAD_PART_ID,
  FIXTURE_LARGE_PART_ID,
  FIXTURE_MESH_PART_ID,
} from "../../api/fixtureIds";

describe("palette", () => {
  it("is deterministic: the same part id always maps to the same color", () => {
    expect(paletteColor(FIXTURE_CAD_PART_ID)).toBe(paletteColor(FIXTURE_CAD_PART_ID));
    expect(paletteIndex(FIXTURE_CAD_PART_ID)).toBe(paletteIndex(FIXTURE_CAD_PART_ID));
  });

  it("always lands inside the palette", () => {
    for (const id of [
      FIXTURE_CAD_PART_ID,
      FIXTURE_MESH_PART_ID,
      FIXTURE_LARGE_PART_ID,
      FIXTURE_BLANK_PART_ID,
      crypto.randomUUID(),
      crypto.randomUUID(),
    ]) {
      const index = paletteIndex(id);
      expect(index).toBeGreaterThanOrEqual(0);
      expect(index).toBeLessThan(PART_PALETTE.length);
      expect(PART_PALETTE).toContain(paletteColor(id));
    }
  });

  it("spreads distinct ids across the palette (not everything one color)", () => {
    const indices = new Set(
      Array.from({ length: 64 }, () => paletteIndex(crypto.randomUUID())),
    );
    expect(indices.size).toBeGreaterThan(PART_PALETTE.length / 2);
  });

  it("palette entries are valid hex colors", () => {
    for (const hex of PART_PALETTE) expect(hex).toMatch(/^#[0-9a-f]{6}$/);
  });
});
