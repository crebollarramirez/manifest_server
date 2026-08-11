// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { IconButton } from "./IconButton";

describe("IconButton", () => {
  it("uses the required label as the accessible name", () => {
    render(<IconButton label="Undo">{"↶"}</IconButton>);
    expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument();
  });

  it("defaults to ghost/md and fires onClick", () => {
    const onClick = vi.fn();
    render(
      <IconButton label="Zoom in" onClick={onClick}>
        +
      </IconButton>,
    );
    const button = screen.getByRole("button", { name: "Zoom in" });
    expect(button.className).toMatch(/ghost/);
    expect(button.className).toMatch(/md/);
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it.each(["ghost", "soft", "dangerSoft", "primary"] as const)(
    "applies the %s variant class",
    (variant) => {
      render(
        <IconButton label="x" variant={variant}>
          i
        </IconButton>,
      );
      expect(screen.getByRole("button").className).toMatch(new RegExp(variant));
    },
  );

  it("does not fire onClick when disabled", () => {
    const onClick = vi.fn();
    render(
      <IconButton label="x" onClick={onClick} disabled>
        i
      </IconButton>,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });
});
