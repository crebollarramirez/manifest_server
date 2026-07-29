import { describe, expect, it } from "vitest";
import { Crossfade, CROSSFADE_MS } from "./crossfade";

describe("Crossfade", () => {
  it("is idle (progress 1, inactive) before begin", () => {
    const fade = new Crossfade();
    expect(fade.progress(1000)).toBe(1);
    expect(fade.active(1000)).toBe(false);
    expect(fade.done(1000)).toBe(false);
  });

  it("progresses 0 -> 1 across the duration", () => {
    const fade = new Crossfade(300);
    fade.begin(1000);
    expect(fade.progress(1000)).toBe(0);
    expect(fade.progress(1150)).toBeCloseTo(0.5);
    expect(fade.progress(1300)).toBe(1);
    expect(fade.active(1150)).toBe(true);
    expect(fade.done(1299)).toBe(false);
    expect(fade.done(1300)).toBe(true);
  });

  it("clamps and never exceeds 1, even long after completion", () => {
    const fade = new Crossfade(300);
    fade.begin(0);
    expect(fade.progress(100_000)).toBe(1);
  });

  it("resets to idle", () => {
    const fade = new Crossfade();
    fade.begin(0);
    fade.reset();
    expect(fade.active(10)).toBe(false);
    expect(fade.progress(10)).toBe(1);
  });

  it("defaults to the ~300ms budget from the plan", () => {
    expect(CROSSFADE_MS).toBe(300);
  });
});
