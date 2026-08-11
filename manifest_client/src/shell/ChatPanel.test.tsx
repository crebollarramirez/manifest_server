// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ChatPanel } from "./ChatPanel";
import { createFixtureClient } from "../api/fixtureClient";
import { FIXTURE_PROJECT_ID } from "../api/fixtureIds";

const LONG_TIMEOUT = { timeout: 5000 };

describe("ChatPanel", () => {
  it("shows Manny's persona (name, tagline) and an opening greeting", () => {
    render(
      <ChatPanel
        client={createFixtureClient()}
        projectId={FIXTURE_PROJECT_ID}
        focusedPartId={null}
        onPartUpdated={() => {}}
      />,
    );
    expect(screen.getByText("Manny")).toBeInTheDocument();
    expect(screen.getByText("your buddy")).toBeInTheDocument();
    expect(screen.getByText("Hi! What should we build today?")).toBeInTheDocument();
  });

  it(
    "sending a project-scoped message drives a real chat -> edit job -> completion, and reports the update",
    async () => {
      const onPartUpdated = vi.fn();
      render(
        <ChatPanel
          client={createFixtureClient()}
          projectId={FIXTURE_PROJECT_ID}
          focusedPartId={null}
          onPartUpdated={onPartUpdated}
        />,
      );
      fireEvent.change(screen.getByPlaceholderText("Ask Manny..."), {
        target: { value: "widen the bracket" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Send message" }));

      expect(await screen.findByText("widen the bracket")).toBeInTheDocument();
      await waitFor(
        () => expect(screen.getByText(/Done!/)).toBeInTheDocument(),
        LONG_TIMEOUT,
      );
      expect(onPartUpdated).toHaveBeenCalledOnce();
      expect(onPartUpdated.mock.calls[0]![1]).toEqual(expect.any(String));
    },
    6000,
  );

  it(
    "Enter key sends the message too",
    async () => {
      render(
        <ChatPanel
          client={createFixtureClient()}
          projectId={FIXTURE_PROJECT_ID}
          focusedPartId={null}
          onPartUpdated={() => {}}
        />,
      );
      const input = screen.getByPlaceholderText("Ask Manny...");
      fireEvent.change(input, { target: { value: "add a hole" } });
      fireEvent.keyDown(input, { key: "Enter" });
      expect(await screen.findByText("add a hole")).toBeInTheDocument();
    },
    6000,
  );

  it(
    "a suggestion chip sends its own text as a real user message",
    async () => {
      render(
        <ChatPanel
          client={createFixtureClient()}
          projectId={FIXTURE_PROJECT_ID}
          focusedPartId={null}
          onPartUpdated={() => {}}
        />,
      );
      fireEvent.click(screen.getByText("Make it bigger"));
      // Once sent, the suggestion becomes a user bubble and the chip row
      // disappears (only rendered after the latest message is assistant's).
      await waitFor(() => {
        expect(screen.queryByText("Add a fillet")).not.toBeInTheDocument();
      });
    },
    6000,
  );

  it("the mic toggle doesn't throw and can be clicked repeatedly", () => {
    render(
      <ChatPanel
        client={createFixtureClient()}
        projectId={FIXTURE_PROJECT_ID}
        focusedPartId={null}
        onPartUpdated={() => {}}
      />,
    );
    const mic = screen.getByRole("button", { name: "Speak instead of typing" });
    expect(() => {
      fireEvent.click(mic);
      fireEvent.click(mic);
    }).not.toThrow();
  });

  it("send is disabled with empty input", () => {
    render(
      <ChatPanel
        client={createFixtureClient()}
        projectId={FIXTURE_PROJECT_ID}
        focusedPartId={null}
        onPartUpdated={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });
});
