-- Native B-rep geometry artifacts: the authoritative geometric representation
-- of one built CAD candidate.
--
-- Before this table, a built candidate's geometry existed only as the handful
-- of numbers in `geometry_snapshots`. The CadQuery shape itself lived inside a
-- sandboxed subprocess and was destroyed when that process exited, so the only
-- surviving record of what a candidate physically was, was a summary of it.
-- Every later question ("which faces are cylindrical?", "what is the clearance
-- here?") would have required re-executing model.py.
--
-- Now the runtime serializes the normalized root shape to a native OCCT .brep
-- file, uploads it to the same `3dProjects` bucket the candidate source lives
-- in, and records it here. `geometry_snapshots` becomes a derived observation
-- of a row in this table rather than the geometry itself.
--
-- The layering this preserves:
--     model.py         canonical, reproducible design source
--     .brep artifact   authoritative geometry of one built candidate
--     snapshot         compact derived observations of that artifact
-- The artifact does not replace model.py. It is what model.py produced.
create table public.geometry_artifacts (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  part_id uuid not null,
  -- Null for artifacts built from accepted (committed) source, which belongs
  -- to no edit job. Mirrors geometry_snapshots.edit_job_id exactly: the
  -- previous side of a comparison is frequently accepted source.
  edit_job_id uuid references public.edit_jobs(id) on delete cascade,
  source_storage_path text not null check (btrim(source_storage_path) <> ''),
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  geometry_checker_version integer not null
    check (geometry_checker_version >= 1),
  artifact_format text not null check (artifact_format in ('brep')),
  artifact_storage_path text not null check (btrim(artifact_storage_path) <> ''),
  -- sha256 of the exact serialized bytes. This is an INTEGRITY AND IDENTITY
  -- check on the file, NOT a canonical identity for the geometry. OCCT's B-rep
  -- serialization is deterministic for a given construction but not canonical
  -- across constructions: a 10mm cube built with .box() and the same cube built
  -- with .rect().extrude().translate() are geometrically identical and produce
  -- different bytes and different digests. Nothing may infer "different digest
  -- therefore different geometry" from this column.
  artifact_digest text not null check (artifact_digest ~ '^[0-9a-f]{64}$'),
  artifact_bytes bigint not null check (artifact_bytes > 0),
  -- Which CAD runtime wrote the file (cadquery / occt / checker versions). A
  -- .brep is only meaningful with respect to the kernel that produced it.
  geometry_runtime jsonb
    check (geometry_runtime is null or jsonb_typeof(geometry_runtime) = 'object'),
  created_at timestamptz not null default now(),
  foreign key (project_id, part_id)
    references public.parts(project_id, id)
    on delete cascade,
  -- Same cache key as geometry_snapshots, giving a 1:1 pairing and reusing the
  -- reuse semantics that already exist: identical source under an identical
  -- checker version is identical geometry, so measuring it twice is waste. The
  -- key is the SOURCE hash, never the artifact digest -- see above.
  unique (source_sha256, geometry_checker_version)
);

create index geometry_artifacts_part_idx
  on public.geometry_artifacts (part_id, created_at desc);

create index geometry_artifacts_edit_job_idx
  on public.geometry_artifacts (edit_job_id)
  where edit_job_id is not null;

alter table public.geometry_artifacts enable row level security;

-- A snapshot is now an observation OF an artifact, so it must be possible to
-- say which artifact it observed. Nullable and `on delete set null` because a
-- snapshot measured before this table existed, or one whose artifact upload
-- failed, is still a valid measurement -- losing the artifact link degrades
-- what can be asked later, it does not invalidate what was already measured.
alter table public.geometry_snapshots
  add column geometry_artifact_id uuid
    references public.geometry_artifacts(id) on delete set null;

create index geometry_snapshots_artifact_idx
  on public.geometry_snapshots (geometry_artifact_id)
  where geometry_artifact_id is not null;
