import { useState } from "react";
import { Export, Printer, Moon, Smiley, Sun } from "@phosphor-icons/react";
import { Button, IconButton, useTheme } from "../design-system";
import type { FixtureCadAgentClient } from "../api/fixtureClient";
import type { PartRecord } from "../api/schemas";
import styles from "./TopBar.module.css";

/**
 * Top navigation bar. Export is genuinely wired to the fixture client's
 * exportPart() action; Order has no backend anywhere in cad-agent (no
 * print-ordering concept exists), so it renders disabled with a clear
 * "coming soon" label rather than implying it does something.
 */
export function TopBar({
  projectName,
  focusedPart,
  client,
}: {
  projectName: string;
  focusedPart: PartRecord | null;
  client: FixtureCadAgentClient;
}) {
  const { resolvedTheme, setPreference } = useTheme();
  const [exportState, setExportState] = useState<"idle" | "queuing" | "queued">("idle");

  const handleExport = async () => {
    if (!focusedPart || exportState === "queuing") return;
    setExportState("queuing");
    try {
      await client.exportPart(focusedPart.id);
      setExportState("queued");
    } finally {
      setTimeout(() => setExportState("idle"), 2000);
    }
  };

  return (
    <div className={`${styles.bar} glass-bar`}>
      <div className={styles.brand}>
        <div className={styles.logo} />
        <span className={styles.wordmark}>Manifest</span>
      </div>

      <div className={styles.projectPill}>{projectName}</div>

      <div className={styles.actions}>
        <Button
          variant="secondary"
          size="md"
          onClick={handleExport}
          disabled={!focusedPart || exportState === "queuing"}
          title={focusedPart ? undefined : "Select a part to export"}
        >
          <Export weight="bold" style={{ marginRight: 6 }} />
          {exportState === "idle" && "Export"}
          {exportState === "queuing" && "Queuing…"}
          {exportState === "queued" && "Queued"}
        </Button>
        <Button variant="primary" size="md" disabled title="Print ordering isn't available yet">
          <Printer weight="bold" style={{ marginRight: 6 }} />
          Order
        </Button>
        <IconButton
          variant="ghost"
          size="md"
          label={resolvedTheme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          onClick={() => setPreference(resolvedTheme === "dark" ? "light" : "dark")}
        >
          {resolvedTheme === "dark" ? <Sun /> : <Moon />}
        </IconButton>
        <div className={styles.avatar}>
          <Smiley weight="fill" />
        </div>
      </div>
    </div>
  );
}
