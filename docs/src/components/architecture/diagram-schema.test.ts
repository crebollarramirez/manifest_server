import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import test from "node:test";

import {
  ArchitectureDocumentSchema,
  type ArchitectureDocument,
} from "./diagram-schema.ts";

const validDocument: ArchitectureDocument = {
  id: "indexing-domain",
  title: "Indexing Domain",
  summary: "An example architecture document.",
  scope: "domain",
  direction: "RIGHT",
  nodes: [
    {
      id: "edge-api",
      label: "Edge API",
      description: "Validates indexing requests.",
      type: "api",
      icon: "api",
      position: { column: 0, row: 0 },
      mobileOrder: 1,
    },
    {
      id: "index-jobs",
      label: "Index Jobs",
      description: "Owns indexing job lifecycle state.",
      type: "queue",
      href: "/infrastructure/index-jobs/",
      position: { column: 1, row: 0 },
      mobileOrder: 2,
    },
  ],
  edges: [
    {
      id: "enqueue-job",
      source: "edge-api",
      target: "index-jobs",
      label: "validated job",
      href: "#edge-api-requests",
      importance: "important",
      request: {
        label: "validated index job",
        type: "enqueue_index",
        exampleBody: '{"project_id":"example"}',
      },
      protocol: "queue",
      direction: "one-way",
      flow: "asynchronous",
    },
  ],
};

function cloneValidDocument(): Record<string, unknown> {
  return structuredClone(validDocument);
}

function issuePaths(input: unknown): string[] {
  const result = ArchitectureDocumentSchema.safeParse(input);
  assert.equal(result.success, false);

  return result.error.issues.map((issue) => issue.path.join("."));
}

test("accepts a valid architecture document", () => {
  const result = ArchitectureDocumentSchema.safeParse(validDocument);

  assert.equal(result.success, true);
  if (result.success) {
    assert.deepEqual(result.data, validDocument);
  }
});

test("requires valid, unique desktop and mobile positions", () => {
  const malformed = cloneValidDocument();
  const nodes = malformed.nodes as Array<Record<string, unknown>>;
  nodes[0].position = { column: -1, row: 0 };
  assert.ok(issuePaths(malformed).includes("nodes.0.position.column"));

  const duplicateGrid = cloneValidDocument();
  (duplicateGrid.nodes as Array<Record<string, unknown>>)[1].position = {
    column: 0,
    row: 0,
  };
  assert.ok(issuePaths(duplicateGrid).includes("nodes.1.position"));

  const missingMobileOrder = cloneValidDocument();
  delete (missingMobileOrder.nodes as Array<Record<string, unknown>>)[0].mobileOrder;
  assert.ok(issuePaths(missingMobileOrder).includes("nodes.0.mobileOrder"));

  const duplicateMobileOrder = cloneValidDocument();
  (duplicateMobileOrder.nodes as Array<Record<string, unknown>>)[1].mobileOrder = 1;
  assert.ok(issuePaths(duplicateMobileOrder).includes("nodes.1.mobileOrder"));
});

test("rejects duplicate node ids", () => {
  const input = cloneValidDocument();
  const nodes = input.nodes as Array<Record<string, unknown>>;
  nodes.push({
    id: "edge-api",
    label: "Duplicate",
    description: "Uses an id that is already present.",
    type: "service",
    position: { column: 2, row: 0 },
    mobileOrder: 3,
  });

  assert.ok(issuePaths(input).includes("nodes.2.id"));
});

test("rejects duplicate edge ids", () => {
  const input = cloneValidDocument();
  const edges = input.edges as Array<Record<string, unknown>>;
  edges.push({
    id: "enqueue-job",
    source: "index-jobs",
    target: "edge-api",
  });

  assert.ok(issuePaths(input).includes("edges.1.id"));
});

test("rejects dangling edge sources and targets", () => {
  const input = cloneValidDocument();
  const edges = input.edges as Array<Record<string, unknown>>;
  edges[0].source = "missing-source";
  edges[0].target = "missing-target";

  const paths = issuePaths(input);
  assert.ok(paths.includes("edges.0.source"));
  assert.ok(paths.includes("edges.0.target"));
});

test("rejects a linked foundation node", () => {
  const input = cloneValidDocument();
  const nodes = input.nodes as Array<Record<string, unknown>>;
  nodes[1].foundation = true;

  assert.ok(issuePaths(input).includes("nodes.1.href"));
});

