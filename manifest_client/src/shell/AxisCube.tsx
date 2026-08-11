import { useRef, useState } from "react";
import { applyDragDelta, clampPolar, type CameraApi, type OrbitAngles } from "../viewer/cameraApi";
import styles from "./AxisCube.module.css";

/**
 * CSS 3D orbit gizmo (pure DOM transforms, not WebGL — matches the
 * mockup's technique exactly). Genuinely functional: click a labeled face
 * to snap the real camera, drag anywhere on the cube to orbit it live.
 */
export function AxisCube({
  cameraApiRef,
  orbitAngles,
  left,
}: {
  cameraApiRef: React.MutableRefObject<CameraApi | null>;
  orbitAngles: OrbitAngles;
  left: number;
}) {
  const [dragging, setDragging] = useState(false);
  const dragState = useRef<{ startX: number; startY: number; startAngles: OrbitAngles } | null>(
    null,
  );

  const onPointerDown = (event: React.PointerEvent) => {
    dragState.current = {
      startX: event.clientX,
      startY: event.clientY,
      startAngles: orbitAngles,
    };
    setDragging(true);
    const onMove = (moveEvent: PointerEvent) => {
      const drag = dragState.current;
      if (!drag) return;
      const angles = applyDragDelta(
        drag.startAngles,
        moveEvent.clientX - drag.startX,
        moveEvent.clientY - drag.startY,
      );
      cameraApiRef.current?.setOrbitAngles(angles);
    };
    const onUp = () => {
      setDragging(false);
      dragState.current = null;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  // Visual mapping only (not a physical camera model): polar measured from
  // +Y (0 = top-down) maps to a level cube face at polar=90.
  const rotateX = clampPolar(orbitAngles.polar) - 90;
  const rotateY = -orbitAngles.azimuth;

  return (
    <div
      className={`${styles.wrap} glass--gloss ${dragging ? styles.wrapGrabbing : styles.wrapGrab}`}
      style={{ left }}
      onPointerDown={onPointerDown}
      title="Drag to orbit — click a face to snap"
    >
      <div
        className={`${styles.inner} ${dragging ? "" : styles.innerAnimated}`}
        style={{ transform: `rotateX(${rotateX}deg) rotateY(${rotateY}deg)` }}
      >
        <button
          type="button"
          className={`${styles.face} ${styles.faceFront}`}
          onClick={() => cameraApiRef.current?.snapTo("front")}
        >
          FRONT
        </button>
        <div className={`${styles.face} ${styles.faceBack}`}>BACK</div>
        <button
          type="button"
          className={`${styles.face} ${styles.faceRight}`}
          onClick={() => cameraApiRef.current?.snapTo("right")}
        >
          RIGHT
        </button>
        <div className={`${styles.face} ${styles.faceLeft}`}>LEFT</div>
        <button
          type="button"
          className={`${styles.face} ${styles.faceTop}`}
          onClick={() => cameraApiRef.current?.snapTo("top")}
        >
          TOP
        </button>
        <div className={`${styles.face} ${styles.faceBase}`}>BASE</div>
      </div>
    </div>
  );
}
