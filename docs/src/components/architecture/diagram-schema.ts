import { z } from "zod";

export const architectureScopes = [
  "system",
  "domain",
  "service",
  "foundation",
] as const;

export const architectureDirections = ["RIGHT", "DOWN"] as const;

export const architectureNodeTypes = [
  "layer",
  "client",
  "api",
  "function",
  "service",
  "worker",
  "database",
  "queue",
  "storage",
  "external",
] as const;

export const architectureIconNames = [
  "layers",
  "terminal",
  "api",
  "process",
  "service",
  "worker",
  "database",
  "queue",
  "object-storage",
  "file-json",
  "source-code",
  "ast",
  "search",
  "editor",
  "external",
] as const;

export const architectureProtocols = [
  "http",
  "event",
  "queue",
  "database",
  "storage",
  "file",
  "internal",
] as const;

export const architectureEdgeDirections = ["one-way", "two-way"] as const;
export const architectureFlows = ["synchronous", "asynchronous"] as const;
export const architectureEdgeImportance = ["important"] as const;
export const architectureNodeVariants = [
  "operation",
  "transform",
  "gate",
  "decision",
  "completion",
  "polling",
  "sandbox",
  "scheduled",
] as const;
export const architectureBoundaryKinds = [
  "service",
  "worker",
  "runtime",
  "trust",
  "transaction",
] as const;
export const architectureEvidenceKinds = [
  "source",
  "migration",
  "schema",
  "test",
  "configuration",
] as const;

const requiredText = (fieldName: string) =>
  z.string().refine((value) => value.trim().length > 0, {
    message: `${fieldName} cannot be blank.`,
  });

export const ArchitectureScopeSchema = z.enum(architectureScopes);
export const ArchitectureDirectionSchema = z.enum(architectureDirections);
export const ArchitectureNodeTypeSchema = z.enum(architectureNodeTypes);
export const ArchitectureIconNameSchema = z.enum(architectureIconNames);
export const ArchitectureProtocolSchema = z.enum(architectureProtocols);
export const ArchitectureEdgeDirectionSchema = z.enum(
  architectureEdgeDirections,
);
export const ArchitectureFlowSchema = z.enum(architectureFlows);
export const ArchitectureEdgeImportanceSchema = z.enum(architectureEdgeImportance);
export const ArchitectureNodeVariantSchema = z.enum(architectureNodeVariants);
export const ArchitectureBoundaryKindSchema = z.enum(architectureBoundaryKinds);
export const ArchitectureEvidenceKindSchema = z.enum(architectureEvidenceKinds);

export const ArchitectureNodePositionSchema = z.strictObject({
  column: z.number().int().min(0),
  row: z.number().int().min(0),
});

export const ArchitectureEdgeMessageSchema = z.strictObject({
  label: requiredText("Message label"),
  type: requiredText("Message type"),
  exampleBody: requiredText("Message example body"),
});

const nonEmptyTextArray = (fieldName: string) =>
  z.array(requiredText(fieldName)).min(1);

export const ArchitectureEvidenceSchema = z.strictObject({
  path: requiredText("Evidence path").refine((path) => {
    if (path.startsWith("/") || /^[a-z][a-z\d+.-]*:/i.test(path)) return false;
    const parts = path.split("/");
    return !parts.some((part) => part === "" || part === "." || part === "..");
  }, {
    message: "Evidence path must be a repository-relative path without traversal or a URL.",
  }),
  symbol: requiredText("Evidence symbol").optional(),
  kind: ArchitectureEvidenceKindSchema,
});

export const ArchitectureContractItemSchema = z.strictObject({
  name: requiredText("Contract item name"),
  description: requiredText("Contract item description"),
});

export const ArchitectureFailureConditionSchema = z.strictObject({
  condition: requiredText("Failure condition"),
  outcome: requiredText("Failure outcome"),
  continuesTo: requiredText("Failure continuation").optional(),
});

export const ArchitectureOperationsSchema = z.strictObject({
  timeout: requiredText("Operation timeout").optional(),
  concurrency: requiredText("Operation concurrency").optional(),
  retry: requiredText("Operation retry behavior").optional(),
  observability: nonEmptyTextArray("Observability item").optional(),
  controls: nonEmptyTextArray("Runtime control").optional(),
});

