import { ProjectView } from "./viewer/ProjectView";
import { FIXTURE_PROJECT_NAME } from "./api/fixtureIds";

/** Phase 1 shell: full-viewport viewer over the fixture project. */
export default function App() {
  return (
    <div style={{ position: "relative", height: "100%" }}>
      <header
        style={{
          position: "absolute",
          zIndex: 1,
          padding: "0.75rem 1rem",
          fontFamily: "system-ui, sans-serif",
          color: "#e8eaed",
          pointerEvents: "none",
        }}
      >
        <strong>Manifest</strong> — {FIXTURE_PROJECT_NAME}
      </header>
      <ProjectView />
    </div>
  );
}
