import { useEffect, useRef, useState } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import {
  BufferAttribute,
  BufferGeometry,
  type MeshStandardMaterial,
} from "three";
import { Crossfade } from "./gpu/crossfade";
import {
  applyInteractionState,
  createPartMaterial,
  type InteractionState,
} from "./materials/useMaterial";
import { plateTransform } from "./normalize";
import type { DecodedGeometry } from "./decode/types";

/**
 * One part's renderable model. Geometry arrives pre-decoded (worker output);
 * construction here is cheap: attribute wrap + single GPU upload.
 *
 * Seamlessness: when `decoded` changes (post-edit swap), the new geometry
 * crossfades in over ~300ms and the old layer's GPU resources are explicitly
 * disposed once the fade completes. Materials go transparent only for the
 * duration of the fade.
 */

type Layer = {
  key: number;
  decoded: DecodedGeometry;
  geometry: BufferGeometry;
  material: MeshStandardMaterial;
};

let nextLayerKey = 1;

function buildLayer(partId: string, decoded: DecodedGeometry): Layer {
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(decoded.positions, 3));
  geometry.setAttribute("normal", new BufferAttribute(decoded.normals, 3));
  if (decoded.indices) geometry.setIndex(new BufferAttribute(decoded.indices, 1));
  // Material system lives in materials/useMaterial.ts: palette for STL,
  // authored GLB factors passed through untouched.
  const material = createPartMaterial(partId, decoded);
  nextLayerKey += 1;
  return { key: nextLayerKey, decoded, geometry, material };
}

function disposeLayer(layer: Layer): void {
  layer.geometry.dispose();
  layer.material.dispose();
}

export function PartModel({
  partId,
  decoded,
  hovered,
  selected,
}: {
  partId: string;
  decoded: DecodedGeometry;
  hovered: boolean;
  selected: boolean;
}) {
  const invalidate = useThree((state) => state.invalidate);
  const fade = useRef(new Crossfade()).current;
  const interaction = useRef<InteractionState>({ hovered, selected });
  interaction.current = { hovered, selected };
  const [layers, setLayers] = useState<{ current: Layer; previous: Layer | null }>(
    () => ({ current: buildLayer(partId, decoded), previous: null }),
  );
  const layersRef = useRef(layers);
  layersRef.current = layers;

  // Geometry swap: new layer fades in over the previous one.
  useEffect(() => {
    if (layersRef.current.current.decoded === decoded) return;
    setLayers((state) => {
      if (state.previous) disposeLayer(state.previous); // interrupted fade
      const current = buildLayer(partId, decoded);
      applyInteractionState(current.material, interaction.current);
      current.material.transparent = true;
      current.material.opacity = 0;
      state.current.material.transparent = true;
      fade.begin(performance.now());
      return { current, previous: state.current };
    });
    invalidate();
  }, [decoded, partId, fade, invalidate]);

  // Hover/selection: mutate the existing materials, never recreate; spend
  // exactly one demand-frame when something actually changed.
  useEffect(() => {
    const { current, previous } = layersRef.current;
    let changed = applyInteractionState(current.material, { hovered, selected });
    if (previous) {
      changed =
        applyInteractionState(previous.material, { hovered, selected }) || changed;
    }
    if (changed) invalidate();
  }, [hovered, selected, layers, invalidate]);

  // Dispose everything on unmount.
  useEffect(() => {
    return () => {
      disposeLayer(layersRef.current.current);
      if (layersRef.current.previous) disposeLayer(layersRef.current.previous);
    };
  }, []);

  useFrame((state) => {
    const { current, previous } = layersRef.current;
    if (!previous) return;
    const now = performance.now();
    const progress = fade.progress(now);
    current.material.opacity = progress;
    previous.material.opacity = 1 - progress;
    if (fade.done(now)) {
      disposeLayer(previous);
      current.material.transparent = false;
      current.material.opacity = 1;
      fade.reset();
      setLayers({ current, previous: null });
    }
    state.invalidate(); // keep frames coming until the fade settles
  });

  return (
    <group>
      {[layers.previous, layers.current].map((layer) => {
        if (!layer) return null;
        const transform = plateTransform(layer.decoded.bounds);
        return (
          <group
            key={layer.key}
            position={[0, transform.seatHeight, 0]}
            rotation={[-Math.PI / 2, 0, 0]}
          >
            <mesh
              geometry={layer.geometry}
              material={layer.material}
              scale={transform.scale}
              position={[
                -transform.center[0] * transform.scale,
                -transform.center[1] * transform.scale,
                -transform.center[2] * transform.scale,
              ]}
            />
          </group>
        );
      })}
    </group>
  );
}
