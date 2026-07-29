import { useEffect, useMemo, useState } from "react";
import { Canvas } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { PlateGrid, type Plate } from "./PlateGrid";
import { decodeGeometry } from "./decode/workerClient";
import { createFixtureClient } from "../api/fixtureClient";
import { FIXTURE_PROJECT_ID } from "../api/fixtureIds";
import type { PlateStatus } from "./PartPlate";

/**
 * Single-canvas project view: one WebGL context for all plates,
 * frameloop="demand" so idle GPU usage is ~0 (drei's OrbitControls
 * invalidates on interaction; loading/fade components invalidate while
 * active).
 *
 * PHASE-1 WIRING NOTE: data loading below talks to the fixture client
 * directly with ad-hoc polling. Phase 3 replaces this entire effect with
 * TanStack Query (useExportJob/usePartGeometry) and prefetch-on-complete;
 * nothing in the component tree below the plate list will change.
 */

const TERMINAL: readonly string[] = ["completed", "failed", "cancelled"];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function ProjectView() {
  const client = useMemo(() => createFixtureClient(), []);
  const [plates, setPlates] = useState<Plate[]>([]);
  const [selectedPartId, setSelectedPartId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const setStatus = (partId: string, status: PlateStatus): void => {
      if (cancelled) return;
      setPlates((current) =>
        current.map((plate) =>
          plate.part.id === partId ? { ...plate, status } : plate,
        ),
      );
    };

    void (async () => {
      const listed = await client.listParts(FIXTURE_PROJECT_ID);
      if (cancelled) return;
      const jobIds = new Map<string, string | null>(
        listed.parts.map((part) => [
          part.id,
          client.latestExportJobIdForPart(part.id),
        ]),
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
          try {
            let exportJob = await client.getExportJob(jobId);
            for (
              let poll = 0;
              poll < 40 && !TERMINAL.includes(exportJob.status);
              poll += 1
            ) {
              await sleep(150);
              exportJob = await client.getExportJob(jobId);
            }
            if (exportJob.status !== "completed") {
              throw new Error(
                exportJob.job.error_message ?? "Export did not complete.",
              );
            }
            const wanted = part.part_type === "mesh" ? "model.glb" : "model.stl";
            const artifact = exportJob.artifacts?.find((a) => a.file === wanted);
            if (!artifact) throw new Error("No renderable artifact in export.");
            const response = await fetch(artifact.url);
            if (!response.ok) {
              throw new Error(`Artifact fetch failed (${response.status}).`);
            }
            const buffer = await response.arrayBuffer();
            const decoded = await decodeGeometry(
              part.part_type === "mesh" ? "glb" : "stl",
              buffer,
            );
            setStatus(part.id, { kind: "ready", decoded });
          } catch (error) {
            setStatus(part.id, {
              kind: "error",
              message:
                error instanceof Error ? error.message : "Failed to load part.",
            });
          }
        }),
      );
    })();

    return () => {
      cancelled = true;
    };
  }, [client]);

  return (
    <Canvas
      frameloop="demand"
      dpr={[1, 2]}
      camera={{ position: [7, 6, 9], fov: 40 }}
      onPointerMissed={() => setSelectedPartId(null)}
    >
      <ambientLight intensity={0.55} />
      <directionalLight position={[6, 10, 4]} intensity={1.4} />
      <directionalLight position={[-6, 4, -6]} intensity={0.4} />
      <PlateGrid
        plates={plates}
        selectedPartId={selectedPartId}
        onSelect={(partId) =>
          setSelectedPartId((current) => (current === partId ? null : partId))
        }
      />
      <OrbitControls makeDefault />
    </Canvas>
  );
}
