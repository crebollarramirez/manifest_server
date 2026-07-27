-- Add resumable project-scoped CAD edit workflows and candidate validation.
create table public.edit_jobs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  request_text text not null check (btrim(request_text) <> ''),
  messages jsonb not null default '[]'::jsonb
    check (jsonb_typeof(messages) = 'array'),
  resolved_part_id uuid,
  resolved_targets jsonb not null default '[]'::jsonb
    check (jsonb_typeof(resolved_targets) = 'array'),
  status text not null default 'queued'
    check (status in ('queued', 'running', 'completed', 'failed', 'cancelled')),
  state text not null default 'received'
    check (
      state in (
        'received',
        'ensuring_index',
        'resolving_target',
        'retrieving_context',
        'planning_edit',
        'validating_plan',
        'applying_edit',
        'validating_candidate',
        'classifying_error',
        'retrieving_repair_context',
        'planning_repair',
        'applying_repair',
        'committing',
        'reindexing',
        'queueing_export',
        'completed',
        'failed',
        'cancelled'
      )
    ),
  attempt_count integer not null default 0
    check (attempt_count between 0 and 3),
  max_attempts integer not null default 3
    check (max_attempts = 3),
  accepted_source_sha256 text,
  original_storage_path text,
  current_candidate_path text,
  current_candidate_sha256 text,
  validation_job_id uuid,
  index_job_id uuid,
  export_job_id uuid,
  worker_id text,
  lease_expires_at timestamptz,
  heartbeat_at timestamptz,
  history jsonb not null default '[]'::jsonb
    check (jsonb_typeof(history) = 'array'),
  result jsonb,
  error_code text,
  error_message text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  foreign key (project_id, resolved_part_id)
    references public.parts(project_id, id)
    on delete cascade,
  check (
    accepted_source_sha256 is null
    or accepted_source_sha256 ~ '^[0-9a-f]{64}$'
  ),
  check (
    current_candidate_sha256 is null
    or current_candidate_sha256 ~ '^[0-9a-f]{64}$'
  )
);

create index edit_jobs_claim_idx
  on public.edit_jobs (created_at, id)
  where status in ('queued', 'running');

create index edit_jobs_project_status_idx
  on public.edit_jobs (project_id, status);

create unique index edit_jobs_one_active_per_part_idx
  on public.edit_jobs (project_id, resolved_part_id)
  where resolved_part_id is not null
    and status in ('queued', 'running');

alter table public.generation_jobs
  add column source_kind text not null default 'accepted'
    check (source_kind in ('accepted', 'candidate')),
  add column source_storage_path text,
  add column edit_job_id uuid references public.edit_jobs(id) on delete cascade;

alter table public.generation_jobs
  add constraint generation_jobs_candidate_source_check
  check (
    (
      source_kind = 'accepted'
      and source_storage_path is null
    )
    or (
      source_kind = 'candidate'
      and type = 'validate_cad'
      and source_storage_path is not null
      and btrim(source_storage_path) <> ''
      and edit_job_id is not null
    )
  );

create index generation_jobs_edit_job_idx
  on public.generation_jobs (edit_job_id)
  where edit_job_id is not null;