test("rejects unknown document, node, and edge keys", () => {
  const documentInput = cloneValidDocument();
  documentInput.extra = true;
  assert.equal(ArchitectureDocumentSchema.safeParse(documentInput).success, false);

  const nodeInput = cloneValidDocument();
  (nodeInput.nodes as Array<Record<string, unknown>>)[0].extra = true;
  assert.equal(ArchitectureDocumentSchema.safeParse(nodeInput).success, false);

  const edgeInput = cloneValidDocument();
  (edgeInput.edges as Array<Record<string, unknown>>)[0].extra = true;
  assert.equal(ArchitectureDocumentSchema.safeParse(edgeInput).success, false);
});

test("rejects malformed enum values", () => {
  const scopeInput = cloneValidDocument();
  scopeInput.scope = "application";
  assert.equal(ArchitectureDocumentSchema.safeParse(scopeInput).success, false);

  const nodeInput = cloneValidDocument();
  (nodeInput.nodes as Array<Record<string, unknown>>)[0].type = "controller";
  assert.equal(ArchitectureDocumentSchema.safeParse(nodeInput).success, false);

  const artifactNodeInput = cloneValidDocument();
  (artifactNodeInput.nodes as Array<Record<string, unknown>>)[0].type = "artifact";
  assert.equal(ArchitectureDocumentSchema.safeParse(artifactNodeInput).success, false);

  const edgeInput = cloneValidDocument();
  (edgeInput.edges as Array<Record<string, unknown>>)[0].protocol = "grpc";
  assert.equal(ArchitectureDocumentSchema.safeParse(edgeInput).success, false);

  const importanceInput = cloneValidDocument();
  (importanceInput.edges as Array<Record<string, unknown>>)[0].importance = "normal";
  assert.equal(ArchitectureDocumentSchema.safeParse(importanceInput).success, false);

  const requestInput = cloneValidDocument();
  (requestInput.edges as Array<Record<string, unknown>>)[0].request = {
    label: "enqueue index job",
    type: "enqueue_index",
    exampleBody: "",
  };
  assert.equal(ArchitectureDocumentSchema.safeParse(requestInput).success, false);

  const iconInput = cloneValidDocument();
  (iconInput.nodes as Array<Record<string, unknown>>)[0].icon = "magic";
  assert.equal(ArchitectureDocumentSchema.safeParse(iconInput).success, false);

  const hrefInput = cloneValidDocument();
  (hrefInput.edges as Array<Record<string, unknown>>)[0].href = "   ";
  assert.equal(ArchitectureDocumentSchema.safeParse(hrefInput).success, false);
});

test("allows stored contents only on durable storage nodes", () => {
  const storageContents = cloneValidDocument();
  const storage = (storageContents.nodes as Array<Record<string, unknown>>)[1];
  storage.type = "storage";
  storage.contents = ["semantic_index.json"];
  assert.equal(ArchitectureDocumentSchema.safeParse(storageContents).success, true);

  const transientContents = cloneValidDocument();
  (transientContents.nodes as Array<Record<string, unknown>>)[0].contents = ["EditPlan"];
  assert.ok(issuePaths(transientContents).includes("nodes.0.contents"));

  const duplicateContents = cloneValidDocument();
  const duplicateStorage = (duplicateContents.nodes as Array<Record<string, unknown>>)[1];
  duplicateStorage.type = "storage";
  duplicateStorage.contents = ["model.py", "model.py"];
  assert.ok(issuePaths(duplicateContents).includes("nodes.1.contents"));
});

test("requires separate request and response messages for two-way edges", () => {
  const missingMessages = cloneValidDocument();
  const edge = (missingMessages.edges as Array<Record<string, unknown>>)[0];
  edge.direction = "two-way";
  assert.ok(issuePaths(missingMessages).includes("edges.0.response"));

  edge.response = {
    label: "queued job reference",
    type: "enqueue_index response",
    exampleBody: '{"job_id":"index-job-123"}',
  };
  assert.equal(ArchitectureDocumentSchema.safeParse(missingMessages).success, true);
});

test("rejects response metadata on one-way edges", () => {
  const input = cloneValidDocument();
  (input.edges as Array<Record<string, unknown>>)[0].response = {
    label: "unexpected response",
    type: "response",
    exampleBody: "{}",
  };
  assert.ok(issuePaths(input).includes("edges.0.response"));
});

