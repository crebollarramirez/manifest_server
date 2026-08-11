// @vitest-environment jsdom
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AxisCube } from "./AxisCube";
import type { CameraApi, OrbitAngles } from "../viewer/cameraApi";

function makeApiRef() {
  const ref = createRef<CameraApi | null>() as React.MutableRefObject<CameraApi | null>;
  ref.current = {
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    snapTo: vi.fn(),
    getOrbitAngles: vi.fn(() => ({ azimuth: 0, polar: 55 })),
    setOrbitAngles: vi.fn(),
  };
  return ref;
}

const ANGLES: OrbitAngles = { azimuth: -35, polar: 55 };

describe("AxisCube", () => {
  it("clicking FRONT/RIGHT/TOP snaps the real camera to that preset", () => {
    const apiRef = makeApiRef();
    render(<AxisCube cameraApiRef={apiRef} orbitAngles={ANGLES} left={0} />);
    fireEvent.click(screen.getByText("FRONT"));
    expect(apiRef.current?.snapTo).toHaveBeenCalledWith("front");
    fireEvent.click(screen.getByText("RIGHT"));
    expect(apiRef.current?.snapTo).toHaveBeenCalledWith("right");
    fireEvent.click(screen.getByText("TOP"));
    expect(apiRef.current?.snapTo).toHaveBeenCalledWith("top");
  });

  it("BACK/LEFT/BASE are decorative — plain divs, not clickable buttons", () => {
    render(<AxisCube cameraApiRef={makeApiRef()} orbitAngles={ANGLES} left={0} />);
    expect(screen.queryByRole("button", { name: "BACK" })).not.toBeInTheDocument();
    expect(screen.getByText("BACK").tagName).toBe("DIV");
  });

  it("dragging updates the real camera's orbit angles via setOrbitAngles", () => {
    const apiRef = makeApiRef();
    render(<AxisCube cameraApiRef={apiRef} orbitAngles={ANGLES} left={0} />);
    const cube = screen.getByTitle("Drag to orbit — click a face to snap");

    fireEvent.pointerDown(cube, { clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { clientX: 130, clientY: 90 });
    expect(apiRef.current?.setOrbitAngles).toHaveBeenCalledWith({
      azimuth: -35 - 30 * 0.6,
      polar: 55 + 10 * 0.6,
    });
    fireEvent.pointerUp(window, { clientX: 130, clientY: 90 });
  });

  it("drag stops updating angles after pointerup", () => {
    const apiRef = makeApiRef();
    render(<AxisCube cameraApiRef={apiRef} orbitAngles={ANGLES} left={0} />);
    const cube = screen.getByTitle("Drag to orbit — click a face to snap");

    fireEvent.pointerDown(cube, { clientX: 100, clientY: 100 });
    fireEvent.pointerUp(window, { clientX: 100, clientY: 100 });
    vi.mocked(apiRef.current!.setOrbitAngles).mockClear();
    fireEvent.pointerMove(window, { clientX: 200, clientY: 200 });
    expect(apiRef.current?.setOrbitAngles).not.toHaveBeenCalled();
  });

  it("positions itself via the left prop", () => {
    render(<AxisCube cameraApiRef={makeApiRef()} orbitAngles={ANGLES} left={412} />);
    expect(screen.getByTitle("Drag to orbit — click a face to snap")).toHaveStyle({
      left: "412px",
    });
  });
});
