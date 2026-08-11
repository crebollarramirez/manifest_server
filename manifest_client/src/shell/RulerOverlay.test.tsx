// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { RulerOverlay } from "./RulerOverlay";
import type { DecodedGeometry } from "../viewer/decode/types";

const DECODED: DecodedGeometry = {
  positions: new Float32Array(),
  normals: new Float32Array(),
  indices: null,
  bounds: { min: [0, 0, 0], max: [60, 48, 66] },
  triangleCount: 0,
  authoredMaterial: null,
};

describe("RulerOverlay", () => {
  it("shows W/L/H labels with the real converted dimensions and unit suffix", () => {
    render(<RulerOverlay decoded={DECODED} unit="mm" />);
    expect(screen.getByText("W 60 mm")).toBeInTheDocument();
    expect(screen.getByText("L 48 mm")).toBeInTheDocument();
    expect(screen.getByText("H 66 mm")).toBeInTheDocument();
  });

  it("respects the selected unit", () => {
    render(<RulerOverlay decoded={DECODED} unit="in" />);
    expect(screen.getByText(`W ${(60 / 25.4).toFixed(2)} in`)).toBeInTheDocument();
  });
});
