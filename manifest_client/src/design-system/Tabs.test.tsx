// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Tabs } from "./Tabs";

const SETTINGS_TABS = [
  { label: "Material", value: "material" },
  { label: "Size", value: "size" },
  { label: "Quality", value: "quality" },
];

describe("Tabs", () => {
  it("renders every tab as a role=tab button", () => {
    render(<Tabs tabs={SETTINGS_TABS} active="material" onChange={() => {}} />);
    for (const tab of SETTINGS_TABS) {
      expect(screen.getByRole("tab", { name: tab.label })).toBeInTheDocument();
    }
  });

  it("marks only the active tab as selected", () => {
    render(<Tabs tabs={SETTINGS_TABS} active="size" onChange={() => {}} />);
    expect(screen.getByRole("tab", { name: "Size" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Material" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
  });

  it("fires onChange with the clicked tab's value", () => {
    const onChange = vi.fn();
    render(<Tabs tabs={SETTINGS_TABS} active="material" onChange={onChange} />);
    fireEvent.click(screen.getByRole("tab", { name: "Quality" }));
    expect(onChange).toHaveBeenCalledWith("quality");
  });
});
