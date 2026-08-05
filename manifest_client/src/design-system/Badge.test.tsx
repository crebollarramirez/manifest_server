// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge, statusToBadgeVariant, type BadgeVariant } from "./Badge";
import type { JobStatus } from "../api/schemas";

describe("Badge", () => {
  it("defaults to the neutral variant", () => {
    render(<Badge>queued</Badge>);
    expect(screen.getByText("queued").className).toMatch(/neutral/);
  });

  it.each(["neutral", "info", "success", "warning", "error"] as const)(
    "applies the %s variant class",
    (variant) => {
      render(<Badge variant={variant}>x</Badge>);
      expect(screen.getByText("x").className).toMatch(new RegExp(variant));
    },
  );

  it("renders as a <span> and merges a caller className", () => {
    render(<Badge className="custom">x</Badge>);
    const badge = screen.getByText("x");
    expect(badge.tagName).toBe("SPAN");
    expect(badge.className).toMatch(/custom/);
  });
});

describe("statusToBadgeVariant", () => {
  const expected: Record<JobStatus, BadgeVariant> = {
    queued: "neutral",
    running: "info",
    completed: "success",
    failed: "error",
    cancelled: "neutral",
  };

  it.each(Object.entries(expected) as Array<[JobStatus, BadgeVariant]>)(
    "maps %s -> %s",
    (status, variant) => {
      expect(statusToBadgeVariant(status)).toBe(variant);
    },
  );

  it("covers every JobStatus value with no gaps", () => {
    expect(Object.keys(expected).sort()).toEqual(
      ["queued", "running", "completed", "failed", "cancelled"].sort(),
    );
  });
});
