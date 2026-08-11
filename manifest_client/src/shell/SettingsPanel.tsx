import { useState } from "react";
import { CaretDown, Sliders } from "@phosphor-icons/react";
import { Select, Slider, Switch, Tabs } from "../design-system";
import {
  convertLength,
  DIMENSION_UNIT_OPTIONS,
  plateTransform,
  type DimensionUnit,
} from "../viewer/normalize";
import type { DecodedGeometry } from "../viewer/decode/types";
import styles from "./SettingsPanel.module.css";

/**
 * Bottom-right settings panel. Per the "visual-only, no backend claim"
 * decision: filament type, infill, layer height, and supports are local UI
 * state only — there's no print-ordering backend anywhere in cad-agent to
 * wire them to. Two exceptions that ARE genuinely functional: the color
 * swatches live-tint the focused part's real mesh, and the size slider
 * live-scales it (both via AppShell's scalePreview/colorPreview state,
 * consumed by PreviewLayer/PartModel) — honest, visual, no backend claim,
 * but not fake either.
 */

const FILAMENTS: { hex: string; name: string }[] = [
  { hex: "#F2F2F0", name: "White" },
  { hex: "#2B2B2E", name: "Black" },
  { hex: "#9F87E0", name: "Purple" },
  { hex: "#F2A868", name: "Orange" },
  { hex: "#B9AFD1", name: "Gray" },
];

const FILAMENT_TYPE_OPTIONS = [
  { label: "PLA — easy & eco", value: "PLA" },
  { label: "PETG — tough", value: "PETG" },
  { label: "TPU — bendy", value: "TPU" },
  { label: "Silk — shiny", value: "Silk" },
];

const LAYER_TABS = [
  { label: "Rough", value: "0.3" },
  { label: "Normal", value: "0.2" },
  { label: "Fine", value: "0.1" },
];

const SETTINGS_TABS = [
  { label: "Material", value: "material" },
  { label: "Size", value: "size" },
  { label: "Quality", value: "quality" },
];

export function SettingsPanel({
  open,
  onToggleOpen,
  filamentColor,
  onFilamentColorChange,
  scalePct,
  onScalePctChange,
  unit,
  onUnitChange,
  decoded,
}: {
  open: boolean;
  onToggleOpen: () => void;
  filamentColor: string | null;
  onFilamentColorChange: (color: string | null) => void;
  scalePct: number;
  onScalePctChange: (pct: number) => void;
  unit: DimensionUnit;
  onUnitChange: (unit: DimensionUnit) => void;
  decoded: DecodedGeometry | null;
}) {
  const [tab, setTab] = useState("material");
  const [filamentType, setFilamentType] = useState("PLA");
  const [infill, setInfill] = useState(15);
  const [layerHeight, setLayerHeight] = useState("0.2");
  const [supportsOn, setSupportsOn] = useState(true);

  const filamentName = FILAMENTS.find((f) => f.hex === filamentColor)?.name ?? "Default";
  const size = decoded ? plateTransform(decoded.bounds).size : null;
  const scale = scalePct / 100;

  return (
    <div className={styles.wrap} style={{ height: open ? "calc(100% - 240px)" : "56px" }}>
      <div className={`${styles.panel} glass--gloss ${open ? "" : styles.panelCollapsed}`}>
        {!open && (
          <button type="button" className={styles.collapsedRow} onClick={onToggleOpen}>
            <Sliders weight="bold" className={styles.collapsedIcon} />
            <span className={styles.collapsedLabel}>Print settings</span>
          </button>
        )}

        {open && (
          <>
            <div className={styles.titleRow}>
              <span className={styles.title}>Print settings</span>
              <button
                type="button"
                onClick={onToggleOpen}
                aria-label="Collapse settings"
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-tertiary)" }}
              >
                <CaretDown />
              </button>
            </div>

            <div className={styles.tabsRow}>
              <Tabs tabs={SETTINGS_TABS} active={tab} onChange={setTab} />
            </div>

            <div className={styles.body}>
              {tab === "material" && (
                <>
                  <div className={styles.sectionLabel}>Filament type</div>
                  <div className={styles.fieldGap}>
                    <Select
                      options={FILAMENT_TYPE_OPTIONS}
                      value={filamentType}
                      onChange={(event) => setFilamentType(event.target.value)}
                    />
                  </div>
                  <div className={styles.sectionLabel}>Color</div>
                  <div className={styles.swatchRow}>
                    {FILAMENTS.map((filament) => (
                      <button
                        key={filament.hex}
                        type="button"
                        aria-label={filament.name}
                        className={[
                          styles.swatch,
                          filamentColor === filament.hex ? styles.swatchActive : styles.swatchInactive,
                        ].join(" ")}
                        style={{ background: filament.hex }}
                        onClick={() =>
                          onFilamentColorChange(filamentColor === filament.hex ? null : filament.hex)
                        }
                      />
                    ))}
                  </div>
                  <div className={styles.swatchCaption}>
                    {filamentName} · {filamentType}
                  </div>
                </>
              )}

              {tab === "size" && (
                <>
                  <div className={styles.sectionLabel}>Size — {scalePct}%</div>
                  <div className={styles.fieldGap}>
                    <Slider
                      label="Size"
                      min={50}
                      max={150}
                      value={scalePct}
                      onChange={(event) => onScalePctChange(Number(event.target.value))}
                    />
                  </div>
                  <div className={styles.sectionLabel}>Measurement unit</div>
                  <div className={styles.fieldGap}>
                    <Select
                      options={DIMENSION_UNIT_OPTIONS}
                      value={unit}
                      onChange={(event) => onUnitChange(event.target.value as DimensionUnit)}
                    />
                  </div>
                  <div className={styles.dimsBox}>
                    <div className={styles.dimsField}>
                      <div className={styles.dimsLabel}>Width</div>
                      <div className={styles.dimsValue}>
                        {size ? convertLength(size[0] * scale, unit) : "—"}
                      </div>
                    </div>
                    <div className={styles.dimsField}>
                      <div className={styles.dimsLabel}>Length</div>
                      <div className={styles.dimsValue}>
                        {size ? convertLength(size[1] * scale, unit) : "—"}
                      </div>
                    </div>
                    <div className={styles.dimsField}>
                      <div className={styles.dimsLabel}>Height</div>
                      <div className={styles.dimsValue}>
                        {size ? convertLength(size[2] * scale, unit) : "—"}
                      </div>
                    </div>
                  </div>
                </>
              )}

              {tab === "quality" && (
                <>
                  <div className={styles.sectionLabel}>Fill — {infill}%</div>
                  <div className={styles.fieldGap}>
                    <Slider
                      label="Fill"
                      min={0}
                      max={100}
                      value={infill}
                      onChange={(event) => setInfill(Number(event.target.value))}
                    />
                  </div>
                  <div className={styles.sectionLabel}>Smoothness</div>
                  <div className={styles.fieldGap}>
                    <Tabs tabs={LAYER_TABS} active={layerHeight} onChange={setLayerHeight} />
                  </div>
                  <div className={styles.switchRow}>
                    <span className={styles.switchLabel}>Supports</span>
                    <Switch
                      label="Supports"
                      checked={supportsOn}
                      onChange={(event) => setSupportsOn(event.target.checked)}
                    />
                  </div>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
