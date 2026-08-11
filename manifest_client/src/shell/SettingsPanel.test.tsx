// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SettingsPanel } from "./SettingsPanel";
import type { DecodedGeometry } from "../viewer/decode/types";

const DECODED: DecodedGeometry = {
  positions: new Float32Array(),
  normals: new Float32Array(),
  indices: null,
  bounds: { min: [0, 0, 0], max: [60, 48, 66] },
  triangleCount: 0,
  authoredMaterial: null,
};

function baseProps() {
  return {
    open: true,
    onToggleOpen: vi.fn(),
    filamentColor: null,
    onFilamentColorChange: vi.fn(),
    scalePct: 100,
    onScalePctChange: vi.fn(),
    unit: "mm" as const,
    onUnitChange: vi.fn(),
    decoded: DECODED,
  };
}

describe("SettingsPanel", () => {
  it("collapsed state shows only the 'Print settings' pill", () => {
    render(<SettingsPanel {...baseProps()} open={false} />);
    expect(screen.getByText("Print settings")).toBeInTheDocument();
    expect(screen.queryByText("Material")).not.toBeInTheDocument();
  });

  it("clicking the collapsed pill calls onToggleOpen", () => {
    const onToggleOpen = vi.fn();
    render(<SettingsPanel {...baseProps()} open={false} onToggleOpen={onToggleOpen} />);
    fireEvent.click(screen.getByText("Print settings"));
    expect(onToggleOpen).toHaveBeenCalledOnce();
  });

  it("defaults to the Material tab, showing filament swatches", () => {
    render(<SettingsPanel {...baseProps()} />);
    expect(screen.getByRole("tab", { name: "Material" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("button", { name: "Purple" })).toBeInTheDocument();
  });

  it("clicking a filament swatch calls onFilamentColorChange with its hex", () => {
    const onFilamentColorChange = vi.fn();
    render(<SettingsPanel {...baseProps()} onFilamentColorChange={onFilamentColorChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Orange" }));
    expect(onFilamentColorChange).toHaveBeenCalledWith("#F2A868");
  });

  it("clicking the already-active swatch clears the override (toggles off)", () => {
    const onFilamentColorChange = vi.fn();
    render(
      <SettingsPanel
        {...baseProps()}
        filamentColor="#F2A868"
        onFilamentColorChange={onFilamentColorChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Orange" }));
    expect(onFilamentColorChange).toHaveBeenCalledWith(null);
  });

  it("switching to the Size tab shows real dimensions scaled by scalePct", () => {
    render(<SettingsPanel {...baseProps()} scalePct={50} />);
    fireEvent.click(screen.getByRole("tab", { name: "Size" }));
    expect(screen.getByText("Width")).toBeInTheDocument();
    // 60mm width at 50% scale -> 30mm
    expect(screen.getByText("30")).toBeInTheDocument();
  });

  it("moving the size slider calls onScalePctChange with the real backing scale state", () => {
    const onScalePctChange = vi.fn();
    render(<SettingsPanel {...baseProps()} onScalePctChange={onScalePctChange} />);
    fireEvent.click(screen.getByRole("tab", { name: "Size" }));
    fireEvent.change(screen.getByRole("slider", { name: "Size" }), {
      target: { value: "120" },
    });
    expect(onScalePctChange).toHaveBeenCalledWith(120);
  });

  it("Size tab shows an em dash when no part is focused (decoded is null)", () => {
    render(<SettingsPanel {...baseProps()} decoded={null} />);
    fireEvent.click(screen.getByRole("tab", { name: "Size" }));
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("Quality tab controls are local-only (infill/layer/supports don't call any external handler)", () => {
    render(<SettingsPanel {...baseProps()} />);
    fireEvent.click(screen.getByRole("tab", { name: "Quality" }));
    expect(screen.getByText(/Fill —/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch", { name: "Supports" }));
    expect(screen.getByRole("switch", { name: "Supports" })).not.toBeChecked();
  });
});
