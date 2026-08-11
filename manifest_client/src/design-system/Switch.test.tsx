// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Switch } from "./Switch";

describe("Switch", () => {
  it("renders as a switch role with the label as its accessible name", () => {
    render(<Switch label="Supports" checked={false} onChange={() => {}} />);
    expect(screen.getByRole("switch", { name: "Supports" })).toBeInTheDocument();
  });

  it("reflects the checked prop", () => {
    render(<Switch label="Supports" checked onChange={() => {}} />);
    expect(screen.getByRole("switch")).toBeChecked();
  });

  it("fires onChange on click", () => {
    const onChange = vi.fn();
    render(<Switch label="Supports" checked={false} onChange={onChange} />);
    fireEvent.click(screen.getByRole("switch"));
    expect(onChange).toHaveBeenCalledOnce();
  });

  it("respects disabled", () => {
    render(<Switch label="Supports" checked={false} onChange={() => {}} disabled />);
    expect(screen.getByRole("switch")).toBeDisabled();
  });
});
