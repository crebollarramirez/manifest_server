// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Card } from "./Card";

describe("Card", () => {
  it("renders children on the solid surface, untinted by default", () => {
    render(<Card>dense content</Card>);
    const card = screen.getByText("dense content");
    expect(card.className).toMatch(/card/);
    expect(card.className).not.toMatch(/tinted/);
  });

  it("applies the tinted variant when requested", () => {
    render(<Card tinted>content</Card>);
    expect(screen.getByText("content").className).toMatch(/tinted/);
  });

  it("forwards div props and merges className", () => {
    render(
      <Card className="custom" data-testid="panel">
        x
      </Card>,
    );
    const card = screen.getByTestId("panel");
    expect(card.className).toMatch(/custom/);
  });
});
