import { useState } from "react";
import { CaretDown, CaretUp, Cube, Package, Stack } from "@phosphor-icons/react";
import type { PartRecord } from "../api/schemas";
import styles from "./PlateSelector.module.css";

/**
 * Top-center plate selector. Per the "no fabricated grouping" decision, this
 * lists real parts one-for-one rather than the mockup's invented "Body &
 * legs"/"Head & spikes" groups — "All parts" shows the full grid, selecting
 * one part focuses just its plate.
 */
export function PlateSelector({
  parts,
  focusedPartId,
  onFocus,
}: {
  parts: PartRecord[];
  focusedPartId: string | null;
  onFocus: (partId: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const focusedPart = parts.find((part) => part.id === focusedPartId) ?? null;
  const label = focusedPart ? focusedPart.part_name : "All parts";

  return (
    <div className={styles.wrap} onMouseLeave={() => setOpen(false)}>
      <div
        className={`${styles.trigger} glass--gloss glass-pill`}
        onClick={() => setOpen((current) => !current)}
        role="button"
        tabIndex={0}
        aria-expanded={open}
      >
        <Stack weight="bold" />
        <span className={styles.triggerLabel}>{label}</span>
        {open ? (
          <CaretUp className={styles.triggerCaret} />
        ) : (
          <CaretDown className={styles.triggerCaret} />
        )}
      </div>

      {open && (
        <div className={`${styles.menu} glass--gloss`}>
          <button
            type="button"
            className={[styles.item, focusedPartId === null && styles.itemActive]
              .filter(Boolean)
              .join(" ")}
            onClick={() => {
              onFocus(null);
              setOpen(false);
            }}
          >
            <Stack weight="bold" className={styles.itemIcon} />
            <span className={styles.itemLabel}>All parts</span>
            <span className={styles.itemMeta}>{parts.length}</span>
          </button>
          {parts.map((part) => (
            <button
              key={part.id}
              type="button"
              className={[styles.item, focusedPartId === part.id && styles.itemActive]
                .filter(Boolean)
                .join(" ")}
              onClick={() => {
                onFocus(part.id);
                setOpen(false);
              }}
            >
              {part.part_type === "cad" ? (
                <Cube weight="bold" className={styles.itemIcon} />
              ) : (
                <Package weight="bold" className={styles.itemIcon} />
              )}
              <span className={styles.itemLabel}>{part.part_name}</span>
              <span className={styles.itemMeta}>{part.part_type}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
