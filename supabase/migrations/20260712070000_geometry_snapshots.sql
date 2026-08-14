-- Dedicated, immutable geometry snapshot storage: one row per exact source
-- version, keyed for cache reuse by (source_sha256, geometry_checker_version).
--
-- check_geometry gives Agent3D deterministic geometric evidence (volume,
-- bounding box, center of mass, solid/face/edge counts) about the current
-- candidate compared against the candidate immediately before the latest
-- mutation.
create table public.geometry_snapshots (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  part_id uuid not null,
  edit_job_id uuid references public.edit_jobs(id) on delete cascade,
  source_storage_path text not null check (btrim(source_storage_path) <> ''),
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  geometry_checker_version integer not null default 1
    check (geometry_checker_version >= 1),
  execution_ok boolean not null,
  geometry_valid boolean,
  error_message text,
  volume_mm3 double precision,
  bounding_box jsonb
    check (bounding_box is null or jsonb_typeof(bounding_box) = 'object'),
  center_of_mass jsonb
    check (center_of_mass is null or jsonb_typeof(center_of_mass) = 'array'),
  solid_count integer,
  face_count integer,
  edge_count integer,
  created_at timestamptz not null default now(),
  foreign key (project_id, part_id)
    references public.parts(project_id, id)
    on delete cascade,
  unique (source_sha256, geometry_checker_version),
  check (execution_ok = false or geometry_valid is not null)
);

create index geometry_snapshots_part_idx
  on public.geometry_snapshots (part_id, created_at desc);

create index geometry_snapshots_edit_job_idx
  on public.geometry_snapshots (edit_job_id)
  where edit_job_id is not null;

alter table public.geometry_snapshots enable row level security;
