// @vitest-environment jsdom
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Button } from "./Button";

describe("Button", () => {
  it("renders its label and defaults to a primary, medium, type=button element", () => {
    render(<Button>Export part</Button>);
    const button = screen.getByRole("button", { name: "Export part" });
    expect(button).toHaveAttribute("type", "button");
    expect(button.className).toMatch(/primary/);
    expect(button.className).toMatch(/md/);
  });

  it("fires onClick when enabled", () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("does not fire onClick when disabled", () => {
    const onClick = vi.fn();
    render(
      <Button onClick={onClick} disabled>
        Go
      </Button>,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
    expect(screen.getByRole("button")).toBeDisabled();
  });

  it.each(["primary", "secondary", "ghost", "danger"] as const)(
    "applies the %s variant class",
    (variant) => {
      render(<Button variant={variant}>x</Button>);
      expect(screen.getByRole("button").className).toMatch(new RegExp(variant));
    },
  );

  it("forwards a ref to the underlying <button>", () => {
    const ref = createRef<HTMLButtonElement>();
    render(<Button ref={ref}>x</Button>);
    expect(ref.current).toBeInstanceOf(HTMLButtonElement);
  });

  it("merges a caller-supplied className instead of replacing internal classes", () => {
    render(<Button className="custom">x</Button>);
    const button = screen.getByRole("button");
    expect(button.className).toMatch(/custom/);
    expect(button.className).toMatch(/primary/);
  });

  it("respects an explicit type override (e.g. submit)", () => {
    render(<Button type="submit">x</Button>);
    expect(screen.getByRole("button")).toHaveAttribute("type", "submit");
  });
});
