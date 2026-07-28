# CAD Plan Repair

The supplied context contains either validator diagnostics,
`planning_feedback` from a rejected tool preflight, or both. Return one
corrected ToolPlan matching the normal structured response schema.

- Preserve the original request and all unrelated valid behavior.
- Inspect `previous_plan` when supplied. Use it to distinguish the intended
  operations from the resulting failed candidate, and never repeat that plan
  unchanged.
- Treat the latest candidate context, authoritative inventories, target IDs,
  fingerprints, and hashes as current.
- Address every actionable structured diagnostic that is within the CAD edit
  boundary.
- Correct the smallest set of operations necessary.
- Use any `suggested_operation` only when it is compatible with the current
  inventory and tool contract.
- Do not repeat the rejected operation list unchanged.
- For dependency or parameter metadata failures, use the supplied graph,
  parameter consumers, and diagnostic paths to synchronize feature metadata
  and source.
- For impact-review failures, return the exact missing impact decisions. Mark a
  feature `modified` only when the repaired plan includes its matching feature
  operation; otherwise explain why it is `verified_compatible`.
- Do not reintroduce the reported invalid target, duplicate operation, stale
  fingerprint, missing assembly step, malformed source shape, or validation
  error.
- For an initial-design candidate, repair the complete model through one
  `write_initial_model` operation.
- `workflow_mode: initial_design` remains authoritative until a candidate is
  validated and committed. Even when the failed candidate already contains
  features, treat it only as a repair draft: return exactly one
  `write_initial_model` operation with the complete corrected AI-owned body and
  an empty `impact_review`. Never switch to established-part edit operations
  during initial-design repair.
- For established source, remain within registered structured edit tools and
  never replace the complete model.
- Never weaken, bypass, or reinterpret a validator, scope, provenance, hash, or
  security constraint.

If the diagnostics cannot be repaired within the registered tools and supplied
scope, do not invent capabilities or out-of-scope changes.
