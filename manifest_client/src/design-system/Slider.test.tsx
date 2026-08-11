// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Slider } from "./Slider";

describe("Slider", () => {
  it("renders as a slider with the label as its accessible name", () => {
    render(<Slider label="Fill" min={0} max={100} value={15} onChange={() => {}} />);
    expect(screen.getByRole("slider", { name: "Fill" })).toBeInTheDocument();
  });

  it("reflects the controlled value and min/max", () => {
    render(<Slider label="Fill" min={0} max={100} value={42} onChange={() => {}} />);
    const slider = screen.getByRole("slider");
    expect(slider).toHaveValue("42");
    expect(slider).toHaveAttribute("min", "0");
    expect(slider).toHaveAttribute("max", "100");
  });

  it("fires onChange with the new value", () => {
    // Same controlled-input caveat as Select's equivalent test: capture the
    // value inside the handler, since event.target is a live DOM node React
    // resets back to the controlled `value` prop once the mock returns.
    let observedValue: string | undefined;
    const onChange = vi.fn((event: React.ChangeEvent<HTMLInputElement>) => {
      observedValue = event.target.value;
    });
    render(<Slider label="Fill" min={0} max={100} value={15} onChange={onChange} />);
    fireEvent.change(screen.getByRole("slider"), { target: { value: "80" } });
    expect(onChange).toHaveBeenCalledOnce();
    expect(observedValue).toBe("80");
  });

  it("respects disabled", () => {
    render(<Slider label="Fill" min={0} max={100} value={15} onChange={() => {}} disabled />);
    expect(screen.getByRole("slider")).toBeDisabled();
  });
});
