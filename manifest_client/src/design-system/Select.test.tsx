// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Select } from "./Select";

const UNIT_OPTIONS = [
  { label: "Millimeters (mm)", value: "mm" },
  { label: "Centimeters (cm)", value: "cm" },
  { label: "Inches (in)", value: "in" },
];

describe("Select", () => {
  it("renders every option", () => {
    render(<Select options={UNIT_OPTIONS} value="mm" onChange={() => {}} />);
    for (const option of UNIT_OPTIONS) {
      expect(screen.getByRole("option", { name: option.label })).toBeInTheDocument();
    }
  });

  it("reflects the controlled value", () => {
    render(<Select options={UNIT_OPTIONS} value="cm" onChange={() => {}} />);
    expect(screen.getByRole("combobox")).toHaveValue("cm");
  });

  it("fires onChange with the native event on selection", () => {
    // event.target is a live DOM reference: since this is a controlled
    // select and the mock never updates `value`, React resets the DOM node
    // back to "mm" after the handler runs. Capture the value synchronously
    // inside the handler rather than reading it from mock.calls afterward.
    let observedValue: string | undefined;
    const onChange = vi.fn((event: React.ChangeEvent<HTMLSelectElement>) => {
      observedValue = event.target.value;
    });
    render(<Select options={UNIT_OPTIONS} value="mm" onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "in" } });
    expect(onChange).toHaveBeenCalledOnce();
    expect(observedValue).toBe("in");
  });

  it("respects disabled", () => {
    render(<Select options={UNIT_OPTIONS} value="mm" onChange={() => {}} disabled />);
    expect(screen.getByRole("combobox")).toBeDisabled();
  });
});
