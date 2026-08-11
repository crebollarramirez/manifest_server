// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ThemeProvider } from "../design-system";
import { createFixtureClient } from "../api/fixtureClient";
import { FIXTURE_CAD_PART_ID, FIXTURE_PROJECT_ID } from "../api/fixtureIds";
import { TopBar } from "./TopBar";

const CAD_PART = {
  id: FIXTURE_CAD_PART_ID,
  project_id: FIXTURE_PROJECT_ID,
  part_name: "bracket",
  part_type: "cad" as const,
};

function renderTopBar(focusedPart: typeof CAD_PART | null) {
  const client = createFixtureClient();
  render(
    <ThemeProvider>
      <TopBar projectName="fixture-project" focusedPart={focusedPart} client={client} />
    </ThemeProvider>,
  );
  return client;
}

describe("TopBar", () => {
  it("shows the project name and wordmark", () => {
    renderTopBar(null);
    expect(screen.getByText("Manifest")).toBeInTheDocument();
    expect(screen.getByText("fixture-project")).toBeInTheDocument();
  });

  it("disables Export when no part is focused", () => {
    renderTopBar(null);
    expect(screen.getByRole("button", { name: /Export/ })).toBeDisabled();
  });

  it("Order is always disabled — no backend print-ordering exists", () => {
    renderTopBar(CAD_PART);
    expect(screen.getByRole("button", { name: /Order/ })).toBeDisabled();
  });

  it("Export calls the real exportPart action when a part is focused", async () => {
    const client = renderTopBar(CAD_PART);
    const exportSpy = vi.spyOn(client, "exportPart");
    fireEvent.click(screen.getByRole("button", { name: /Export/ }));
    await waitFor(() => expect(exportSpy).toHaveBeenCalledWith(FIXTURE_CAD_PART_ID));
  });

  it("theme toggle flips the ThemeProvider preference", () => {
    renderTopBar(null);
    const toggle = screen.getByRole("button", { name: "Switch to dark mode" });
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeInTheDocument();
  });
});
