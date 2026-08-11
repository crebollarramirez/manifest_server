import { describe, expect, it } from "vitest";
import { convertLength, formatDimensions, plateTransform, PLATE_FIT_UNITS } from "./normalize";

describe("plateTransform", () => {
  it("scales the longest axis to the plate fit and recenters", () => {
    // 60 x 48 x 66 mm bracket-ish bounds, offset from the origin.
    const transform = plateTransform({ min: [10, -24, 0], max: [70, 24, 66] });
    expect(transform.size).toEqual([60, 48, 66]);
    expect(transform.scale).toBeCloseTo(PLATE_FIT_UNITS / 66);
    expect(transform.center).toEqual([40, 0, 33]);
    // Z extent becomes vertical after the -90° X rotation; seat = half height.
    expect(transform.seatHeight).toBeCloseTo((66 * PLATE_FIT_UNITS) / 66 / 2);
  });

  it("keeps a 5mm screw and a 300mm bracket equally legible", () => {
    const screw = plateTransform({ min: [0, 0, 0], max: [5, 5, 5] });
    const bracket = plateTransform({ min: [0, 0, 0], max: [300, 300, 300] });
    expect(screw.scale * 5).toBeCloseTo(PLATE_FIT_UNITS);
    expect(bracket.scale * 300).toBeCloseTo(PLATE_FIT_UNITS);
  });

  it("degenerates safely on empty bounds", () => {
    const transform = plateTransform({ min: [0, 0, 0], max: [0, 0, 0] });
    expect(transform.scale).toBe(1);
    expect(transform.seatHeight).toBe(0);
  });
});

describe("formatDimensions", () => {
  it("shows one decimal under 100mm, none above", () => {
    expect(formatDimensions([60, 48.25, 66])).toBe("60.0 × 48.3 × 66.0 mm");
    expect(formatDimensions([300, 4.9, 120])).toBe("300 × 4.9 × 120 mm");
  });
});

describe("convertLength", () => {
  it("rounds to whole millimeters", () => {
    expect(convertLength(142.6, "mm")).toBe("143");
  });

  it("converts to centimeters with one decimal", () => {
    expect(convertLength(142, "cm")).toBe("14.2");
  });

  it("converts to inches with two decimals", () => {
    expect(convertLength(25.4, "in")).toBe("1.00");
  });

  it("handles zero", () => {
    expect(convertLength(0, "mm")).toBe("0");
    expect(convertLength(0, "cm")).toBe("0.0");
    expect(convertLength(0, "in")).toBe("0.00");
  });
});
