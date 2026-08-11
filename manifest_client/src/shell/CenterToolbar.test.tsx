// @vitest-environment jsdom
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CenterToolbar } from "./CenterToolbar";
import type { CameraApi } from "../viewer/cameraApi";

function makeApiRef(overrides: Partial<CameraApi> = {}) {
  const ref = createRef<CameraApi | null>();
  (ref as React.MutableRefObject<CameraApi | null>).current = {
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    snapTo: vi.fn(),
    getOrbitAngles: vi.fn(() => ({ azimuth: 0, polar: 55 })),
    setOrbitAngles: vi.fn(),
    ...overrides,
  };
  return ref as React.MutableRefObject<CameraApi | null>;
}

describe("CenterToolbar", () => {
  it("undo, redo, and the version pill are honestly disabled (no edit history exists)", () => {
    render(<CenterToolbar cameraApiRef={makeApiRef()} />);
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Redo" })).toBeDisabled();
    expect(screen.getByText("v1")).toBeInTheDocument();
  });

  it("zoom in calls the real camera API", () => {
    const apiRef = makeApiRef();
    render(<CenterToolbar cameraApiRef={apiRef} />);
    fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(apiRef.current?.zoomIn).toHaveBeenCalledOnce();
  });

  it("zoom out calls the real camera API", () => {
    const apiRef = makeApiRef();
    render(<CenterToolbar cameraApiRef={apiRef} />);
    fireEvent.click(screen.getByRole("button", { name: "Zoom out" }));
    expect(apiRef.current?.zoomOut).toHaveBeenCalledOnce();
  });

  it("zoom buttons no-op safely before the Canvas has mounted (ref.current is null)", () => {
    const ref = createRef<CameraApi | null>() as React.MutableRefObject<CameraApi | null>;
    render(<CenterToolbar cameraApiRef={ref} />);
    expect(() =>
      fireEvent.click(screen.getByRole("button", { name: "Zoom in" })),
    ).not.toThrow();
  });
});
