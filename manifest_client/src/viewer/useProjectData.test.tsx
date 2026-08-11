// @vitest-environment jsdom
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { createFixtureClient } from "../api/fixtureClient";
import { FIXTURE_BLANK_PART_ID, FIXTURE_CAD_PART_ID, FIXTURE_PROJECT_ID } from "../api/fixtureIds";
import { useProjectData } from "./useProjectData";

// jsdom has no Worker implementation; decodeGeometry's real path creates
// one, so it's mocked here. This test verifies the hook's own state-machine
// (blank -> loading -> ready/error) against the real fixture client, not
// the decode pipeline itself (covered separately in decode/*.test.ts).
vi.mock("./decode/workerClient", () => ({
  decodeGeometry: vi.fn(async () => ({
    positions: new Float32Array(9),
    normals: new Float32Array(9),
    indices: null,
    bounds: { min: [0, 0, 0], max: [1, 1, 1] },
    triangleCount: 1,
    authoredMaterial: null,
  })),
}));

const originalFetch = global.fetch;

describe("useProjectData", () => {
  beforeEach(() => {
    // Fetching real fixture files (public/fixtures/...) doesn't work under
    // jsdom's default fetch; stub a successful small-buffer response.
    global.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(8),
    })) as unknown as typeof fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("lists real parts immediately, blank part shown as blank not loading", async () => {
    const client = createFixtureClient();
    const { result } = renderHook(() => useProjectData(client, FIXTURE_PROJECT_ID));

    await waitFor(() => expect(result.current.plates.length).toBe(4));
    const blank = result.current.plates.find((p) => p.part.id === FIXTURE_BLANK_PART_ID);
    expect(blank?.status.kind).toBe("blank");
  });

  it("resolves parts with export history to ready, using the mocked decode", async () => {
    const client = createFixtureClient();
    const { result } = renderHook(() => useProjectData(client, FIXTURE_PROJECT_ID));

    await waitFor(() => {
      const nonBlank = result.current.plates.filter((p) => p.status.kind !== "blank");
      expect(nonBlank.length).toBeGreaterThan(0);
      expect(nonBlank.every((p) => p.status.kind === "ready")).toBe(true);
    });
  });

  it("surfaces a fetch failure as an error status, not a thrown exception", async () => {
    global.fetch = vi.fn(async () => ({
      ok: false,
      status: 404,
      arrayBuffer: async () => new ArrayBuffer(0),
    })) as unknown as typeof fetch;

    const client = createFixtureClient();
    const { result } = renderHook(() => useProjectData(client, FIXTURE_PROJECT_ID));

    await waitFor(() => {
      const errored = result.current.plates.filter((p) => p.status.kind === "error");
      expect(errored.length).toBeGreaterThan(0);
    });
  });

  it("refreshPart re-resolves a single part's status without touching the others", async () => {
    const client = createFixtureClient();
    const { result } = renderHook(() => useProjectData(client, FIXTURE_PROJECT_ID));
    await waitFor(() => expect(result.current.plates.length).toBe(4));

    const otherStatusesBefore = result.current.plates
      .filter((p) => p.part.id !== FIXTURE_CAD_PART_ID)
      .map((p) => p.status.kind);

    const newExportJob = await client.exportPart(FIXTURE_CAD_PART_ID);
    await act(async () => {
      await result.current.refreshPart(FIXTURE_CAD_PART_ID, newExportJob.job_id);
    });

    await waitFor(() => {
      const cadPlate = result.current.plates.find((p) => p.part.id === FIXTURE_CAD_PART_ID);
      expect(cadPlate?.status.kind).toBe("ready");
    });
    const otherStatusesAfter = result.current.plates
      .filter((p) => p.part.id !== FIXTURE_CAD_PART_ID)
      .map((p) => p.status.kind);
    expect(otherStatusesAfter).toEqual(otherStatusesBefore);
  });
});
