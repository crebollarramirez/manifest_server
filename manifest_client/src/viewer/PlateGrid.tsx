import { PartPlate, type PlateStatus } from "./PartPlate";
import type { PartRecord } from "../api/schemas";

export type Plate = { part: PartRecord; status: PlateStatus };

const SPACING = 4;

/**
 * Square-ish grid of plates, laid out from the part list alone (geometry
 * never affects layout). Windowed rendering beyond ~20 parts lands with
 * Phase 7; the layout math already supports it.
 */
export function PlateGrid({
  plates,
  selectedPartId,
  onSelect,
}: {
  plates: Plate[];
  selectedPartId: string | null;
  onSelect: (partId: string) => void;
}) {
  const columns = Math.max(1, Math.ceil(Math.sqrt(plates.length)));
  const rows = Math.ceil(plates.length / columns);
  return (
    <group>
      {plates.map((plate, index) => {
        const column = index % columns;
        const row = Math.floor(index / columns);
        const x = (column - (columns - 1) / 2) * SPACING;
        const z = (row - (rows - 1) / 2) * SPACING;
        return (
          <PartPlate
            key={plate.part.id}
            part={plate.part}
            status={plate.status}
            position={[x, 0, z]}
            selected={plate.part.id === selectedPartId}
            onSelect={onSelect}
          />
        );
      })}
    </group>
  );
}
