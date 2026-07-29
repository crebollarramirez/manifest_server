import { useRef, useState } from "react";
import { useFrame } from "@react-three/fiber";
import { Html, useCursor } from "@react-three/drei";
import type { MeshStandardMaterial } from "three";
import { PartModel } from "./PartModel";
import { formatDimensions, plateTransform } from "./normalize";
import type { DecodedGeometry } from "./decode/types";
import type { PartRecord } from "../api/schemas";

/**
 * One plate per part. The plate (pedestal + label) exists as soon as the part
 * list arrives — before any geometry — so geometry arrival never causes
 * layout shift. Backend-originated strings (part names, error messages)
 * render through React's default escaping only.
 */

export type PlateStatus =
  | { kind: "loading" }
  | { kind: "ready"; decoded: DecodedGeometry }
  | { kind: "blank" }
  | { kind: "error"; message: string };

const PLATE_SIZE = 2.6;

/** Pedestal shimmer while geometry loads; requests frames only while mounted. */
function LoadingPulse({
  target,
}: {
  target: React.RefObject<MeshStandardMaterial | null>;
}) {
  useFrame((state) => {
    if (target.current) {
      const t = state.clock.getElapsedTime();
      target.current.emissiveIntensity = 0.12 + 0.1 * (1 + Math.sin(t * 3.5));
    }
    state.invalidate();
  });
  return null;
}

export function PartPlate({
  part,
  status,
  position,
  selected,
  onSelect,
}: {
  part: PartRecord;
  status: PlateStatus;
  position: [number, number, number];
  selected: boolean;
  onSelect: (partId: string) => void;
}) {
  const pedestalMaterial = useRef<MeshStandardMaterial>(null);
  const [hovered, setHovered] = useState(false);
  useCursor(hovered);
  const dimensions =
    status.kind === "ready"
      ? formatDimensions(plateTransform(status.decoded.bounds).size)
      : null;

  return (
    <group position={position}>
      <mesh position={[0, -0.1, 0]}>
        <boxGeometry args={[PLATE_SIZE, 0.2, PLATE_SIZE]} />
        <meshStandardMaterial
          ref={pedestalMaterial}
          color="#3c4148"
          roughness={0.9}
          emissive="#6b7683"
          emissiveIntensity={0}
        />
      </mesh>

      {status.kind === "loading" && <LoadingPulse target={pedestalMaterial} />}
      {status.kind === "ready" && (
        <group
          onPointerOver={(event) => {
            event.stopPropagation();
            setHovered(true);
          }}
          onPointerOut={() => setHovered(false)}
          onClick={(event) => {
            event.stopPropagation();
            onSelect(part.id);
          }}
        >
          <PartModel
            partId={part.id}
            decoded={status.decoded}
            hovered={hovered}
            selected={selected}
          />
        </group>
      )}
      {status.kind === "blank" && (
        <Html position={[0, 0.8, 0]} center>
          <div className="plate-hint">describe this part to design it</div>
        </Html>
      )}
      {status.kind === "error" && (
        <Html position={[0, 0.8, 0]} center>
          <div className="plate-hint plate-error">{status.message}</div>
        </Html>
      )}

      <Html position={[0, -0.45, PLATE_SIZE / 2]} center>
        <div className="plate-label">
          <strong>{part.part_name}</strong> [{part.part_type}]
          {dimensions !== null && <span> — {dimensions}</span>}
        </div>
      </Html>
    </group>
  );
}
