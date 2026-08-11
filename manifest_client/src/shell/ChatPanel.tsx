import { useState } from "react";
import { CaretDown, CaretUp, Microphone, PaperPlaneRight } from "@phosphor-icons/react";
import { IconButton } from "../design-system";
import { MAX_HISTORY_MESSAGES, type ChatMessage } from "../api/client";
import type { FixtureCadAgentClient } from "../api/fixtureClient";
import styles from "./ChatPanel.module.css";

/**
 * Bottom-left chat, genuinely wired to the fixture client's chat() /
 * getEditJob() / getExportJob() flow — sending a message really submits a
 * CAD edit or mesh update and, once it completes, calls onPartUpdated so
 * AppShell can refresh that part's geometry via useProjectData.refreshPart.
 * "Manny" persona kept exactly (name, avatar, tagline) per the mockup;
 * message content itself is real, not scripted demo copy.
 */

type Bubble = { role: "user" | "assistant"; content: string; error?: boolean };

const SUGGESTIONS = ["Make it bigger", "Add a fillet"];

type Stage = 1 | 2 | 3;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function ChatPanel({
  client,
  projectId,
  focusedPartId,
  onPartUpdated,
}: {
  client: FixtureCadAgentClient;
  projectId: string;
  focusedPartId: string | null;
  onPartUpdated: (partId: string, exportJobId: string) => void;
}) {
  const [stage, setStage] = useState<Stage>(2);
  const [hovered, setHovered] = useState(false);
  const [bubbles, setBubbles] = useState<Bubble[]>([
    { role: "assistant", content: "Hi! What should we build today?" },
  ]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [micOn, setMicOn] = useState(false);

  const submit = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setInput("");
    setSending(true);
    const history: ChatMessage[] = [
      ...bubbles
        .filter((b) => !b.error)
        .map((b) => ({ role: b.role, content: b.content })),
      { role: "user" as const, content: trimmed },
    ].slice(-MAX_HISTORY_MESSAGES);
    setBubbles((current) => [...current, { role: "user", content: trimmed }]);

    try {
      const response = await client.chat({
        projectId,
        partId: focusedPartId ?? undefined,
        clientRequestId: crypto.randomUUID(),
        messages: history,
      });

      if (response.job_type === "export_mesh") {
        setBubbles((current) => [
          ...current,
          { role: "assistant", content: "Updating and exporting…" },
        ]);
        let job = await client.getExportJob(response.job_id);
        for (let poll = 0; poll < 40 && job.status !== "completed" && job.status !== "failed"; poll += 1) {
          await sleep(150);
          job = await client.getExportJob(response.job_id);
        }
        if (job.status === "completed") {
          setBubbles((current) => [...current, { role: "assistant", content: "Done!" }]);
          onPartUpdated(response.part_id, response.job_id);
        } else {
          setBubbles((current) => [
            ...current,
            { role: "assistant", error: true, content: job.job.error_message ?? "That export failed." },
          ]);
        }
      } else {
        setBubbles((current) => [...current, { role: "assistant", content: "Working on it…" }]);
        let job = await client.getEditJob(response.job_id);
        for (let poll = 0; poll < 60 && job.status !== "completed" && job.status !== "failed" && job.status !== "cancelled"; poll += 1) {
          await sleep(150);
          job = await client.getEditJob(response.job_id);
        }
        if (job.status === "completed" && job.job.resolved_part_id && job.job.export_job_id) {
          setBubbles((current) => [
            ...current,
            { role: "assistant", content: "Done! Updated the part — exporting now." },
          ]);
          onPartUpdated(job.job.resolved_part_id, job.job.export_job_id);
        } else if (job.status === "completed") {
          setBubbles((current) => [
            ...current,
            { role: "assistant", content: "Done, but no export was queued yet." },
          ]);
        } else {
          setBubbles((current) => [
            ...current,
            {
              role: "assistant",
              error: true,
              content: job.job.error_message ?? "That edit didn't complete.",
            },
          ]);
        }
      }
    } catch (error) {
      setBubbles((current) => [
        ...current,
        {
          role: "assistant",
          error: true,
          content: error instanceof Error ? error.message : "Something went wrong.",
        },
      ]);
    } finally {
      setSending(false);
    }
  };

  const height = stage === 1 ? "74px" : stage === 2 ? "calc(50% - 12px)" : "calc(100% - 48px)";

  return (
    <div
      className={styles.wrap}
      style={{ height }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div className={[styles.expandBtn, hovered && styles.expandBtnVisible].filter(Boolean).join(" ")}>
        {stage > 2 && (
          <button type="button" className={styles.caretBtn} onClick={() => setStage(2)} aria-label="Collapse chat">
            <CaretDown size={13} />
          </button>
        )}
        {stage < 3 && (
          <button type="button" className={styles.caretBtn} onClick={() => setStage(3)} aria-label="Expand chat">
            <CaretUp size={13} />
          </button>
        )}
      </div>

      <div className={`${styles.container} glass--gloss ${stage === 1 ? styles.containerBar : ""}`}>
        {stage >= 2 && (
          <>
            <div className={styles.header}>
              <div className={styles.avatar}>
                <span className={styles.eye} style={{ left: 11 }} />
                <span className={styles.eye} style={{ left: 22 }} />
              </div>
              <div className={styles.headerText}>
                <div className={styles.headerName}>Manny</div>
                <div className={styles.headerTagline}>your buddy</div>
              </div>
            </div>

            <div className={styles.messages}>
              {bubbles.map((bubble, index) => (
                <div
                  key={index}
                  className={[styles.bubbleRow, bubble.role === "user" && styles.bubbleRowUser]
                    .filter(Boolean)
                    .join(" ")}
                >
                  <div
                    className={[
                      styles.bubble,
                      bubble.error
                        ? styles.bubbleError
                        : bubble.role === "user"
                          ? styles.bubbleUser
                          : styles.bubbleAssistant,
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    {bubble.content}
                  </div>
                </div>
              ))}
              {!sending && bubbles.length > 0 && bubbles[bubbles.length - 1]!.role === "assistant" && (
                <div className={styles.suggestions}>
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      className={styles.bubbleAssistant}
                      style={{ border: "none", cursor: "pointer", borderRadius: "var(--radius-pill)" }}
                      onClick={() => submit(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        <div className={styles.inputRow}>
          <input
            className={styles.input}
            placeholder="Ask Manny..."
            value={input}
            disabled={sending}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void submit(input);
            }}
          />
          <IconButton
            variant={micOn ? "dangerSoft" : "ghost"}
            size="md"
            label="Speak instead of typing"
            onClick={() => setMicOn((current) => !current)}
          >
            <Microphone />
          </IconButton>
          <IconButton
            variant="soft"
            size="md"
            label="Send message"
            disabled={sending || !input.trim()}
            onClick={() => void submit(input)}
          >
            <PaperPlaneRight />
          </IconButton>
        </div>
      </div>
    </div>
  );
}
