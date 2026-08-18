-- Orientation and edge treatment become measurable.
--
-- Until now a snapshot recorded volume, an axis-aligned bounding box, a center
-- of mass, and three counts. That vocabulary can say how much material exists
-- and how far it reaches on each world axis, never how the shape is oriented:
-- two parts whose support faces differ by six degrees have byte-identical
-- bounding boxes, and rounding four of a part's forty-eight edges moves the
-- volume by four hundredths of a percent. Both are shipped defects this schema
-- had no column to describe.
--
--   planar_faces           the largest planar faces, each with its outward
--                          normal, inclination from horizontal, area, and
--                          centroid. Capped, so `face_count` minus
--                          `non_planar_face_count` is what says whether the
--                          list is complete.
--   non_planar_face_count  cylinders, fillets, lofts -- the faces a planar
--                          census cannot describe.
--   sharp_edge_count       edges whose two faces still meet at a corner.
--                          Filleting drives this down while *raising* the
--                          total edge count, so the two answer different
--                          questions and both are kept.
--
-- Nullable with no backfill, and no backfill is possible: these are derived by
-- executing the model, not stored from it. Existing rows keep null and are
-- already unreachable -- GEOMETRY_CHECKER_VERSION moved to 2 in the same
-- change, and the cache is keyed on (source_sha256, geometry_checker_version),
-- so nothing measured under the older vocabulary can be served as if it
-- answered these questions.
--
-- The array check mirrors center_of_mass, the table's other list-valued jsonb.
alter table public.geometry_snapshots
  add column planar_faces jsonb
    check (planar_faces is null or jsonb_typeof(planar_faces) = 'array'),
  add column non_planar_face_count integer
    check (non_planar_face_count is null or non_planar_face_count >= 0),
  add column sharp_edge_count integer
    check (sharp_edge_count is null or sharp_edge_count >= 0);
