/**
 * Imperative camera control surface, shared between DOM chrome that lives
 * OUTSIDE the R3F <Canvas> (CenterToolbar's zoom buttons, AxisCube's
 * click-to-snap) and the OrbitControls instance that lives inside it.
 *
 * Plain mutable ref object rather than a new state-management dependency:
 * AppShell creates one `useRef<CameraApi | null>(null)`, passes it into
 * PreviewLayer, and a small component *inside* the Canvas (which is the
 * only place useThree()/OrbitControls are reachable) populates it.
 * DOM chrome calls `cameraApiRef.current?.zoomIn()` etc — a no-op via
 * optional chaining before the Canvas has mounted.
 */

export type OrbitAngles = { azimuth: number; polar: number };

export type CameraApi = {
  zoomIn: () => void;
  zoomOut: () => void;
  /** Snap the camera to a named preset (mirrors the mockup's axis-cube faces). */
  snapTo: (preset: "front" | "top" | "right" | "iso") => void;
  /** Current orbit angles in degrees, for the AxisCube to mirror. */
  getOrbitAngles: () => OrbitAngles;
  /** Drag-to-orbit: set angles directly (clamped), used by AxisCube dragging. */
  setOrbitAngles: (angles: OrbitAngles) => void;
};

export const ORBIT_PRESETS: Record<"front" | "top" | "right" | "iso", OrbitAngles> = {
  front: { azimuth: 0, polar: 82 },
  top: { azimuth: 0, polar: 2 },
  right: { azimuth: 90, polar: 82 },
  iso: { azimuth: -35, polar: 55 },
};

export function clampPolar(polar: number): number {
  return Math.min(178, Math.max(2, polar));
}

const DRAG_SENSITIVITY = 0.6;

/**
 * Pure drag-to-orbit math: pointer delta (px) from drag start -> new angles.
 *
 * Trackball convention (Blender/Fusion 360/Onshape), not a camera-pan
 * convention: dragging right should make the model appear to turn right.
 * Since increasing theta (azimuth) in three.js's Spherical orbits the
 * CAMERA toward its own right — which makes the model appear to turn the
 * opposite way from the viewer's fixed screen perspective — a rightward
 * drag must DECREASE azimuth (orbit the camera left) to make the object
 * follow the drag direction, not fight it.
 */
export function applyDragDelta(
  start: OrbitAngles,
  deltaX: number,
  deltaY: number,
): OrbitAngles {
  return {
    azimuth: start.azimuth - deltaX * DRAG_SENSITIVITY,
    polar: clampPolar(start.polar - deltaY * DRAG_SENSITIVITY),
  };
}
