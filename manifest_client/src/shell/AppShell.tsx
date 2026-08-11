import { useMemo, useRef, useState } from "react";
import { PreviewLayer } from "../viewer/PreviewLayer";
import { useProjectData } from "../viewer/useProjectData";
import { createFixtureClient } from "../api/fixtureClient";
import { FIXTURE_PROJECT_ID, FIXTURE_PROJECT_NAME } from "../api/fixtureIds";
import type { CameraApi, OrbitAngles } from "../viewer/cameraApi";
import type { DimensionUnit } from "../viewer/normalize";
import { TopBar } from "./TopBar";
import { PlateSelector } from "./PlateSelector";
import { DimensionsChip } from "./DimensionsChip";
import { CenterToolbar } from "./CenterToolbar";
import { ChatPanel } from "./ChatPanel";
import { AxisCube } from "./AxisCube";
import { SettingsPanel } from "./SettingsPanel";
import { RulerOverlay } from "./RulerOverlay";
import styles from "./AppShell.module.css";

const PAD = 24;
const CHAT_WIDTH = 320;

/**
 * The main interface — replaces the Phase 1 minimal shell entirely. Wires
 * every piece of shell chrome to real data: useProjectData is the single
 * fetch for the whole app (PlateSelector, DimensionsChip, ChatPanel, and
 * PreviewLayer's 3D canvas all read the same `plates`); the camera API ref
 * connects CenterToolbar's zoom and AxisCube's snap/drag to the actual
 * OrbitControls instance living inside the Canvas.
 */
export function AppShell() {
  const client = useMemo(() => createFixtureClient(), []);
  const { plates, refreshPart } = useProjectData(client, FIXTURE_PROJECT_ID);

  const [focusedPartId, setFocusedPartId] = useState<string | null>(null);
  const [unit, setUnit] = useState<DimensionUnit>("mm");
  const [rulerOn, setRulerOn] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(true);
  const [scalePct, setScalePct] = useState(100);
  const [filamentColor, setFilamentColor] = useState<string | null>(null);
  const [orbitAngles, setOrbitAngles] = useState<OrbitAngles>({ azimuth: -35, polar: 55 });
  const cameraApiRef = useRef<CameraApi | null>(null);

  const focusedPlate = focusedPartId
    ? (plates.find((plate) => plate.part.id === focusedPartId) ?? null)
    : null;
  const focusedDecoded =
    focusedPlate && focusedPlate.status.kind === "ready" ? focusedPlate.status.decoded : null;
  const scalePreview = scalePct / 100;

  return (
    <div className={styles.app}>
      <TopBar
        projectName={FIXTURE_PROJECT_NAME}
        focusedPart={focusedPlate?.part ?? null}
        client={client}
      />

      <div className={`${styles.previewLayer} canvas-field`}>
        <PreviewLayer
          plates={plates}
          focusedPartId={focusedPartId}
          onSelectPart={setFocusedPartId}
          cameraApiRef={cameraApiRef}
          onOrbitChange={setOrbitAngles}
          scalePreview={scalePreview}
          colorPreview={filamentColor}
        />

        <PlateSelector
          parts={plates.map((plate) => plate.part)}
          focusedPartId={focusedPartId}
          onFocus={setFocusedPartId}
        />

        {rulerOn && focusedDecoded && <RulerOverlay decoded={focusedDecoded} unit={unit} />}

        <DimensionsChip
          decoded={focusedDecoded}
          scalePreview={scalePreview}
          unit={unit}
          onUnitChange={setUnit}
          rulerOn={rulerOn}
          onToggleRuler={() => setRulerOn((current) => !current)}
        />

        <CenterToolbar cameraApiRef={cameraApiRef} />

        <ChatPanel
          client={client}
          projectId={FIXTURE_PROJECT_ID}
          focusedPartId={focusedPartId}
          onPartUpdated={(partId, exportJobId) => {
            void refreshPart(partId, exportJobId);
          }}
        />

        <AxisCube cameraApiRef={cameraApiRef} orbitAngles={orbitAngles} left={PAD + CHAT_WIDTH + 12} />

        <SettingsPanel
          open={settingsOpen}
          onToggleOpen={() => setSettingsOpen((current) => !current)}
          filamentColor={filamentColor}
          onFilamentColorChange={setFilamentColor}
          scalePct={scalePct}
          onScalePctChange={setScalePct}
          unit={unit}
          onUnitChange={setUnit}
          decoded={focusedDecoded}
        />
      </div>
    </div>
  );
}
