import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Default environment is "node" — fast, and correct for the pure-logic
    // suites (decode, materials, api). Component tests opt into jsdom via a
    // "// @vitest-environment jsdom" pragma at the top of the test file.
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test/setup.ts"],
  },
});
