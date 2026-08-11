import { convertLength, plateTransform, type DimensionUnit } from "../viewer/normalize";
import type { DecodedGeometry } from "../viewer/decode/types";
import styles from "./RulerOverlay.module.css";

/**
 * Illustrative measurement overlay (matches the mockup: a styled callout,
 * not a true 3D-projected dimension line) — but the numbers themselves are
 * the focused part's real decoded bounds, not placeholders.
 */
export function RulerOverlay({
  decoded,
  unit,
}: {
  decoded: DecodedGeometry;
  unit: DimensionUnit;
}) {
  const [width, length, height] = plateTransform(decoded.bounds).size;

  return (
    <div className={styles.overlay}>
      <div className={`${styles.line} ${styles.lineWidth}`} />
      <div className={`${styles.label} ${styles.labelWidth}`}>
        W {convertLength(width, unit)} {unit}
      </div>
      <div className={`${styles.line} ${styles.lineHeight}`} />
      <div className={`${styles.label} ${styles.labelHeight}`}>
        H {convertLength(height, unit)} {unit}
      </div>
      <div className={`${styles.line} ${styles.lineLength}`} />
      <div className={`${styles.label} ${styles.labelLength}`}>
        L {convertLength(length, unit)} {unit}
      </div>
    </div>
  );
}
