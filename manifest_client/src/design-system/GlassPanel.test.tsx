// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { GlassPanel } from "./GlassPanel";

describe("GlassPanel", () => {
  it("defaults to the standard .glass primitive", () => {
    render(<GlassPanel>content</GlassPanel>);
    const panel = screen.getByText("content");
    expect(panel.className).toMatch(/\bglass\b/);
    expect(panel.className).not.toMatch(/glass--/);
  });

  it.each([
    ["strong", ["glass", "glass--strong"]],
    ["subtle", ["glass", "glass--subtle"]],
    ["gloss", ["glass--gloss"]],
  ] as const)("applies the %s variant's primitive classes", (variant, expectedParts) => {
    render(<GlassPanel variant={variant}>content</GlassPanel>);
    const panel = screen.getByText("content");
    for (const part of expectedParts) {
      expect(panel.className).toMatch(new RegExp(part.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&")));
    }
  });

  it("renders children and merges a caller className", () => {
    render(<GlassPanel className="custom">hello</GlassPanel>);
    const panel = screen.getByText("hello");
    expect(panel.className).toMatch(/custom/);
  });

  it("forwards arbitrary div props (e.g. onClick, role)", () => {
    render(
      <GlassPanel role="dialog" aria-label="settings">
        panel body
      </GlassPanel>,
    );
    expect(screen.getByRole("dialog", { name: "settings" })).toBeInTheDocument();
  });
});
