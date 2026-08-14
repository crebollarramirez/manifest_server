-- Project-scoped generation jobs: CAD/mesh export, CAD validation (against
-- either the accepted source or an in-flight edit candidate), and geometry
-- checks. This is the full final shape of generation_jobs and every function
-- whose primary payload is a generation_jobs row.
create table public.generation_jobs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  part_id uuid not null,
  type text not null
    check (type in ('export_cad', 'export_mesh', 'validate_cad', 'geometry_check')),
  status text not null default 'queued'
    check (status in ('queued', 'running', 'completed', 'failed', 'cancelled')),
  error_message text,
  created_at timestamptz not null default now(),
  source_sha256 text
    check (source_sha256 is null or source_sha256 ~ '^[0-9a-f]{64}$'),
  result jsonb,
  source_kind text not null default 'accepted'
    check (source_kind in ('accepted', 'candidate')),
  source_storage_path text,
  edit_job_id uuid references public.edit_jobs(id) on delete cascade,
  -- The exact prior source version a geometry_check job is comparing the
  -- current candidate against.
  previous_source_storage_path text,
  previous_source_sha256 text
    check (
      previous_source_sha256 is null
      or previous_source_sha256 ~ '^[0-9a-f]{64}$'
    ),
  foreign key (project_id, part_id)
    references public.parts(project_id, id)
    on delete cascade,
  check (type <> 'validate_cad' or source_sha256 is not null),
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
  )
);

create index generation_jobs_queued_type_idx
  on public.generation_jobs (type, created_at, id)
  where status = 'queued'
    and type in ('export_cad', 'export_mesh', 'validate_cad', 'geometry_check');

create index generation_jobs_project_part_status_idx
  on public.generation_jobs (project_id, part_id, status);

create index generation_jobs_edit_job_idx
  on public.generation_jobs (edit_job_id)
  where edit_job_id is not null;

alter table public.generation_jobs enable row level security;

create function public.claim_next_generation_job(
  p_job_type text default 'export_cad'
)
returns setof public.generation_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed_job public.generation_jobs;
begin
  select job.*
  into claimed_job
  from public.generation_jobs as job
  where job.status = 'queued'
    and job.type = p_job_type
  order by job.created_at, job.id
  for update skip locked
  limit 1;

  if claimed_job.id is null then
    return;
  end if;

  update public.generation_jobs
  set status = 'running', error_message = null
  where id = claimed_job.id
  returning * into claimed_job;

  return next claimed_job;
end;
$$;

create function public.claim_next_supported_generation_job(
  p_job_types text[]
)
returns setof public.generation_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed_job public.generation_jobs;
begin
  if p_job_types is null or cardinality(p_job_types) = 0 then
    return;
  end if;

  select job.*
  into claimed_job
  from public.generation_jobs as job
  where job.status = 'queued'
    and job.type = any(p_job_types)
  order by job.created_at, job.id
  for update skip locked
  limit 1;

  if claimed_job.id is null then
    return;
  end if;

  update public.generation_jobs
  set status = 'running', error_message = null
  where id = claimed_job.id
  returning * into claimed_job;

  return next claimed_job;
end;
$$;

create function public.complete_cad_validation_and_queue_export(
  p_validation_job_id uuid,
  p_source_sha256 text,
  p_result jsonb
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  validation_job public.generation_jobs;
  export_job_id uuid;
begin
  select job.*
  into validation_job
  from public.generation_jobs as job
  where job.id = p_validation_job_id
  for update;

  if validation_job.id is null then
    raise exception 'Validation job % was not found.', p_validation_job_id;
  end if;

  if validation_job.type <> 'validate_cad'
    or validation_job.source_kind <> 'accepted' then
    raise exception 'Job % is not an accepted-source CAD validation job.',
      p_validation_job_id;
  end if;

  if validation_job.status <> 'running' then
    raise exception 'Validation job % is not running.', p_validation_job_id;
  end if;

  if validation_job.source_sha256 <> p_source_sha256 then
    raise exception 'Validation job source hash does not match.';
  end if;

  insert into public.generation_jobs (
    project_id,
    part_id,
    type,
    status,
    source_sha256
  )
  values (
    validation_job.project_id,
    validation_job.part_id,
    'export_cad',
    'queued',
    p_source_sha256
  )
  returning id into export_job_id;

  update public.generation_jobs
  set
    status = 'completed',
    error_message = null,
    result = coalesce(p_result, '{}'::jsonb) || jsonb_build_object(
      'export_job_id', export_job_id
    )
  where id = p_validation_job_id;

  return export_job_id;
end;
$$;

create function public.complete_candidate_cad_validation(
  p_validation_job_id uuid,
  p_source_sha256 text,
  p_result jsonb
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  validation_job public.generation_jobs;
begin
  select job.*
  into validation_job
  from public.generation_jobs as job
  where job.id = p_validation_job_id
  for update;

  if validation_job.id is null then
    raise exception 'Validation job % was not found.', p_validation_job_id;
  end if;

  if validation_job.type <> 'validate_cad'
    or validation_job.source_kind <> 'candidate' then
    raise exception 'Job % is not a candidate CAD validation job.',
      p_validation_job_id;
  end if;

  if validation_job.status <> 'running' then
    raise exception 'Validation job % is not running.', p_validation_job_id;
  end if;

  if validation_job.source_sha256 <> p_source_sha256 then
    raise exception 'Validation job source hash does not match.';
  end if;

  update public.generation_jobs
  set
    status = 'completed',
    error_message = null,
    result = coalesce(p_result, '{}'::jsonb)
  where id = p_validation_job_id;
end;
$$;

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

revoke all on function public.claim_next_generation_job(text) from public, anon, authenticated;
revoke all on function public.claim_next_supported_generation_job(text[])
  from public, anon, authenticated;
revoke all on function public.complete_cad_validation_and_queue_export(
  uuid,
  text,
  jsonb
) from public, anon, authenticated;
revoke all on function public.complete_candidate_cad_validation(
  uuid,
  text,
  jsonb
) from public, anon, authenticated;
revoke all on function public.queue_geometry_check(uuid, text)
  from public, anon, authenticated;
revoke all on function public.complete_geometry_check(uuid, text, jsonb)
  from public, anon, authenticated;

grant execute on function public.claim_next_generation_job(text) to service_role;
grant execute on function public.claim_next_supported_generation_job(text[])
  to service_role;
grant execute on function public.complete_cad_validation_and_queue_export(
  uuid,
  text,
  jsonb
) to service_role;
grant execute on function public.complete_candidate_cad_validation(
  uuid,
  text,
  jsonb
) to service_role;
grant execute on function public.queue_geometry_check(uuid, text)
  to service_role;
grant execute on function public.complete_geometry_check(uuid, text, jsonb)
  to service_role;