export const ArchitectureNodeDetailsSchema = z.strictObject({
  responsibility: requiredText("Node responsibility").optional(),
  ownedBy: requiredText("Node owner").optional(),
  runsIn: requiredText("Node runtime").optional(),
  trigger: nonEmptyTextArray("Node trigger").optional(),
  preconditions: nonEmptyTextArray("Node precondition").optional(),
  inputs: z.array(ArchitectureContractItemSchema).min(1).optional(),
  outputs: z.array(ArchitectureContractItemSchema).min(1).optional(),
  durableReads: nonEmptyTextArray("Durable read").optional(),
  durableWrites: nonEmptyTextArray("Durable write").optional(),
  downstream: nonEmptyTextArray("Downstream destination").optional(),
  steps: nonEmptyTextArray("Node step").max(12).optional(),
  decisionPoints: nonEmptyTextArray("Decision point").optional(),
  shortCircuit: requiredText("Short-circuit behavior").optional(),
  successCondition: requiredText("Success condition").optional(),
  failureConditions: z.array(ArchitectureFailureConditionSchema).min(1).optional(),
  guarantees: nonEmptyTextArray("Node guarantee").optional(),
  operations: ArchitectureOperationsSchema.optional(),
  evidence: z.array(ArchitectureEvidenceSchema).min(1).max(8).optional(),
});

export const ArchitectureNodeSchema = z
  .strictObject({
    id: requiredText("Node id"),
    label: requiredText("Node label"),
    description: requiredText("Node description"),
    type: ArchitectureNodeTypeSchema,
    variant: ArchitectureNodeVariantSchema.optional(),
    icon: ArchitectureIconNameSchema.optional(),
    contents: z.array(requiredText("Storage content")).min(1).max(6).optional(),
    position: ArchitectureNodePositionSchema,
    mobileOrder: z.number().int().min(1),
    href: requiredText("Node href").optional(),
    foundation: z.boolean().optional(),
    details: ArchitectureNodeDetailsSchema.optional(),
  })
  .superRefine((node, context) => {
    if (node.foundation === true && node.href !== undefined) {
      context.addIssue({
        code: "custom",
        message: "Foundation nodes cannot link to deeper architecture pages.",
        path: ["href"],
      });
    }

    if (node.contents !== undefined && node.type !== "storage") {
      context.addIssue({
        code: "custom",
        message: "Only storage nodes can declare stored contents.",
        path: ["contents"],
      });
    }

    if (node.contents && new Set(node.contents).size !== node.contents.length) {
      context.addIssue({
        code: "custom",
        message: "Storage contents must not repeat within a node.",
        path: ["contents"],
      });
    }

    const compatibleVariants: Partial<
      Record<z.infer<typeof ArchitectureNodeTypeSchema>, readonly string[]>
    > = {
      function: ["operation", "transform", "gate", "decision", "completion"],
      worker: ["polling", "sandbox", "scheduled"],
    };
    if (
      node.variant !== undefined
      && !compatibleVariants[node.type]?.includes(node.variant)
    ) {
      context.addIssue({
        code: "custom",
        message: `Variant "${node.variant}" is not compatible with node type "${node.type}".`,
        path: ["variant"],
      });
    }
  });

export const ArchitectureEdgeDetailsSchema = z.strictObject({
  summary: requiredText("Edge summary").optional(),
  preconditions: nonEmptyTextArray("Edge precondition").optional(),
  operation: requiredText("Edge operation").optional(),
  payload: requiredText("Edge payload").optional(),
  durability: requiredText("Edge durability").optional(),
  failureBehavior: requiredText("Edge failure behavior").optional(),
  trustBoundary: requiredText("Edge trust boundary").optional(),
  evidence: z.array(ArchitectureEvidenceSchema).min(1).max(6).optional(),
});

export const ArchitectureEdgeSchema = z.strictObject({
  id: requiredText("Edge id"),
  source: requiredText("Edge source"),
  target: requiredText("Edge target"),
  label: requiredText("Edge label").optional(),
  href: requiredText("Edge href").optional(),
  importance: ArchitectureEdgeImportanceSchema.optional(),
  request: ArchitectureEdgeMessageSchema.optional(),
  response: ArchitectureEdgeMessageSchema.optional(),
  protocol: ArchitectureProtocolSchema.optional(),
  direction: ArchitectureEdgeDirectionSchema.optional(),
  flow: ArchitectureFlowSchema.optional(),
  details: ArchitectureEdgeDetailsSchema.optional(),
});

