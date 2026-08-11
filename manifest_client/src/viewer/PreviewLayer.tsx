import { useEffect, useMemo, useRef } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import { MathUtils, Spherical, Vector3 } from "three";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { gridLayoutPosition, PlateGrid, type Plate } from "./PlateGrid";
import { ORBIT_PRESETS, clampPolar, type CameraApi, type OrbitAngles } from "./cameraApi";

/**
 * The 3D stage: canvas-field backdrop (tokens.css), one shared WebGL
 * context. Every plate stays mounted at all times (see PlateGrid) —
 * "focusing" a part hides its siblings and pans the camera to it, rather
 * than swapping which components are mounted, so switching focus never
 * disposes-and-rebuilds a BufferGeometry that was already GPU-resident.
 * Camera is exposed imperatively via cameraApiRef so DOM chrome outside the
 * Canvas (CenterToolbar, AxisCube) can drive it.
 */

const ZOOM_FACTOR = 0.88;
const MIN_DISTANCE = 4;
const MAX_DISTANCE = 40;

function CameraController({
  cameraApiRef,
  onOrbitChange,
  focusPosition,
}: {
  cameraApiRef: React.MutableRefObject<CameraApi | null>;
  onOrbitChange?: (angles: OrbitAngles) => void;
  /** World position to pan the camera's orbit target to — null recenters on the origin. */
  focusPosition: [number, number, number] | null;
}) {
  const controlsRef = useRef<OrbitControlsImpl>(null);
  const { camera, invalidate } = useThree();

  useEffect(() => {
    const readAngles = (): OrbitAngles => {
      const controls = controlsRef.current;
      if (!controls) return { azimuth: 0, polar: 55 };
      return {
        azimuth: MathUtils.radToDeg(controls.getAzimuthalAngle()),
        polar: MathUtils.radToDeg(controls.getPolarAngle()),
      };
    };

    const applySpherical = (radiusScale: number | null, angles: OrbitAngles | null) => {
      const controls = controlsRef.current;
      if (!controls) return;
      const offset = new Vector3().subVectors(camera.position, controls.target);
      const spherical = new Spherical().setFromVector3(offset);
      if (radiusScale !== null) {
        spherical.radius = Math.min(
          MAX_DISTANCE,
          Math.max(MIN_DISTANCE, spherical.radius * radiusScale),
        );
      }
      if (angles !== null) {
        spherical.theta = MathUtils.degToRad(angles.azimuth);
        spherical.phi = MathUtils.degToRad(clampPolar(angles.polar));
      }
      offset.setFromSpherical(spherical);
      camera.position.copy(controls.target).add(offset);
      camera.lookAt(controls.target);
      controls.update();
      invalidate();
      onOrbitChange?.(readAngles());
    };

    cameraApiRef.current = {
      zoomIn: () => applySpherical(ZOOM_FACTOR, null),
      zoomOut: () => applySpherical(1 / ZOOM_FACTOR, null),
      snapTo: (preset) => applySpherical(null, ORBIT_PRESETS[preset]),
      getOrbitAngles: readAngles,
      setOrbitAngles: (angles) => applySpherical(null, angles),
    };

    return () => {
      cameraApiRef.current = null;
    };
  }, [camera, invalidate, cameraApiRef, onOrbitChange]);

  // Pan the orbit target to the focused plate (or back to the origin),
  // preserving the current viewing angle/distance — a slide, not a reset.
  useEffect(() => {
    const controls = controlsRef.current;
    if (!controls) return;
    const target = focusPosition ?? [0, 0, 0];
    const offset = new Vector3().subVectors(camera.position, controls.target);
    controls.target.set(target[0], target[1], target[2]);
    camera.position.copy(controls.target).add(offset);
    camera.lookAt(controls.target);
    controls.update();
    invalidate();
  }, [focusPosition, camera, invalidate]);

  return (
    <OrbitControls
      ref={controlsRef}
      makeDefault
      minDistance={MIN_DISTANCE}
      maxDistance={MAX_DISTANCE}
      onChange={() => onOrbitChange?.(
        controlsRef.current
          ? {
              azimuth: MathUtils.radToDeg(controlsRef.current.getAzimuthalAngle()),
              polar: MathUtils.radToDeg(controlsRef.current.getPolarAngle()),
            }
          : { azimuth: 0, polar: 55 },
      )}
    />
  );
}

export function PreviewLayer({
  plates,
  focusedPartId,
  onSelectPart,
  cameraApiRef,
  onOrbitChange,
  scalePreview,
  colorPreview,
}: {
  plates: Plate[];
  focusedPartId: string | null;
  onSelectPart: (partId: string | null) => void;
  cameraApiRef: React.MutableRefObject<CameraApi | null>;
  onOrbitChange?: (angles: OrbitAngles) => void;
  /** Live preview overrides from SettingsPanel, applied to the focused plate only. */
  scalePreview?: number;
  colorPreview?: string | null;
}) {
  const focusedIndex = focusedPartId
    ? plates.findIndex((plate) => plate.part.id === focusedPartId)
    : -1;
  // Memoized on primitives (index, count), not recomputed as a fresh array
  // reference on every unrelated render — CameraController's pan effect
  // depends on this by reference, and that effect itself indirectly causes
  // re-renders (via onOrbitChange), so an unmemoized array here would give
  // the effect a "changed" dependency every render and let it re-fire
  // continuously off its own feedback loop.
  const focusPosition = useMemo(
    () => (focusedIndex >= 0 ? gridLayoutPosition(focusedIndex, plates.length) : null),
    [focusedIndex, plates.length],
  );

  // No wrapping div of its own — AppShell owns the position:relative,
  // canvas-field-classed container so floating chrome (PlateSelector,
  // ChatPanel, ...) shares the identical positioning context as siblings,
  // not nested descendants.
  return (
    <Canvas
      frameloop="demand"
      dpr={[1, 2]}
      gl={{ alpha: true }}
      camera={{ position: [7, 6, 9], fov: 40 }}
      onPointerMissed={() => onSelectPart(null)}
      style={{ width: "100%", height: "100%" }}
    >
      <ambientLight intensity={0.6} />
      <directionalLight position={[6, 10, 4]} intensity={1.5} />
      <directionalLight position={[-6, 4, -6]} intensity={0.45} />
      <PlateGrid
        plates={plates}
        selectedPartId={focusedPartId}
        onSelect={onSelectPart}
        scaleOverride={scalePreview}
        colorOverride={colorPreview}
      />
      <CameraController
        cameraApiRef={cameraApiRef}
        onOrbitChange={onOrbitChange}
        focusPosition={focusPosition}
      />
    </Canvas>
  );
}
