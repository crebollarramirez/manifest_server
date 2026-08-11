// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PlateSelector } from "./PlateSelector";
import type { PartRecord } from "../api/schemas";

const PARTS: PartRecord[] = [
  { id: "1", project_id: "p", part_name: "bracket", part_type: "cad" },
  { id: "2", project_id: "p", part_name: "spaceship", part_type: "mesh" },
];

describe("PlateSelector", () => {
  it("shows 'All parts' when nothing is focused", () => {
    render(<PlateSelector parts={PARTS} focusedPartId={null} onFocus={() => {}} />);
    expect(screen.getByText("All parts")).toBeInTheDocument();
  });

  it("shows the focused part's name as the trigger label", () => {
    render(<PlateSelector parts={PARTS} focusedPartId="1" onFocus={() => {}} />);
    expect(screen.getByText("bracket")).toBeInTheDocument();
  });

  it("opens the menu listing every real part plus 'All parts', no fabricated groups", () => {
    render(<PlateSelector parts={PARTS} focusedPartId={null} onFocus={() => {}} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getAllByText("All parts")).toHaveLength(2); // trigger + menu item
    expect(screen.getByText("spaceship")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument(); // part count on "All parts"
  });

  it("selecting a part calls onFocus with its id and closes the menu", () => {
    const onFocus = vi.fn();
    render(<PlateSelector parts={PARTS} focusedPartId={null} onFocus={onFocus} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    fireEvent.click(screen.getByText("spaceship"));
    expect(onFocus).toHaveBeenCalledWith("2");
    expect(screen.queryByText("cad")).not.toBeInTheDocument(); // menu closed
  });

  it("selecting 'All parts' from the menu calls onFocus(null)", () => {
    const onFocus = vi.fn();
    // Trigger shows "bracket" here (a part is focused), so only the menu's
    // "All parts" item matches — unlike the earlier test where both did.
    render(<PlateSelector parts={PARTS} focusedPartId="1" onFocus={onFocus} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    fireEvent.click(screen.getByText("All parts"));
    expect(onFocus).toHaveBeenCalledWith(null);
  });
});