export const ArchitectureBoundarySchema = z.strictObject({
  id: requiredText("Boundary id"),
  label: requiredText("Boundary label"),
  kind: ArchitectureBoundaryKindSchema,
  description: requiredText("Boundary description").optional(),
  nodeIds: z.array(requiredText("Boundary node id")).min(1),
  parentId: requiredText("Boundary parent id").optional(),
});

export const ArchitectureDocumentSchema = z
  .strictObject({
    id: requiredText("Document id"),
    title: requiredText("Document title"),
    summary: requiredText("Document summary"),
    scope: ArchitectureScopeSchema,
    direction: ArchitectureDirectionSchema.optional(),
    nodes: z.array(ArchitectureNodeSchema),
    edges: z.array(ArchitectureEdgeSchema),
    boundaries: z.array(ArchitectureBoundarySchema).optional(),
  })
  .superRefine((document, context) => {
    const nodeIds = new Set<string>();
    const gridPositions = new Set<string>();
    const mobileOrders = new Set<number>();

    document.nodes.forEach((node, index) => {
      if (nodeIds.has(node.id)) {
        context.addIssue({
          code: "custom",
          message: `Node id "${node.id}" must be unique within the document.`,
          path: ["nodes", index, "id"],
        });
      } else {
        nodeIds.add(node.id);
      }

      const gridPosition = `${node.position.column}:${node.position.row}`;
      if (gridPositions.has(gridPosition)) {
        context.addIssue({
          code: "custom",
          message: `Grid position "${gridPosition}" must be unique within the document.`,
          path: ["nodes", index, "position"],
        });
      } else {
        gridPositions.add(gridPosition);
      }

      if (mobileOrders.has(node.mobileOrder)) {
        context.addIssue({
          code: "custom",
          message: `Mobile order "${node.mobileOrder}" must be unique within the document.`,
          path: ["nodes", index, "mobileOrder"],
        });
      } else {
        mobileOrders.add(node.mobileOrder);
      }
    });

    const edgeIds = new Set<string>();

    document.edges.forEach((edge, index) => {
      if (edgeIds.has(edge.id)) {
        context.addIssue({
          code: "custom",
          message: `Edge id "${edge.id}" must be unique within the document.`,
          path: ["edges", index, "id"],
        });
      } else {
        edgeIds.add(edge.id);
      }

      if (!nodeIds.has(edge.source)) {
        context.addIssue({
          code: "custom",
          message: `Edge source "${edge.source}" does not reference a node in this document.`,
          path: ["edges", index, "source"],
        });
      }

      if (!nodeIds.has(edge.target)) {
        context.addIssue({
          code: "custom",
          message: `Edge target "${edge.target}" does not reference a node in this document.`,
          path: ["edges", index, "target"],
        });
      }

      if (edge.direction === "two-way") {
        if (!edge.request) {
          context.addIssue({
            code: "custom",
            message: "Two-way edges must declare request details.",
            path: ["edges", index, "request"],
          });
        }
        if (!edge.response) {
          context.addIssue({
            code: "custom",
            message: "Two-way edges must declare response details.",
            path: ["edges", index, "response"],
          });
        }
      } else if (edge.response) {
        context.addIssue({
          code: "custom",
          message: "One-way edges cannot declare response details.",
          path: ["edges", index, "response"],
        });
      }
    });

    const boundaryIds = new Set<string>();
    const boundaries = document.boundaries ?? [];
    boundaries.forEach((boundary, index) => {
      if (boundaryIds.has(boundary.id) || nodeIds.has(boundary.id)) {
        context.addIssue({
          code: "custom",
          message: `Boundary id "${boundary.id}" must be unique and must not match a node id.`,
          path: ["boundaries", index, "id"],
        });
      } else {
        boundaryIds.add(boundary.id);
      }

      const members = new Set<string>();
      boundary.nodeIds.forEach((nodeId, nodeIndex) => {
        if (!nodeIds.has(nodeId)) {
          context.addIssue({
            code: "custom",
            message: `Boundary member "${nodeId}" does not reference a node in this document.`,
            path: ["boundaries", index, "nodeIds", nodeIndex],
          });
        }
        if (members.has(nodeId)) {
          context.addIssue({
            code: "custom",
            message: `Boundary member "${nodeId}" must not repeat.`,
            path: ["boundaries", index, "nodeIds", nodeIndex],
          });
        }
        members.add(nodeId);
      });
    });

    boundaries.forEach((boundary, index) => {
      if (boundary.parentId !== undefined) {
        const parentIndex = boundaries.findIndex(
          (candidate) => candidate.id === boundary.parentId,
        );
        if (parentIndex < 0) {
          context.addIssue({
            code: "custom",
            message: `Boundary parent "${boundary.parentId}" does not reference a boundary in this document.`,
            path: ["boundaries", index, "parentId"],
          });
        } else if (boundary.parentId === boundary.id) {
          context.addIssue({
            code: "custom",
            message: "A boundary cannot be its own parent.",
            path: ["boundaries", index, "parentId"],
          });
        } else {
          const parentMembers = new Set(boundaries[parentIndex].nodeIds);
          boundary.nodeIds.forEach((nodeId) => {
            if (!parentMembers.has(nodeId)) {
              context.addIssue({
                code: "custom",
                message: `Nested boundary member "${nodeId}" must also belong to parent boundary "${boundary.parentId}".`,
                path: ["boundaries", index, "nodeIds"],
              });
            }
          });
        }
      }

      const visited = new Set<string>([boundary.id]);
      let parentId = boundary.parentId;
      while (parentId) {
        if (visited.has(parentId)) {
          context.addIssue({
            code: "custom",
            message: "Boundary parent relationships cannot contain a cycle.",
            path: ["boundaries", index, "parentId"],
          });
          break;
        }
        visited.add(parentId);
        parentId = boundaries.find((candidate) => candidate.id === parentId)?.parentId;
      }
    });
  });

