import { Cube, Ruler } from "@phosphor-icons/react";
import { IconButton, Select } from "../design-system";
import {
  convertLength,
  DIMENSION_UNIT_OPTIONS,
  plateTransform,
  type DimensionUnit,
} from "../viewer/normalize";
import type { DecodedGeometry } from "../viewer/decode/types";
import styles from "./DimensionsChip.module.css";

/**
 * Top-right dimensions readout — always visible, per the mockup. Shows the
 * focused part's REAL decoded bounds (not placeholder numbers), scaled by
 * the live SettingsPanel scale preview so the readout tracks what's on
 * screen. Empty when no part is focused or its geometry isn't decoded yet.
 */
export function DimensionsChip({
  decoded,
  scalePreview,
  unit,
  onUnitChange,
  rulerOn,
  onToggleRuler,
}: {
  decoded: DecodedGeometry | null;
  scalePreview: number;
  unit: DimensionUnit;
  onUnitChange: (unit: DimensionUnit) => void;
  rulerOn: boolean;
  onToggleRuler: () => void;
}) {
  const size = decoded ? plateTransform(decoded.bounds).size : null;
  const [width, length, height] = size
    ? [size[0] * scalePreview, size[1] * scalePreview, size[2] * scalePreview]
    : [null, null, null];

  return (
    <div className={`${styles.chip} glass--gloss`}>
      <div className={styles.header}>
        <Cube weight="bold" className={styles.headerIcon} />
        <span className={styles.headerLabel}>Dimensions</span>
        <div className={styles.spacer} />
        <IconButton
          variant={rulerOn ? "soft" : "ghost"}
          size="sm"
          label="Ruler — show measurements"
          onClick={onToggleRuler}
          disabled={!decoded}
        >
          <Ruler />
        </IconButton>
      </div>

      {size && width !== null && length !== null && height !== null ? (
        <div className={styles.row}>
          <div className={styles.field}>
            <span className={styles.fieldLabel}>Width</span>
            <span className={styles.fieldValue}>{convertLength(width, unit)}</span>
          </div>
          <div className={styles.divider} />
          <div className={styles.field}>
            <span className={styles.fieldLabel}>Length</span>
            <span className={styles.fieldValue}>{convertLength(length, unit)}</span>
          </div>
          <div className={styles.divider} />
          <div className={styles.field}>
            <span className={styles.fieldLabel}>Height</span>
            <span className={styles.fieldValue}>{convertLength(height, unit)}</span>
          </div>
          <div className={styles.spacer} />
          <div className={styles.unitSelect}>
            <Select
              options={DIMENSION_UNIT_OPTIONS}
              value={unit}
              onChange={(event) => onUnitChange(event.target.value as DimensionUnit)}
            />
          </div>
        </div>
      ) : (
        <div className={styles.empty}>Select a part to see its dimensions.</div>
      )}
    </div>
  );
}
