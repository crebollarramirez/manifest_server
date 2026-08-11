import { ArrowClockwise, ArrowCounterClockwise, GitCommit, Minus, Plus } from "@phosphor-icons/react";
import { IconButton } from "../design-system";
import type { CameraApi } from "../viewer/cameraApi";
import styles from "./CenterToolbar.module.css";

/**
 * Bottom-center floating toolbar. Zoom is genuinely wired to the real
 * camera. Undo/redo and the version indicator have no backing store
 * anywhere in this app (no edit-history stack exists yet), so they render
 * honestly disabled rather than pretending to work — per the "make whatever
 * can be functional functional" rule, that cuts both ways.
 */
export function CenterToolbar({
  cameraApiRef,
}: {
  cameraApiRef: React.MutableRefObject<CameraApi | null>;
}) {
  return (
    <div className={styles.wrap}>
      <div className={`${styles.bar} glass--gloss glass-pill`}>
        <IconButton variant="ghost" size="md" label="Undo" disabled title="No edit history yet">
          <ArrowCounterClockwise />
        </IconButton>
        <IconButton variant="ghost" size="md" label="Redo" disabled title="No edit history yet">
          <ArrowClockwise />
        </IconButton>
        <div className={styles.versionPill} title="No edit history yet">
          <GitCommit weight="bold" className={styles.versionIcon} />
          <span className={styles.versionLabel}>v1</span>
        </div>
        <div className={styles.divider} />
        <IconButton
          variant="ghost"
          size="md"
          label="Zoom in"
          onClick={() => cameraApiRef.current?.zoomIn()}
        >
          <Plus />
        </IconButton>
        <IconButton
          variant="ghost"
          size="md"
          label="Zoom out"
          onClick={() => cameraApiRef.current?.zoomOut()}
        >
          <Minus />
        </IconButton>
      </div>
    </div>
  );
}
