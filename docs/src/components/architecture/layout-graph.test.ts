import assert from "node:assert/strict";
import test from "node:test";

import {
  createArchitectureBoundaryNodes,
  createArchitectureFlowNodes,
  layoutArchitectureDocument,
} from "./layout-graph.ts";
import type { ArchitectureDocument } from "./diagram-schema.ts";

const manualGridDocument: ArchitectureDocument = {
  id: "manual-grid",
  title: "Manual grid",
  summary: "A reusable positioned architecture test.",
  scope: "system",
  nodes: [
    {
      id: "producer",
      label: "Producer",
      description: "Sends a request forward.",
      type: "api",
      position: { column: 0, row: 0 },
      mobileOrder: 1,
    },
    {
      id: "worker",
      label: "Worker",
      description: "Returns its status through an exterior lane.",
      type: "worker",
      position: { column: 1, row: 0 },
      mobileOrder: 2,
    },
  ],
  edges: [
    {
      id: "forward",
      source: "producer",
      target: "worker",
      label: "request",
      protocol: "http",
      direction: "one-way",
      flow: "synchronous",
    },
    {
      id: "feedback",
      source: "worker",
      target: "producer",
      label: "result",
      protocol: "event",
      direction: "one-way",
      flow: "asynchronous",
    },
  ],
};

test("uses authored positions and assigns live cardinal routes without fixed paths", async () => {
  const graph = await layoutArchitectureDocument(manualGridDocument);
  const producer = graph.nodes.find((node) => node.id === "producer");
  const worker = graph.nodes.find((node) => node.id === "worker");
  const forward = graph.edges.find((edge) => edge.id === "forward");
  const feedback = graph.edges.find((edge) => edge.id === "feedback");

  assert.deepEqual(producer?.position, { x: 54, y: 54 });
  assert.deepEqual(worker?.position, { x: 304, y: 54 });
  assert.ok(graph.width > 480);
  assert.ok(graph.height >= 230);
  assert.equal(forward?.sourceHandle, "source-right");
  assert.equal(forward?.targetHandle, "target-left");
  assert.equal(forward?.data?.routeSlot, 0);
  assert.equal(feedback?.sourceHandle, "source-bottom");
  assert.equal(feedback?.targetHandle, "target-bottom");
  assert.equal(feedback?.data?.routeSlot, 1);
  assert.equal(feedback?.data?.marker, "←");
  assert.equal(
    Object.hasOwn(forward?.data ?? {}, "path"),
    false,
    "runtime edges must not carry precomputed connector coordinates",
  );
});

test("derives non-interactive group bounds from live child positions", () => {
  const grouped: ArchitectureDocument = {
    ...manualGridDocument,
    boundaries: [{
      id: "worker-boundary",
      label: "Worker",
      kind: "worker",
      nodeIds: ["producer", "worker"],
    }],
  };
  const nodes = createArchitectureFlowNodes(grouped);
  const [boundary] = createArchitectureBoundaryNodes(grouped, nodes);

  assert.equal(boundary.type, "architectureBoundary");
  assert.equal(boundary.draggable, false);
  assert.ok((boundary.width ?? 0) > 430);

  const moved = nodes.map((node) => node.id === "worker"
    ? { ...node, position: { x: 600, y: 200 } }
    : node);
  const [movedBoundary] = createArchitectureBoundaryNodes(grouped, moved);
  assert.ok((movedBoundary.width ?? 0) > (boundary.width ?? 0));
  assert.ok((movedBoundary.height ?? 0) > (boundary.height ?? 0));
});
