import { useEffect, useRef, useState } from "react";
import { useFrame } from "@react-three/fiber";
import { Grid, Html, useCursor } from "@react-three/drei";
import type { MeshStandardMaterial } from "three";
import { PartModel } from "./PartModel";
import { formatDimensions, plateTransform } from "./normalize";
import { useTheme } from "../design-system";
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

/**
 * Build-plate pedestal colors, mirroring the reference screens' inline
 * values (design_handoff_manifest_tokens/reference-screens/) — three.js
 * materials can't consume CSS custom properties directly, so these hex
 * constants are hand-matched to tokens.css's purple-700/800 (light) and
 * white-on-black (dark), same precedent as viewer/materials/palette.ts.
 */
const PEDESTAL_TONE = {
  light: { base: "#6B58A8", baseOpacity: 0.1, grid: "#4A3B78", gridOpacity: 0.14 },
  dark: { base: "#ffffff", baseOpacity: 0.04, grid: "#ffffff", gridOpacity: 0.08 },
} as const;

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
  scaleOverride,
  colorOverride,
  interactive = true,
}: {
  part: PartRecord;
  status: PlateStatus;
  position: [number, number, number];
  selected: boolean;
  onSelect: (partId: string) => void;
  /** Live preview overrides from SettingsPanel — visual-only, no backend effect. */
  scaleOverride?: number;
  colorOverride?: string | null;
  /**
   * False for plates hidden behind another focused part. `visible={false}`
   * alone does NOT exclude an object from raycasting — confirmed directly
   * in three.js's source (Raycaster.js's `intersect()` checks only
   * `object.layers`, never `object.visible`). Every pointer move raycasts
   * against every mesh with pointer handlers attached, hidden or not, so a
   * hidden 503k-triangle mesh was still being fully hit-tested on every
   * hover anywhere in the scene. The fix is to not attach handlers at all
   * when a plate isn't interactive — R3F only raycasts objects that
   * registered a handler, so this fully excludes it, not just fast-paths it.
   */
  interactive?: boolean;
}) {
  const pedestalMaterial = useRef<MeshStandardMaterial>(null);
  const [hovered, setHovered] = useState(false);
  useCursor(hovered);
  // Without handlers attached, a plate that goes non-interactive mid-hover
  // never gets the onPointerOut that would normally clear this — reset
  // explicitly so its hover glow doesn't stay stuck on while hidden.
  useEffect(() => {
    if (!interactive) setHovered(false);
  }, [interactive]);
  const { resolvedTheme } = useTheme();
  const tone = PEDESTAL_TONE[resolvedTheme];
  const dimensions =
    status.kind === "ready"
      ? formatDimensions(plateTransform(status.decoded.bounds).size)
      : null;

  return (
    <group position={position}>
      {/* Build-plate pedestal: tinted base + drei's Grid for the mockup's
          repeating grid-line pattern (see PEDESTAL_TONE above for the
          token-matched colors).

          One mesh, not two: an earlier version had a second, separate
          invisible box (as LoadingPulse's material target) whose top face
          sat at the exact same Y as this plane — coplanar surfaces
          z-fight, which read as "twinkling" while orbiting. LoadingPulse
          now targets this plane's own material directly, and polygonOffset
          gives the Grid a reliable depth bias over it regardless of camera
          distance/angle (a fixed Y gap alone isn't reliable at all zoom
          levels, since depth-buffer precision drops with distance). */}
      <mesh position={[0, -0.09, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[PLATE_SIZE, PLATE_SIZE]} />
        <meshStandardMaterial
          ref={pedestalMaterial}
          color={tone.base}
          transparent
          opacity={tone.baseOpacity}
          roughness={0.9}
          emissive={tone.base}
          emissiveIntensity={0}
          toneMapped={false}
          polygonOffset
          polygonOffsetFactor={1}
          polygonOffsetUnits={1}
        />
      </mesh>
      <Grid
        position={[0, -0.08, 0]}
        args={[PLATE_SIZE, PLATE_SIZE]}
        cellSize={PLATE_SIZE / 10}
        cellThickness={0.6}
        cellColor={tone.grid}
        sectionThickness={0}
        fadeDistance={PLATE_SIZE * 1.4}
        fadeStrength={1}
        followCamera={false}
        infiniteGrid={false}
        // opacity isn't a Grid prop; cellColor's own alpha carries it via toneMapped-off blending.
      />

      {status.kind === "loading" && <LoadingPulse target={pedestalMaterial} />}
      {status.kind === "ready" && (
        <group
          onPointerOver={
            interactive
              ? (event) => {
                  event.stopPropagation();
                  setHovered(true);
                }
              : undefined
          }
          onPointerOut={interactive ? () => setHovered(false) : undefined}
          onClick={
            interactive
              ? (event) => {
                  event.stopPropagation();
                  onSelect(part.id);
                }
              : undefined
          }
        >
          <PartModel
            partId={part.id}
            decoded={status.decoded}
            hovered={hovered}
            selected={selected}
            scaleOverride={scaleOverride}
            colorOverride={colorOverride}
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
