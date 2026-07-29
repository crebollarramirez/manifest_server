import { Color, MeshStandardMaterial } from "three";
import { paletteColor } from "./palette";
import type { DecodedGeometry } from "../decode/types";

/**
 * Client-owned material system (Phase 2).
 *
 * - CAD STLs get a neutral PBR base tinted by the stable per-part palette.
 * - A GLB that ships its own material is NEVER overridden — authored
 *   pbrMetallicRoughness factors pass straight through. That is the path
 *   authored color arrives through if CAD generation ever gains appearance
 *   data (contract open question 2).
 * - Hover/selection is an emissive lift applied by MUTATING the existing
 *   material (never recreation) — the caller invalidates a demand-frame when
 *   `applyInteractionState` reports a change.
 *
 * Material lifecycle (creation/disposal) belongs to PartModel's crossfade
 * layers; these functions are deliberately pure so that stays in one place.
 */

export type InteractionState = {
  hovered: boolean;
  selected: boolean;
};

const HOVER_EMISSIVE = 0.18;
const SELECTED_EMISSIVE = 0.35;

export function createPartMaterial(
  partId: string,
  decoded: DecodedGeometry,
): MeshStandardMaterial {
  // Born in the canonical idle interaction state: three defaults
  // emissiveIntensity to 1 (masked by a black emissive), which would break
  // applyInteractionState's exact no-op detection.
  const authored = decoded.authoredMaterial;
  if (authored) {
    return new MeshStandardMaterial({
      // baseColorFactor is linear per glTF; numeric Color channels stay linear.
      color: new Color(
        authored.baseColorFactor[0],
        authored.baseColorFactor[1],
        authored.baseColorFactor[2],
      ),
      metalness: authored.metallicFactor,
      roughness: authored.roughnessFactor,
      emissiveIntensity: 0,
    });
  }
  return new MeshStandardMaterial({
    color: paletteColor(partId),
    metalness: 0.15,
    roughness: 0.7,
    // STL output is non-indexed with flat normals; render it faceted.
    flatShading: decoded.indices === null,
    emissiveIntensity: 0,
  });
}

/**
 * Mutates emissive state in place; returns true when something changed so the
 * caller can invalidate exactly one demand-frame. Selection outranks hover.
 * The emissive hue tracks the base color, so authored GLB colors glow as
 * themselves rather than being overridden.
 */
export function applyInteractionState(
  material: MeshStandardMaterial,
  state: InteractionState,
): boolean {
  const intensity = state.selected
    ? SELECTED_EMISSIVE
    : state.hovered
      ? HOVER_EMISSIVE
      : 0;
  if (material.emissiveIntensity === intensity) return false;
  material.emissive.copy(material.color);
  material.emissiveIntensity = intensity;
  return true;
}
