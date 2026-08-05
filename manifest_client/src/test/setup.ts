import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Runs for every test file regardless of environment. Only jsdom-environment
// (component) tests have a `document` to unmount from; node-environment
// (pure-logic) tests no-op here.
if (typeof document !== "undefined") {
  // Silences React's "not configured to support act()" warning — component
  // tests use act() deliberately (e.g. ThemeProvider.test.tsx) to flush
  // updates triggered outside a DOM event.
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
}

afterEach(() => {
  if (typeof document !== "undefined") {
    cleanup();
  }
});
