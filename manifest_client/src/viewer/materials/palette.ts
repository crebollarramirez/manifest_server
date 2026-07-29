/**
 * Stable part palette: each part hashes to a fixed color from `part_id`
 * alone, so colors never shuffle between sessions, list reorderings, or
 * re-exports. Muted engineering tones that read well against the dark stage
 * and keep geometry (not color) the focus.
 */

export const PART_PALETTE = [
  "#8496b0", // steel blue
  "#b09a84", // warm tan
  "#84b092", // sage
  "#a984b0", // muted violet
  "#b0ab84", // brass
  "#84a9b0", // teal
  "#b08484", // clay
  "#9284b0", // periwinkle
] as const;

/** FNV-1a over the part id — deterministic, well-spread for uuid strings. */
export function paletteIndex(partId: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < partId.length; i += 1) {
    hash ^= partId.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0) % PART_PALETTE.length;
}

export function paletteColor(partId: string): string {
  return PART_PALETTE[paletteIndex(partId)]!;
}
