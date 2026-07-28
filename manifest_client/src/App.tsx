import { useEffect, useState } from "react";
import { createFixtureClient } from "./api/fixtureClient";
import { FIXTURE_PROJECT_ID } from "./api/fixtureIds";
import type { PartRecord } from "./api/schemas";

const client = createFixtureClient();

/**
 * Phase 0 shell: proves the scaffold runs and the trust boundary works
 * end-to-end (fixture client -> Zod parse -> React default-escaped render).
 * The viewer replaces this in Phase 1.
 */
export default function App() {
  const [parts, setParts] = useState<PartRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    client.listParts(FIXTURE_PROJECT_ID).then(
      (response) => setParts(response.parts),
      (cause: unknown) =>
        setError(cause instanceof Error ? cause.message : "Unknown error"),
    );
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
      <h1>Manifest</h1>
      <p>Phase 0 scaffold — fixture-backed part list.</p>
      {error !== null && <p role="alert">{error}</p>}
      {parts === null && error === null && <p>Loading…</p>}
      {parts !== null && (
        <ul>
          {parts.map((part) => (
            <li key={part.id}>
              {part.part_name} <code>[{part.part_type}]</code>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
