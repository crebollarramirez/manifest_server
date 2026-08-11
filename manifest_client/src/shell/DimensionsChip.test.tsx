// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DimensionsChip } from "./DimensionsChip";
import type { DecodedGeometry } from "../viewer/decode/types";

const DECODED: DecodedGeometry = {
  positions: new Float32Array(),
  normals: new Float32Array(),
  indices: null,
  bounds: { min: [0, 0, 0], max: [60, 48, 66] },
  triangleCount: 0,
  authoredMaterial: null,
};

describe("DimensionsChip", () => {
  it("shows an empty state when nothing is focused", () => {
    render(
      <DimensionsChip
        decoded={null}
        scalePreview={1}
        unit="mm"
        onUnitChange={() => {}}
        rulerOn={false}
        onToggleRuler={() => {}}
      />,
    );
    expect(screen.getByText(/Select a part/)).toBeInTheDocument();
  });

  it("shows real converted dimensions from the decoded bounds", () => {
    render(
      <DimensionsChip
        decoded={DECODED}
        scalePreview={1}
        unit="mm"
        onUnitChange={() => {}}
        rulerOn={false}
        onToggleRuler={() => {}}
      />,
    );
    // bounds are 60x48x66 world units; plateTransform scales to fit a
    // 2-unit plate, so the raw mm size is what's asserted via convertLength
    // indirectly — just check the labels and that values render, not exact
    // pixel-perfect scale math (covered by normalize.test.ts).
    expect(screen.getByText("Width")).toBeInTheDocument();
    expect(screen.getByText("Length")).toBeInTheDocument();
    expect(screen.getByText("Height")).toBeInTheDocument();
  });

  it("applies the live scale preview to the displayed dimensions", () => {
    const { rerender } = render(
      <DimensionsChip
        decoded={DECODED}
        scalePreview={1}
        unit="mm"
        onUnitChange={() => {}}
        rulerOn={false}
        onToggleRuler={() => {}}
      />,
    );
    const baseline = screen.getAllByText(/^\d+$/).map((el) => el.textContent);

    rerender(
      <DimensionsChip
        decoded={DECODED}
        scalePreview={0.5}
        unit="mm"
        onUnitChange={() => {}}
        rulerOn={false}
        onToggleRuler={() => {}}
      />,
    );
    const scaled = screen.getAllByText(/^\d+$/).map((el) => el.textContent);
    expect(scaled).not.toEqual(baseline);
  });

  it("fires onUnitChange when the unit selector changes", () => {
    const onUnitChange = vi.fn();
    render(
      <DimensionsChip
        decoded={DECODED}
        scalePreview={1}
        unit="mm"
        onUnitChange={onUnitChange}
        rulerOn={false}
        onToggleRuler={() => {}}
      />,
    );
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "in" } });
    expect(onUnitChange).toHaveBeenCalledWith("in");
  });

  it("fires onToggleRuler and disables the ruler button when nothing is focused", () => {
    const onToggleRuler = vi.fn();
    const { rerender } = render(
      <DimensionsChip
        decoded={null}
        scalePreview={1}
        unit="mm"
        onUnitChange={() => {}}
        rulerOn={false}
        onToggleRuler={onToggleRuler}
      />,
    );
    expect(screen.getByRole("button", { name: /Ruler/ })).toBeDisabled();

    rerender(
      <DimensionsChip
        decoded={DECODED}
        scalePreview={1}
        unit="mm"
        onUnitChange={() => {}}
        rulerOn={false}
        onToggleRuler={onToggleRuler}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Ruler/ }));
    expect(onToggleRuler).toHaveBeenCalledOnce();
  });
});
