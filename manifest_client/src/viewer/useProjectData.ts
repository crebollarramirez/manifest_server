import { useCallback, useEffect, useRef, useState } from "react";
import { decodeGeometry } from "./decode/workerClient";
import type { FixtureCadAgentClient } from "../api/fixtureClient";
import type { PartRecord } from "../api/schemas";
import type { Plate } from "./PlateGrid";
import type { PlateStatus } from "./PartPlate";

/**
 * The single real data source for a project: fetches the part list, then
 * resolves each part's latest export -> artifact -> decoded geometry.
 * Shared by PreviewLayer (the 3D canvas) and the UI chrome (PlateSelector,
 * DimensionsChip) so both read the identical, single-fetched state instead
 * of each re-deriving it — call this once (in AppShell) and pass `plates`
 * down as a prop everywhere else.
 *
 * `refreshPart` lets ChatPanel pull in a specific part's newly-completed
 * export after a chat-driven edit finishes, without refetching the whole
 * project — the rest of the app's plates stay untouched.
 *
 * PHASE-1 WIRING NOTE (carried over from the code this was extracted from):
 * polls via a plain retry loop against the fixture client. Phase 3 replaces
 * this with TanStack Query; nothing above this hook's return value changes.
 *
 * Typed against the concrete FixtureCadAgentClient, not the CadAgentClient
 * interface, because it calls latestExportJobIdForPart() — a fixture-only
 * stand-in for the optional get_part_artifacts action (CONTRACT.md §4.1).
 * Pre-existing coupling (unchanged from the code this was extracted from),
 * called out explicitly rather than silently typed away; revisit once
 * get_part_artifacts or get_export_job ships and the live client exists.
 */

const TERMINAL: readonly string[] = ["completed", "failed", "cancelled"];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function resolveExportJobToStatus(
  client: FixtureCadAgentClient,
  part: PartRecord,
  jobId: string,
): Promise<PlateStatus> {
  try {
    let exportJob = await client.getExportJob(jobId);
    for (let poll = 0; poll < 40 && !TERMINAL.includes(exportJob.status); poll += 1) {
      await sleep(150);
      exportJob = await client.getExportJob(jobId);
    }
    if (exportJob.status !== "completed") {
      throw new Error(exportJob.job.error_message ?? "Export did not complete.");
    }
    const wanted = part.part_type === "mesh" ? "model.glb" : "model.stl";
    const artifact = exportJob.artifacts?.find((a) => a.file === wanted);
    if (!artifact) throw new Error("No renderable artifact in export.");
    const response = await fetch(artifact.url);
    if (!response.ok) throw new Error(`Artifact fetch failed (${response.status}).`);
    const buffer = await response.arrayBuffer();
    const decoded = await decodeGeometry(part.part_type === "mesh" ? "glb" : "stl", buffer);
    return { kind: "ready", decoded };
  } catch (error) {
    return {
      kind: "error",
      message: error instanceof Error ? error.message : "Failed to load part.",
    };
  }
}

export function useProjectData(
  client: FixtureCadAgentClient,
  projectId: string,
): { plates: Plate[]; refreshPart: (partId: string, exportJobId: string) => Promise<void> } {
  const [plates, setPlates] = useState<Plate[]>([]);
  const platesRef = useRef(plates);
  platesRef.current = plates;

  const setStatus = useCallback((partId: string, status: PlateStatus) => {
    setPlates((current) =>
      current.map((plate) => (plate.part.id === partId ? { ...plate, status } : plate)),
    );
  }, []);

  useEffect(() => {
    let cancelled = false;

    void (async () => {
      const listed = await client.listParts(projectId);
      if (cancelled) return;
      const jobIds = new Map<string, string | null>(
        listed.parts.map((part) => [part.id, client.latestExportJobIdForPart(part.id)]),
      );
      // Plates exist immediately; geometry arrival never shifts layout.
      setPlates(
        listed.parts.map((part) => ({
          part,
          status: jobIds.get(part.id) ? { kind: "loading" } : { kind: "blank" },
        })),
      );

      await Promise.all(
        listed.parts.map(async (part) => {
          const jobId = jobIds.get(part.id);
          if (!jobId) return;
          const status = await resolveExportJobToStatus(client, part, jobId);
          if (!cancelled) setStatus(part.id, status);
        }),
      );
    })();

    return () => {
      cancelled = true;
    };
  }, [client, projectId, setStatus]);

  const refreshPart = useCallback(
    async (partId: string, exportJobId: string) => {
      const part = platesRef.current.find((plate) => plate.part.id === partId)?.part;
      if (!part) return;
      setStatus(partId, { kind: "loading" });
      const status = await resolveExportJobToStatus(client, part, exportJobId);
      setStatus(partId, status);
    },
    [client, setStatus],
  );

  return { plates, refreshPart };
}
