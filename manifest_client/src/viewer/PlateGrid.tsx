import { PartPlate, type PlateStatus } from "./PartPlate";
import type { PartRecord } from "../api/schemas";

export type Plate = { part: PartRecord; status: PlateStatus };

const SPACING = 4;

/** Deterministic grid layout — shared with PreviewLayer's camera framing so
 * "point the camera at part N" and "where PlateGrid actually put part N"
 * can never drift apart. */
export function gridLayoutPosition(
  index: number,
  total: number,
): [number, number, number] {
  const columns = Math.max(1, Math.ceil(Math.sqrt(total)));
  const rows = Math.ceil(total / columns);
  const column = index % columns;
  const row = Math.floor(index / columns);
  const x = (column - (columns - 1) / 2) * SPACING;
  const z = (row - (rows - 1) / 2) * SPACING;
  return [x, 0, z];
}

/**
 * Square-ish grid of plates, laid out from the part list alone (geometry
 * never affects layout). Windowed rendering beyond ~20 parts lands with
 * Phase 7; the layout math already supports it.
 *
 * Every plate stays mounted at all times, focused or not — focusing a part
 * only hides its siblings (visible=false) rather than unmounting them.
 * Unmounting would dispose and then immediately rebuild each plate's
 * BufferGeometry and re-upload it to the GPU; for a large mesh that round-
 * trip is the actual cost of "switching parts" — not anything about
 * rendering fewer triangles.
 *
 * visible=false alone does NOT stop a hidden plate from being raycast
 * (verified against three.js's own source — Raycaster's intersect() checks
 * only object.layers, never .visible). Every mounted plate's pointer
 * handlers get hit-tested on every pointer move regardless of visibility,
 * so `interactive={!hidden}` is passed through to PartPlate too, which
 * omits the handlers entirely for hidden plates — the only way to actually
 * exclude an object from R3F's raycasting.
 */
export function PlateGrid({
  plates,
  selectedPartId,
  onSelect,
  scaleOverride,
  colorOverride,
}: {
  plates: Plate[];
  selectedPartId: string | null;
  onSelect: (partId: string) => void;
  /** Live preview overrides from SettingsPanel — applied only to the focused plate. */
  scaleOverride?: number;
  colorOverride?: string | null;
}) {
  return (
    <group>
      {plates.map((plate, index) => {
        const position = gridLayoutPosition(index, plates.length);
        const isFocused = plate.part.id === selectedPartId;
        const hidden = selectedPartId !== null && !isFocused;
        return (
          <group key={plate.part.id} visible={!hidden}>
            <PartPlate
              part={plate.part}
              status={plate.status}
              position={position}
              selected={isFocused}
              onSelect={onSelect}
              scaleOverride={isFocused ? scaleOverride : undefined}
              colorOverride={isFocused ? colorOverride : undefined}
              interactive={!hidden}
            />
          </group>
        );
      })}
    </group>
  );
}
