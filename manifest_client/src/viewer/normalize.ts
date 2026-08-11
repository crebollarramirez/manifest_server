import type { Bounds } from "./decode/types";

/**
 * Per-plate normalization: every part is recentered and scaled to fit its
 * plate, with true dimensions surfaced as a label. A 5mm screw and a 300mm
 * bracket are equally legible. CAD/mesh sources are Z-up (CadQuery/Blender);
 * the viewer rotates the model group -90° about X, so the source Z extent
 * becomes the world-space height.
 */

export const PLATE_FIT_UNITS = 2;

export type PlateTransform = {
  /** Uniform scale applied to the model. */
  scale: number;
  /** Source-space center; negate (scaled) to recenter at the origin. */
  center: [number, number, number];
  /** Source-space extents (model units, mm by repo convention). */
  size: [number, number, number];
  /** World-space Y offset that seats the rotated model on the plate. */
  seatHeight: number;
};

export function plateTransform(
  bounds: Bounds,
  fit: number = PLATE_FIT_UNITS,
): PlateTransform {
  const size: [number, number, number] = [
    bounds.max[0] - bounds.min[0],
    bounds.max[1] - bounds.min[1],
    bounds.max[2] - bounds.min[2],
  ];
  const maxDimension = Math.max(size[0], size[1], size[2]);
  const scale = maxDimension > 0 ? fit / maxDimension : 1;
  const center: [number, number, number] = [
    (bounds.min[0] + bounds.max[0]) / 2,
    (bounds.min[1] + bounds.max[1]) / 2,
    (bounds.min[2] + bounds.max[2]) / 2,
  ];
  // After the -90° X rotation the source Z extent is vertical.
  return { scale, center, size, seatHeight: (size[2] * scale) / 2 };
}

export function formatDimensions(size: [number, number, number]): string {
  const fmt = (value: number): string =>
    value >= 100 ? value.toFixed(0) : value.toFixed(1);
  return `${fmt(size[0])} × ${fmt(size[1])} × ${fmt(size[2])} mm`;
}

/** Source geometry is mm by repo convention (CadQuery/Blender both export mm). */
export type DimensionUnit = "mm" | "cm" | "in";

export const DIMENSION_UNIT_OPTIONS: { label: string; value: DimensionUnit }[] = [
  { label: "mm", value: "mm" },
  { label: "cm", value: "cm" },
  { label: "in", value: "in" },
];

/** Converts a millimeter length for display in the given unit. */
export function convertLength(mm: number, unit: DimensionUnit): string {
  if (unit === "cm") return (mm / 10).toFixed(1);
  if (unit === "in") return (mm / 25.4).toFixed(2);
  return String(Math.round(mm));
}
