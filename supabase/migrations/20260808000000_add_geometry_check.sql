-- Add lightweight geometry-check jobs and dedicated geometry snapshot storage.
--
-- check_geometry gives Agent3D deterministic geometric evidence (volume,
-- bounding box, center of mass, solid/face/edge counts) about the current
-- candidate compared against the candidate immediately before the latest
-- mutation. It reuses the existing generation_jobs queue/claim RPCs rather
-- than introducing a second job table or worker.

-- edit_jobs.last_checked_source_sha256 is the minimal history mechanism
-- needed to chain comparisons B->C, C->D, ... instead of always comparing
-- against the original accepted source. Seeded from accepted_source_sha256
-- on the first geometry check for a job.
alter table public.edit_jobs
  add column last_checked_source_sha256 text;

alter table public.edit_jobs
  add constraint edit_jobs_last_checked_source_sha256_check
  check (
    last_checked_source_sha256 is null
    or last_checked_source_sha256 ~ '^[0-9a-f]{64}$'
  );

-- generation_jobs gains a new job type and the two columns a geometry check
-- needs to describe what it is comparing against.
alter table public.generation_jobs
  drop constraint generation_jobs_type_check;

alter table public.generation_jobs
  add constraint generation_jobs_type_check
  check (type in ('export_cad', 'export_mesh', 'validate_cad', 'geometry_check'));

alter table public.generation_jobs
  drop constraint generation_jobs_candidate_source_check;

alter table public.generation_jobs
  add constraint generation_jobs_candidate_source_check
  check (
    (
      source_kind = 'accepted'
      and source_storage_path is null
    )
    or (
      source_kind = 'candidate'
      and type in ('validate_cad', 'geometry_check')
      and source_storage_path is not null
      and btrim(source_storage_path) <> ''
      and edit_job_id is not null
    )
  );

alter table public.generation_jobs
  add column previous_source_storage_path text,
  add column previous_source_sha256 text
    check (
      previous_source_sha256 is null
      or previous_source_sha256 ~ '^[0-9a-f]{64}$'
    );

drop index if exists public.generation_jobs_queued_type_idx;

create index generation_jobs_queued_type_idx
  on public.generation_jobs (type, created_at, id)
  where status = 'queued'
    and type in ('export_cad', 'export_mesh', 'validate_cad', 'geometry_check');

-- Dedicated, immutable geometry snapshot storage: one row per exact source
-- version, keyed for cache reuse by (source_sha256, geometry_checker_version).
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

-- Queue one geometry-check job for the edit job's current live candidate.
-- Resolves the candidate path server-side (the same convention as
-- EditWorkflowOrchestrator._candidate_path) so callers never need to supply
-- internal storage paths, and resolves the immediately previous source
-- version from last_checked_source_sha256 (falling back to the accepted
-- baseline on the first call for this edit job). This does not require
-- worker_id/lease ownership like the "_owned" edit-job mutation RPCs: it only
-- inserts a queue row and advances non-critical bookkeeping, so gating on
-- edit_jobs.status = 'running' is sufficient and keeps worker identity out of
-- the tool-execution surface.
create function public.queue_geometry_check(
  p_edit_job_id uuid,
  p_candidate_sha256 text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  edit_job public.edit_jobs;
  candidate_path text;
  previous_hash text;
  previous_path text;
  geometry_job public.generation_jobs;
begin
  select job.*
  into edit_job
  from public.edit_jobs as job
  where job.id = p_edit_job_id
  for update;

  if edit_job.id is null
    or edit_job.status <> 'running'
    or edit_job.resolved_part_id is null then
    raise exception 'Edit job % is not ready for a geometry check.', p_edit_job_id;
  end if;

  if p_candidate_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'Candidate source hash is invalid.';
  end if;

  candidate_path := edit_job.project_id::text
    || '/candidates/cad/'
    || edit_job.resolved_part_id::text
    || '/'
    || edit_job.id::text
    || '/model.py';

  if edit_job.last_checked_source_sha256 is not null then
    previous_hash := edit_job.last_checked_source_sha256;
    previous_path := null;
  else
    previous_hash := edit_job.accepted_source_sha256;
    previous_path := edit_job.original_storage_path;
  end if;

  insert into public.generation_jobs (
    project_id,
    part_id,
    type,
    status,
    source_kind,
    source_storage_path,
    source_sha256,
    previous_source_storage_path,
    previous_source_sha256,
    edit_job_id
  )
  values (
    edit_job.project_id,
    edit_job.resolved_part_id,
    'geometry_check',
    'queued',
    'candidate',
    candidate_path,
    p_candidate_sha256,
    previous_path,
    previous_hash,
    edit_job.id
  )
  returning * into geometry_job;

  update public.edit_jobs
  set last_checked_source_sha256 = p_candidate_sha256
  where id = edit_job.id;

  return geometry_job.id;
end;
$$;

-- Hash-guarded completion, mirroring complete_candidate_cad_validation:
-- proves the persisted result belongs to exactly the source version the
-- worker was asked to inspect. Failure/cancelled outcomes reuse the plain
-- table update already used by cad_validation_worker.py's update_job().
create function public.complete_geometry_check(
  p_job_id uuid,
  p_source_sha256 text,
  p_result jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  geometry_job public.generation_jobs;
begin
  select job.*
  into geometry_job
  from public.generation_jobs as job
  where job.id = p_job_id
  for update;

  if geometry_job.id is null then
    raise exception 'Geometry check job % was not found.', p_job_id;
  end if;

  if geometry_job.type <> 'geometry_check' then
    raise exception 'Job % is not a geometry check job.', p_job_id;
  end if;

  if geometry_job.status <> 'running' then
    raise exception 'Geometry check job % is not running.', p_job_id;
  end if;

  if geometry_job.source_sha256 <> p_source_sha256 then
    raise exception 'Geometry check job source hash does not match.';
  end if;

  update public.generation_jobs
  set
    status = 'completed',
    error_message = null,
    result = coalesce(p_result, '{}'::jsonb)
  where id = p_job_id;
end;
$$;

revoke all on function public.queue_geometry_check(uuid, text)
  from public, anon, authenticated;
revoke all on function public.complete_geometry_check(uuid, text, jsonb)
  from public, anon, authenticated;

grant execute on function public.queue_geometry_check(uuid, text)
  to service_role;
grant execute on function public.complete_geometry_check(uuid, text, jsonb)
  to service_role;
