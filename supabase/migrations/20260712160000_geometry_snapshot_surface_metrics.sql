-- Two measurements that complete the existing snapshot vocabulary.
--
-- The snapshot already reported volume and the solid/face/edge counts. Surface
-- area and vertex count are the remaining members of that same set, and both
-- are one call on the normalized root the analyzer already holds -- there is no
-- extra geometry work to do for them.
--
-- Surface area is what separates two shapes of equal volume with different
-- amounts of detail: hollowing a part raises its area sharply while barely
-- moving its volume, which is exactly the case a volume delta alone reports as
-- "almost nothing happened".
--
-- GEOMETRY_CHECKER_VERSION moves to 3 in the same change (snapshots also became
-- derived observations of a persisted B-rep artifact), so no backfill is needed
-- or possible: `geometry_snapshots` and `geometry_artifacts` are keyed on
-- (source_sha256, geometry_checker_version), and version-2 rows are simply
-- never read again.
alter table public.geometry_snapshots
  add column surface_area_mm2 double precision
    check (surface_area_mm2 is null or surface_area_mm2 >= 0),
  add column vertex_count integer
    check (vertex_count is null or vertex_count >= 0);
