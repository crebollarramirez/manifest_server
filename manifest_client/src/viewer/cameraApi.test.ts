import { describe, expect, it } from "vitest";
import { applyDragDelta, clampPolar, ORBIT_PRESETS } from "./cameraApi";

describe("clampPolar", () => {
  it("passes through values already inside the safe range", () => {
    expect(clampPolar(90)).toBe(90);
  });

  it("clamps below the minimum (never fully flip past the pole)", () => {
    expect(clampPolar(-40)).toBe(2);
    expect(clampPolar(0)).toBe(2);
  });

  it("clamps above the maximum", () => {
    expect(clampPolar(200)).toBe(178);
  });
});

describe("ORBIT_PRESETS", () => {
  it("defines all four mockup-named presets", () => {
    expect(Object.keys(ORBIT_PRESETS).sort()).toEqual(
      ["front", "iso", "right", "top"].sort(),
    );
  });

  it("every preset's polar angle is within the safe clamped range", () => {
    for (const preset of Object.values(ORBIT_PRESETS)) {
      expect(preset.polar).toBe(clampPolar(preset.polar));
    }
  });
});

describe("applyDragDelta", () => {
  const start = { azimuth: 10, polar: 55 };

  // Trackball convention: dragging right must orbit the camera so the
  // MODEL appears to turn right (follows the drag), not away from it. See
  // the function's own doc comment for why that means azimuth decreases.
  it("dragging right decreases azimuth (model appears to follow the drag)", () => {
    const result = applyDragDelta(start, 20, 0);
    expect(result.azimuth).toBeLessThan(start.azimuth);
    expect(result.polar).toBe(start.polar);
  });

  it("dragging left increases azimuth", () => {
    expect(applyDragDelta(start, -20, 0).azimuth).toBeGreaterThan(start.azimuth);
  });

  it("dragging down decreases polar (orbits toward looking from above)", () => {
    const result = applyDragDelta(start, 0, 30);
    expect(result.polar).toBeLessThan(start.polar);
  });

  it("dragging up increases polar", () => {
    expect(applyDragDelta(start, 0, -30).polar).toBeGreaterThan(start.polar);
  });

  it("clamps polar even on a large drag", () => {
    expect(applyDragDelta(start, 0, 1000).polar).toBe(clampPolar(-Infinity));
    expect(applyDragDelta(start, 0, -1000).polar).toBe(clampPolar(Infinity));
  });

  it("zero delta is a no-op", () => {
    expect(applyDragDelta(start, 0, 0)).toEqual(start);
  });
});
