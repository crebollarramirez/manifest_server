-- check_geometry has never successfully persisted a snapshot.
--
-- geometry_check_job writes every name in its _GEOMETRY_FIELDS tuple as a
-- column, and that tuple includes `diagnostics`, which
-- 20260712070000_geometry_snapshots.sql was created without. Every insert
-- therefore fails with PGRST204 ("Could not find the 'diagnostics' column"),
-- which escapes the duplicate-key handler and crashes the validator worker --
-- so the tool returns GEOMETRY_CHECK_FAILED on 100% of calls, and Agent3D
-- never receives the geometric evidence the tool exists to give it.
--
-- The column is added rather than the field dropped because the field is
-- load-bearing: geometry_check_job derives it from the AST report, and
-- CheckGeometryTool feeds it through bounded_diagnostics into the message the
-- agent reads when a check fails. Removing it would leave the write path
-- working and silently strip the explanation from every cache hit.
--
-- Nullable with no backfill: existing rows predate any successful write, and
-- an absent diagnostics list already means "none reported" everywhere it is
-- read. The array check mirrors center_of_mass, the table's other list-valued
-- jsonb column.
alter table public.geometry_snapshots
  add column diagnostics jsonb
    check (diagnostics is null or jsonb_typeof(diagnostics) = 'array');