export type ArchitectureScope = z.infer<typeof ArchitectureScopeSchema>;
export type ArchitectureDirection = z.infer<
  typeof ArchitectureDirectionSchema
>;
export type ArchitectureNodeType = z.infer<typeof ArchitectureNodeTypeSchema>;
export type ArchitectureIconName = z.infer<typeof ArchitectureIconNameSchema>;
export type ArchitectureNodePosition = z.infer<typeof ArchitectureNodePositionSchema>;
export type ArchitectureEdgeMessage = z.infer<typeof ArchitectureEdgeMessageSchema>;
export type ArchitectureEdgeRequest = ArchitectureEdgeMessage;
export type ArchitectureEdgeResponse = ArchitectureEdgeMessage;
export type ArchitectureProtocol = z.infer<typeof ArchitectureProtocolSchema>;
export type ArchitectureEdgeDirection = z.infer<
  typeof ArchitectureEdgeDirectionSchema
>;
export type ArchitectureFlow = z.infer<typeof ArchitectureFlowSchema>;
export type ArchitectureEdgeImportance = z.infer<typeof ArchitectureEdgeImportanceSchema>;
export type ArchitectureNodeVariant = z.infer<typeof ArchitectureNodeVariantSchema>;
export type ArchitectureBoundaryKind = z.infer<typeof ArchitectureBoundaryKindSchema>;
export type ArchitectureEvidenceKind = z.infer<typeof ArchitectureEvidenceKindSchema>;
export type ArchitectureEvidence = z.infer<typeof ArchitectureEvidenceSchema>;
export type ArchitectureContractItem = z.infer<typeof ArchitectureContractItemSchema>;
export type ArchitectureNodeDetails = z.infer<typeof ArchitectureNodeDetailsSchema>;
export type ArchitectureEdgeDetails = z.infer<typeof ArchitectureEdgeDetailsSchema>;
export type ArchitectureBoundary = z.infer<typeof ArchitectureBoundarySchema>;
export type ArchitectureNode = z.infer<typeof ArchitectureNodeSchema>;
export type ArchitectureEdge = z.infer<typeof ArchitectureEdgeSchema>;
export type ArchitectureDocument = z.infer<typeof ArchitectureDocumentSchema>;

export const architectureNodeSchema = ArchitectureNodeSchema;
export const architectureEdgeSchema = ArchitectureEdgeSchema;
export const architectureDocumentSchema = ArchitectureDocumentSchema;
