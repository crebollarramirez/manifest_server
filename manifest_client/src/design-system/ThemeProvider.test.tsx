// @vitest-environment jsdom
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider, useTheme } from "./ThemeProvider";

/**
 * ThemeProvider is the token system's runtime control: it decides whether
 * data-theme is stamped on <html> at all, so tokens.css's cascade order
 * (media query default -> [data-theme] override) actually resolves correctly.
 */

function makeMatchMedia(prefersDark: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  return {
    matches: prefersDark,
    media: "(prefers-color-scheme: dark)",
    addEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => {
      listeners.add(listener);
    },
    removeEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => {
      listeners.delete(listener);
    },
    dispatch: (matches: boolean) => {
      for (const listener of listeners) {
        listener({ matches } as MediaQueryListEvent);
      }
    },
  };
}

function Probe() {
  const { preference, resolvedTheme, setPreference } = useTheme();
  return (
    <div>
      <span data-testid="preference">{preference}</span>
      <span data-testid="resolved">{resolvedTheme}</span>
      <button onClick={() => setPreference("dark")}>dark</button>
      <button onClick={() => setPreference("light")}>light</button>
      <button onClick={() => setPreference("system")}>system</button>
    </div>
  );
}

describe("ThemeProvider", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("defaults to system preference and does not stamp data-theme", () => {
    vi.stubGlobal("matchMedia", () => makeMatchMedia(false));
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("preference")).toHaveTextContent("system");
    expect(screen.getByTestId("resolved")).toHaveTextContent("light");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("resolves 'system' against a dark OS preference", () => {
    vi.stubGlobal("matchMedia", () => makeMatchMedia(true));
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("an explicit preference stamps data-theme and wins over the OS", () => {
    vi.stubGlobal("matchMedia", () => makeMatchMedia(true));
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "light" }));
    expect(screen.getByTestId("resolved")).toHaveTextContent("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  it("persists an explicit choice and removes the key for 'system'", () => {
    vi.stubGlobal("matchMedia", () => makeMatchMedia(false));
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "dark" }));
    expect(window.localStorage.getItem("manifest:theme")).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: "system" }));
    expect(window.localStorage.getItem("manifest:theme")).toBeNull();
  });

  it("rehydrates an explicit preference from storage on mount", () => {
    vi.stubGlobal("matchMedia", () => makeMatchMedia(false));
    window.localStorage.setItem("manifest:theme", "dark");
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("preference")).toHaveTextContent("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("reacts live to an OS preference change while set to 'system'", () => {
    const media = makeMatchMedia(false);
    vi.stubGlobal("matchMedia", () => media);
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    expect(screen.getByTestId("resolved")).toHaveTextContent("light");
    fireEvent.click(screen.getByRole("button", { name: "system" })); // no-op, already system
    // dispatch() calls the listener directly (not through a DOM event), so
    // fireEvent's automatic act() wrapping doesn't cover it — wrap explicitly
    // to flush the setSystemDark update before asserting.
    act(() => {
      media.dispatch(true);
    });
    expect(screen.getByTestId("resolved")).toHaveTextContent("dark");
  });

  it("throws when useTheme is called outside a ThemeProvider", () => {
    const Bare = () => {
      useTheme();
      return null;
    };
    // Expected error path — suppress React's console.error for this assertion.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Bare />)).toThrow(/within a ThemeProvider/);
    spy.mockRestore();
  });
});