create function public.claim_next_edit_job(
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns setof public.edit_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed_job public.edit_jobs;
  lease_seconds integer := greatest(coalesce(p_lease_seconds, 300), 30);
begin
  if p_worker_id is null or btrim(p_worker_id) = '' then
    raise exception 'A non-empty worker ID is required.';
  end if;

  select job.*
  into claimed_job
  from public.edit_jobs as job
  where job.status = 'queued'
    or (
      job.status = 'running'
      and job.lease_expires_at is not null
      and job.lease_expires_at < now()
    )
  order by job.created_at, job.id
  for update skip locked
  limit 1;

  if claimed_job.id is null then
    return;
  end if;

  update public.edit_jobs
  set
    status = 'running',
    worker_id = btrim(p_worker_id),
    started_at = coalesce(started_at, now()),
    heartbeat_at = now(),
    lease_expires_at = now() + make_interval(secs => lease_seconds),
    completed_at = null
  where id = claimed_job.id
  returning * into claimed_job;

  return next claimed_job;
end;
$$;

create function public.heartbeat_edit_job(
  p_edit_job_id uuid,
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  updated_count integer;
  lease_seconds integer := greatest(coalesce(p_lease_seconds, 300), 30);
begin
  update public.edit_jobs
  set
    heartbeat_at = now(),
    lease_expires_at = now() + make_interval(secs => lease_seconds)
  where id = p_edit_job_id
    and status = 'running'
    and worker_id = btrim(p_worker_id);

  get diagnostics updated_count = row_count;
  return updated_count = 1;
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

create function public.queue_edit_candidate_validation(
  p_edit_job_id uuid,
  p_candidate_path text,
  p_candidate_sha256 text,
  p_attempt_count integer
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  edit_job public.edit_jobs;
  validation_job public.generation_jobs;
  expected_prefix text;
begin
  select job.*
  into edit_job
  from public.edit_jobs as job
  where job.id = p_edit_job_id
  for update;

  if edit_job.id is null then
    raise exception 'Edit job % was not found.', p_edit_job_id;
  end if;

  if edit_job.status <> 'running' or edit_job.resolved_part_id is null then
    raise exception 'Edit job % is not ready for candidate validation.',
      p_edit_job_id;
  end if;

  if p_attempt_count < 1 or p_attempt_count > edit_job.max_attempts then
    raise exception 'Candidate validation attempt is outside the allowed range.';
  end if;

  if p_attempt_count < edit_job.attempt_count
    or p_attempt_count > edit_job.attempt_count + 1 then
    raise exception 'Candidate validation attempt is out of sequence.';
  end if;

  if p_candidate_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'Candidate source hash is invalid.';
  end if;

  expected_prefix := edit_job.project_id::text
    || '/candidates/cad/'
    || edit_job.resolved_part_id::text
    || '/'
    || edit_job.id::text
    || '/attempt-'
    || p_attempt_count::text
    || '/';

  if p_candidate_path <> (expected_prefix || 'model.py') then
    raise exception 'Candidate source path does not belong to the edit attempt.';
  end if;

  if edit_job.validation_job_id is not null then
    select job.*
    into validation_job
    from public.generation_jobs as job
    where job.id = edit_job.validation_job_id;

    if validation_job.id is not null
      and validation_job.source_kind = 'candidate'
      and validation_job.source_storage_path = p_candidate_path
      and validation_job.source_sha256 = p_candidate_sha256 then
      return validation_job.id;
    end if;
  end if;

  insert into public.generation_jobs (
    project_id,
    part_id,
    type,
    status,
    source_sha256,
    source_kind,
    source_storage_path,
    edit_job_id
  )
  values (
    edit_job.project_id,
    edit_job.resolved_part_id,
    'validate_cad',
    'queued',
    p_candidate_sha256,
    'candidate',
    p_candidate_path,
    edit_job.id
  )
  returning * into validation_job;

  update public.edit_jobs
  set
    state = 'validating_candidate',
    attempt_count = p_attempt_count,
    current_candidate_path = p_candidate_path,
    current_candidate_sha256 = p_candidate_sha256,
    validation_job_id = validation_job.id
  where id = edit_job.id;

  return validation_job.id;
end;
$$;

create function public.queue_edit_index_build(
  p_edit_job_id uuid,
  p_state text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  edit_job public.edit_jobs;
  index_job public.index_jobs;
begin
  if p_state not in ('ensuring_index', 'reindexing') then
    raise exception 'Invalid edit index state %.', p_state;
  end if;

  select job.*
  into edit_job
  from public.edit_jobs as job
  where job.id = p_edit_job_id
  for update;

  if edit_job.id is null or edit_job.status <> 'running' then
    raise exception 'Edit job % is not running.', p_edit_job_id;
  end if;

  if edit_job.index_job_id is not null then
    select job.*
    into index_job
    from public.index_jobs as job
    where job.id = edit_job.index_job_id;

    if index_job.id is not null
      and index_job.status in ('queued', 'running') then
      return index_job.id;
    end if;
  end if;

  begin
    insert into public.index_jobs (
      project_id,
      type,
      status
    )
    values (
      edit_job.project_id,
      'build_index',
      'queued'
    )
    returning * into index_job;
  exception
    when unique_violation then
      select job.*
      into index_job
      from public.index_jobs as job
      where job.project_id = edit_job.project_id
        and job.type = 'build_index'
        and job.status in ('queued', 'running')
      order by job.created_at, job.id
      limit 1;
  end;

  if index_job.id is null then
    raise exception 'Could not queue or locate a project index build.';
  end if;

  update public.edit_jobs
  set
    state = p_state,
    index_job_id = index_job.id
  where id = edit_job.id;

  return index_job.id;
end;
$$;

create function public.queue_edit_export(
  p_edit_job_id uuid,
  p_source_sha256 text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  edit_job public.edit_jobs;
  export_job public.generation_jobs;
begin
  select job.*
  into edit_job
  from public.edit_jobs as job
  where job.id = p_edit_job_id
  for update;

  if edit_job.id is null
    or edit_job.status <> 'running'
    or edit_job.resolved_part_id is null then
    raise exception 'Edit job % is not ready to queue export.', p_edit_job_id;
  end if;

  if p_source_sha256 !~ '^[0-9a-f]{64}$'
    or edit_job.current_candidate_sha256 <> p_source_sha256 then
    raise exception 'Committed source hash does not match the edit candidate.';
  end if;

  if edit_job.export_job_id is not null then
    select job.*
    into export_job
    from public.generation_jobs as job
    where job.id = edit_job.export_job_id;
    if export_job.id is not null then
      return export_job.id;
    end if;
  end if;

  insert into public.generation_jobs (
    project_id,
    part_id,
    type,
    status,
    source_sha256,
    edit_job_id
  )
  values (
    edit_job.project_id,
    edit_job.resolved_part_id,
    'export_cad',
    'queued',
    p_source_sha256,
    edit_job.id
  )
  returning * into export_job;

  update public.edit_jobs
  set
    state = 'queueing_export',
    export_job_id = export_job.id
  where id = edit_job.id;

  return export_job.id;
end;
$$;

create or replace function public.complete_cad_validation_and_queue_export(
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

alter table public.edit_jobs enable row level security;

revoke all on function public.claim_next_edit_job(text, integer)
  from public, anon, authenticated;
revoke all on function public.heartbeat_edit_job(uuid, text, integer)
  from public, anon, authenticated;
revoke all on function public.complete_candidate_cad_validation(
  uuid,
  text,
  jsonb
) from public, anon, authenticated;
revoke all on function public.queue_edit_candidate_validation(
  uuid,
  text,
  text,
  integer
) from public, anon, authenticated;
revoke all on function public.queue_edit_index_build(uuid, text)
  from public, anon, authenticated;
revoke all on function public.queue_edit_export(uuid, text)
  from public, anon, authenticated;

grant execute on function public.claim_next_edit_job(text, integer)
  to service_role;
grant execute on function public.heartbeat_edit_job(uuid, text, integer)
  to service_role;
grant execute on function public.complete_candidate_cad_validation(
  uuid,
  text,
  jsonb
) to service_role;
grant execute on function public.queue_edit_candidate_validation(
  uuid,
  text,
  text,
  integer
) to service_role;
grant execute on function public.queue_edit_index_build(uuid, text)
  to service_role;
grant execute on function public.queue_edit_export(uuid, text)
  to service_role;