test("rejects blank identifiers without silently trimming them", () => {
  const input = cloneValidDocument();
  input.id = "   ";

  assert.ok(issuePaths(input).includes("id"));
});

test("accepts strict node details and rejects unknown detail fields", () => {
  const input = cloneValidDocument();
  (input.nodes as Array<Record<string, unknown>>)[0].details = {
    responsibility: "Validate an inbound request.",
    trigger: ["An HTTP request arrives."],
    inputs: [{ name: "request", description: "Validated JSON." }],
    operations: {
      timeout: "30 seconds.",
      observability: ["Request duration."],
    },
    evidence: [{
      path: "services/cad_agent/src/cad-actions.service.ts",
      symbol: "handler",
      kind: "source",
    }],
  };
  assert.equal(ArchitectureDocumentSchema.safeParse(input).success, true);

  ((input.nodes as Array<Record<string, unknown>>)[0].details as Record<string, unknown>).invented = true;
  assert.ok(issuePaths(input).includes("nodes.0.details"));
});

test("requires repository-relative evidence paths without traversal or URLs", () => {
  for (const path of [
    "/absolute/source.ts",
    "../outside/source.ts",
    "src/../secret.ts",
    "https://example.com/source.ts",
    "src//source.ts",
  ]) {
    const input = cloneValidDocument();
    (input.nodes as Array<Record<string, unknown>>)[0].details = {
      evidence: [{ path, kind: "source" }],
    };
    assert.ok(issuePaths(input).includes("nodes.0.details.evidence.0.path"));
  }
});

test("validates node variants against their semantic node type", () => {
  const valid = cloneValidDocument();
  const node = (valid.nodes as Array<Record<string, unknown>>)[0];
  node.type = "function";
  node.variant = "gate";
  assert.equal(ArchitectureDocumentSchema.safeParse(valid).success, true);

  const incompatible = cloneValidDocument();
  (incompatible.nodes as Array<Record<string, unknown>>)[0].variant = "sandbox";
  assert.ok(issuePaths(incompatible).includes("nodes.0.variant"));
});

test("accepts nested boundaries and rejects dangling members or parents", () => {
  const valid = cloneValidDocument();
  valid.boundaries = [
    {
      id: "service-boundary",
      label: "Service",
      kind: "service",
      nodeIds: ["edge-api", "index-jobs"],
    },
    {
      id: "worker-boundary",
      label: "Worker",
      kind: "worker",
      nodeIds: ["index-jobs"],
      parentId: "service-boundary",
    },
  ];
  assert.equal(ArchitectureDocumentSchema.safeParse(valid).success, true);

  const danglingMember = structuredClone(valid);
  (danglingMember.boundaries as Array<Record<string, unknown>>)[0].nodeIds = ["missing"];
  assert.ok(issuePaths(danglingMember).includes("boundaries.0.nodeIds.0"));

  const danglingParent = structuredClone(valid);
  (danglingParent.boundaries as Array<Record<string, unknown>>)[1].parentId = "missing";
  assert.ok(issuePaths(danglingParent).includes("boundaries.1.parentId"));
});

test("validates strict semantic edge details", () => {
  const valid = cloneValidDocument();
  (valid.edges as Array<Record<string, unknown>>)[0].details = {
    summary: "The API persists a validated job request.",
    operation: "enqueue_index",
    durability: "The row is durable after commit.",
    evidence: [{
      path: "services/cad_agent/src/cad-actions.service.ts",
      kind: "source",
    }],
  };
  assert.equal(ArchitectureDocumentSchema.safeParse(valid).success, true);

  ((valid.edges as Array<Record<string, unknown>>)[0].details as Record<string, unknown>).timing = "soon";
  assert.ok(issuePaths(valid).includes("edges.0.details"));
});

test("all authored architecture documents satisfy the shared contract", () => {
  const directory = new URL("../../data/architecture/", import.meta.url);
  const files = readdirSync(directory).filter((file) => file.endsWith(".json"));
  assert.ok(files.length > 0);

  for (const file of files) {
    const input = JSON.parse(readFileSync(new URL(file, directory), "utf8"));
    const result = ArchitectureDocumentSchema.safeParse(input);
    assert.equal(
      result.success,
      true,
      result.success ? undefined : `${file}: ${JSON.stringify(result.error.issues)}`,
    );
  }
});
