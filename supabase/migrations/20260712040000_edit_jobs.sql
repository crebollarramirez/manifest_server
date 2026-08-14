-- Resumable, project-scoped CAD edit workflows: durable candidate
-- validation/repair state, lease-owned worker coordination, and idempotent
-- submission. This is the full final shape of edit_jobs and every function
-- whose primary side effect is on this table.
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
        'creating_goal',
        'planning_goal',
        'ensuring_index',
        'resolving_target',
        'retrieving_context',
        'planning_edit',
        'validating_plan',
        'applying_edit',
        'planning_initial_design',
        'planning_initial_repair',
        'applying_initial_design',
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
  -- Run telemetry, written once at finalization: tool mix, batched rounds,
  -- tool failures, token usage. Deliberately separate from `result`, which is
  -- the public job payload the control plane serves -- telemetry does not
  -- belong in a product contract.
  --
  -- Deliberately jsonb rather than a column per number: which measures matter
  -- is still being discovered, and a column each would mean a migration plus
  -- an allowlist edit in patch_edit_job_owned every time. Promote a field to
  -- a real column once it is queried often enough to deserve an index.
  --
  -- What is NOT here is the per-step and per-milestone record -- turns per
  -- step, validation outcomes, timings -- because edit_job_events already
  -- carries all of that and is already SQL-queryable. This holds only what
  -- events structurally cannot: facts about individual tool calls and model
  -- calls, which would flood the progress stream clients poll.
  metrics jsonb not null default '{}'::jsonb
    check (jsonb_typeof(metrics) = 'object'),
  result jsonb,
  error_code text,
  error_message text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  -- A blank CAD part is designed from scratch before it has indexable
  -- features ('edit' vs. 'initial_design').
  workflow_mode text not null default 'edit'
    check (workflow_mode in ('edit', 'initial_design')),
  -- Preserves an explicit linked CAD target without marking it semantically
  -- resolved. reserve_requested_cad_edit_part() below promotes this into
  -- resolved_part_id on insert, once the part is not already reserved.
  requested_part_id uuid,
  -- Idempotent submission support (submit_cad_edit_job).
  client_request_id uuid,
  request_fingerprint text,
  last_event_sequence integer not null default 0
    check (last_event_sequence >= 0),
  -- The minimal history mechanism needed to chain geometry-check comparisons
  -- B->C, C->D, ... instead of always comparing against the original
  -- accepted source. Seeded from accepted_source_sha256 on the first
  -- geometry check for a job.
  last_checked_source_sha256 text,
  -- Candidate validation runs queued for this edit job. Uncapped, because the
  -- agent loop validates on every step completion. Written only by
  -- queue_edit_candidate_validation_run -- deliberately absent from
  -- patch_edit_job_owned's allowlist, since the only race-free place to
  -- allocate a run number is inside the RPC that already holds `for update`
  -- on the edit row.
  validation_run_count integer not null default 0
    check (validation_run_count >= 0),
  foreign key (project_id, resolved_part_id)
    references public.parts(project_id, id)
    on delete cascade,
  foreign key (project_id, requested_part_id)
    references public.parts(project_id, id)
    on delete cascade,
  check (
    accepted_source_sha256 is null
    or accepted_source_sha256 ~ '^[0-9a-f]{64}$'
  ),
  check (
    current_candidate_sha256 is null
    or current_candidate_sha256 ~ '^[0-9a-f]{64}$'
  ),
  check (
    request_fingerprint is null
    or request_fingerprint ~ '^[0-9a-f]{64}$'
  ),
  check (
    last_checked_source_sha256 is null
    or last_checked_source_sha256 ~ '^[0-9a-f]{64}$'
  )
);

comment on column public.edit_jobs.validation_run_count is
  'Candidate validation runs queued for this edit job. Uncapped, because the '
  'agent loop validates on every step completion. Written only by '
  'queue_edit_candidate_validation_run -- deliberately absent from '
  'patch_edit_job_owned''s allowlist, since the only race-free place to '
  'allocate a run number is inside the RPC that already holds `for update` on '
  'the edit row.';

comment on column public.edit_jobs.attempt_count is
  'Legacy commit-attempt counter, capped at 3. Once every worker is on the '
  'validation-run RPC nothing increments this; validation_run_count is the '
  'live counter. Retained because it is exposed on the public job contract.';

comment on column public.edit_jobs.current_candidate_sha256 is
  'The last candidate hash SUBMITTED for validation -- not the live candidate '
  'hash. The agent keeps editing the candidate while a validation child runs, '
  'so these differ routinely. queue_edit_candidate_validation_run''s callers '
  'depend on exactly this reading to decide whether to re-queue the current '
  'run or open the next one.';

create index edit_jobs_claim_idx
  on public.edit_jobs (created_at, id)
  where status in ('queued', 'running');

create index edit_jobs_project_status_idx
  on public.edit_jobs (project_id, status);

create unique index edit_jobs_one_active_per_part_idx
  on public.edit_jobs (project_id, resolved_part_id)
  where resolved_part_id is not null
    and status in ('queued', 'running');

create index edit_jobs_requested_part_idx
  on public.edit_jobs (project_id, requested_part_id)
  where requested_part_id is not null;

create unique index edit_jobs_client_request_id_idx
  on public.edit_jobs (client_request_id)
  where client_request_id is not null;

alter table public.edit_jobs enable row level security;

-- A linked part is known at admission time, even though Python remains
-- authoritative for blank-vs-established source classification. Reserve that
-- part on the queued row so multiple replicas do not spend model calls on jobs
-- that can never safely run together. The advisory lock gives concurrent
-- submissions a stable PART_EDIT_IN_PROGRESS outcome instead of a raw unique
-- index race.
create function public.reserve_requested_cad_edit_part()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.requested_part_id is null then
    return new;
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended(
      new.project_id::text || ':' || new.requested_part_id::text,
      0
    )
  );
  if exists (
    select 1
    from public.edit_jobs as active_job
    where active_job.project_id = new.project_id
      and active_job.resolved_part_id = new.requested_part_id
      and active_job.status in ('queued', 'running')
      and (
        new.client_request_id is null
        or active_job.client_request_id is distinct from new.client_request_id
      )
  ) then
    raise exception
      'PART_EDIT_IN_PROGRESS: another queued or running CAD edit already reserves this part.';
  end if;

  new.resolved_part_id := new.requested_part_id;
  return new;
end;
$$;

create trigger reserve_requested_cad_edit_part_before_insert
before insert on public.edit_jobs
for each row execute function public.reserve_requested_cad_edit_part();

-- A handful of functions below declare `public.generation_jobs`-typed
-- variables even though that table is not created until the next
-- migration file. Row-type variable declarations are resolved eagerly at
-- CREATE FUNCTION time (unlike ordinary table/function references in a
-- plpgsql body, which resolve lazily on first call), so
-- check_function_bodies must be off for the duration of this file. It is
-- restored at the end of the file; each migration file runs in its own
-- session, so this never leaks into later migrations.
set check_function_bodies = off;

create function public.submit_cad_edit_job(
  p_project_id uuid,
  p_request_text text,
  p_messages jsonb,
  p_requested_part_id uuid,
  p_workflow_mode text,
  p_client_request_id uuid,
  p_request_fingerprint text,
  p_resolved_targets jsonb default '[]'::jsonb
)
returns public.edit_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  existing_job public.edit_jobs;
  submitted_job public.edit_jobs;
  initial_part public.parts;
begin
  if p_request_text is null or btrim(p_request_text) = '' then
    raise exception 'A non-empty CAD request is required.';
  end if;
  if p_messages is null or jsonb_typeof(p_messages) <> 'array' then
    raise exception 'CAD request messages must be an array.';
  end if;
  if p_workflow_mode not in ('edit', 'initial_design') then
    raise exception 'Unsupported CAD edit workflow mode.';
  end if;
  if p_request_fingerprint is null
    or p_request_fingerprint !~ '^[0-9a-f]{64}$' then
    raise exception 'A SHA-256 request fingerprint is required.';
  end if;
  if p_resolved_targets is null
    or jsonb_typeof(p_resolved_targets) <> 'array' then
    raise exception 'Resolved targets must be an array.';
  end if;

  perform 1 from public.projects where id = p_project_id;
  if not found then
    raise exception 'Project % was not found.', p_project_id;
  end if;

  if p_requested_part_id is not null then
    select part.*
    into initial_part
    from public.parts as part
    where part.project_id = p_project_id
      and part.id = p_requested_part_id;
    if initial_part.id is null then
      raise exception 'Part % was not found in project %.',
        p_requested_part_id, p_project_id;
    end if;
    if initial_part.part_type <> 'cad' then
      raise exception 'CAD edit jobs may target only CAD parts.';
    end if;
  end if;

  if p_workflow_mode = 'initial_design' and p_requested_part_id is null then
    raise exception 'Initial design requires a requested CAD part.';
  end if;

  if p_client_request_id is not null then
    select job.*
    into existing_job
    from public.edit_jobs as job
    where job.client_request_id = p_client_request_id;

    if existing_job.id is not null then
      if existing_job.request_fingerprint <> p_request_fingerprint then
        raise exception
          'CLIENT_REQUEST_ID_CONFLICT: the client request ID is already bound to another payload.';
      end if;
      return existing_job;
    end if;
  end if;

  begin
    insert into public.edit_jobs (
      project_id,
      request_text,
      messages,
      requested_part_id,
      workflow_mode,
      resolved_part_id,
      resolved_targets,
      status,
      state,
      client_request_id,
      request_fingerprint,
      last_event_sequence
    )
    values (
      p_project_id,
      btrim(p_request_text),
      p_messages,
      p_requested_part_id,
      p_workflow_mode,
      case
        when p_workflow_mode = 'initial_design' then p_requested_part_id
        else null
      end,
      p_resolved_targets,
      'queued',
      'received',
      p_client_request_id,
      p_request_fingerprint,
      1
    )
    returning * into submitted_job;
  exception
    when unique_violation then
      if p_client_request_id is null then
        raise;
      end if;
      select job.*
      into existing_job
      from public.edit_jobs as job
      where job.client_request_id = p_client_request_id;
      if existing_job.id is null then
        raise;
      end if;
      if existing_job.request_fingerprint <> p_request_fingerprint then
        raise exception
          'CLIENT_REQUEST_ID_CONFLICT: the client request ID is already bound to another payload.';
      end if;
      return existing_job;
  end;

  insert into public.edit_job_events (
    edit_job_id,
    sequence,
    event_type,
    state,
    message,
    metadata
  )
  values (
    submitted_job.id,
    1,
    'job.queued',
    'received',
    'CAD edit request queued.',
    jsonb_build_object(
      'workflow_mode', submitted_job.workflow_mode,
      'part_id', submitted_job.requested_part_id
    )
  );

  return submitted_job;
end;
$$;

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

-- An expired lease cannot be revived by its former owner. If no replacement
-- has claimed the row yet, the normal claim RPC is still the only way to take
-- ownership again.
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
    and worker_id = btrim(p_worker_id)
    and lease_expires_at is not null
    and lease_expires_at >= now();

  get diagnostics updated_count = row_count;
  return updated_count = 1;
end;
$$;

-- State/checkpoint patches use the database clock and an explicit column
-- allowlist. This avoids host-clock skew in PostgREST filters and prevents a
-- generic JSON patch from changing queue ownership or immutable identity.
create function public.patch_edit_job_owned(
  p_edit_job_id uuid,
  p_worker_id text,
  p_patch jsonb
)
returns public.edit_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  updated_job public.edit_jobs;
  unknown_keys text[];
begin
  if p_worker_id is null or btrim(p_worker_id) = '' then
    raise exception 'A non-empty CAD editor worker ID is required.';
  end if;
  if p_patch is null or jsonb_typeof(p_patch) <> 'object' then
    raise exception 'CAD editor patches must be JSON objects.';
  end if;

  select array_agg(key order by key)
  into unknown_keys
  from jsonb_object_keys(p_patch) as keys(key)
  where key <> all (array[
    'state',
    'workflow_mode',
    'resolved_part_id',
    'resolved_targets',
    'accepted_source_sha256',
    'original_storage_path',
    'attempt_count',
    'validation_job_id',
    'index_job_id'
  ]::text[]);

  if unknown_keys is not null then
    raise exception 'Unsupported CAD editor patch keys: %.', unknown_keys;
  end if;

  update public.edit_jobs
  set
    state = case
      when p_patch ? 'state' then p_patch ->> 'state'
      else state
    end,
    workflow_mode = case
      when p_patch ? 'workflow_mode' then p_patch ->> 'workflow_mode'
      else workflow_mode
    end,
    resolved_part_id = case
      when p_patch ? 'resolved_part_id'
        then nullif(p_patch ->> 'resolved_part_id', '')::uuid
      else resolved_part_id
    end,
    resolved_targets = case
      when p_patch ? 'resolved_targets' then p_patch -> 'resolved_targets'
      else resolved_targets
    end,
    accepted_source_sha256 = case
      when p_patch ? 'accepted_source_sha256'
        then nullif(p_patch ->> 'accepted_source_sha256', '')
      else accepted_source_sha256
    end,
    original_storage_path = case
      when p_patch ? 'original_storage_path'
        then nullif(p_patch ->> 'original_storage_path', '')
      else original_storage_path
    end,
    attempt_count = case
      when p_patch ? 'attempt_count' then (p_patch ->> 'attempt_count')::integer
      else attempt_count
    end,
    validation_job_id = case
      when p_patch ? 'validation_job_id'
        then nullif(p_patch ->> 'validation_job_id', '')::uuid
      else validation_job_id
    end,
    index_job_id = case
      when p_patch ? 'index_job_id'
        then nullif(p_patch ->> 'index_job_id', '')::uuid
      else index_job_id
    end
  where id = p_edit_job_id
    and status = 'running'
    and worker_id = btrim(p_worker_id)
    and lease_expires_at is not null
    and lease_expires_at >= now()
  returning * into updated_job;

  if updated_job.id is null then
    raise exception 'EDIT_LEASE_LOST: CAD editor patch lost its lease.';
  end if;
  return updated_job;
end;
$$;

-- Persist private reasoning checkpoints atomically. The active lease owner is
-- the only process allowed to append; stale workers cannot overwrite a job
-- reclaimed by another editor instance.
create function public.append_edit_job_history_owned(
  p_edit_job_id uuid,
  p_worker_id text,
  p_event jsonb
)
returns public.edit_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  updated_job public.edit_jobs;
begin
  if p_worker_id is null or btrim(p_worker_id) = '' then
    raise exception 'A non-empty CAD editor worker ID is required.';
  end if;
  if p_event is null or jsonb_typeof(p_event) <> 'object' then
    raise exception 'CAD editor history events must be JSON objects.';
  end if;

  update public.edit_jobs
  set history = history || jsonb_build_array(
    p_event || jsonb_build_object('recorded_at', now())
  )
  where id = p_edit_job_id
    and status = 'running'
    and worker_id = btrim(p_worker_id)
    and lease_expires_at is not null
    and lease_expires_at >= now()
  returning * into updated_job;

  if updated_job.id is null then
    raise exception 'EDIT_LEASE_LOST: CAD editor history append lost its lease.';
  end if;
  return updated_job;
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

-- Child queue creation holds the edit row lock while it verifies the lease and
-- calls the existing idempotent queue RPC. A stale replica therefore cannot
-- replace validation/index/export child IDs after another worker takes over.
create function public.queue_edit_index_build_owned(
  p_edit_job_id uuid,
  p_worker_id text,
  p_state text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  owned_job public.edit_jobs;
begin
  select job.*
  into owned_job
  from public.edit_jobs as job
  where job.id = p_edit_job_id
  for update;

  if owned_job.id is null
    or owned_job.status <> 'running'
    or owned_job.worker_id <> btrim(p_worker_id)
    or owned_job.lease_expires_at is null
    or owned_job.lease_expires_at < now() then
    raise exception 'EDIT_LEASE_LOST: CAD editor index queue lost its lease.';
  end if;

  return public.queue_edit_index_build(p_edit_job_id, p_state);
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

create function public.queue_edit_candidate_validation_owned(
  p_edit_job_id uuid,
  p_worker_id text,
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
  owned_job public.edit_jobs;
begin
  select job.*
  into owned_job
  from public.edit_jobs as job
  where job.id = p_edit_job_id
  for update;

  if owned_job.id is null
    or owned_job.status <> 'running'
    or owned_job.worker_id <> btrim(p_worker_id)
    or owned_job.lease_expires_at is null
    or owned_job.lease_expires_at < now() then
    raise exception 'EDIT_LEASE_LOST: CAD editor validation queue lost its lease.';
  end if;

  return public.queue_edit_candidate_validation(
    p_edit_job_id,
    p_candidate_path,
    p_candidate_sha256,
    p_attempt_count
  );
end;
$$;

-- Queue one candidate validation run.
--
-- Differs from queue_edit_candidate_validation in three ways: it allocates
-- against validation_run_count instead of attempt_count, it has no upper cap,
-- and its storage path is validation-{run} rather than attempt-{n}. The
-- validator worker only requires the path to sit under the edit job's own
-- prefix and end in /model.py, and complete_candidate_cad_validation is
-- path-blind, so the shape is free to change.
create function public.queue_edit_candidate_validation_run(
  p_edit_job_id uuid,
  p_candidate_path text,
  p_candidate_sha256 text,
  p_validation_run integer
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  edit_job public.edit_jobs;
  validation_job public.generation_jobs;
  expected_path text;
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

  if p_candidate_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'Candidate source hash is invalid.';
  end if;

  -- The caller allocates the run number, because it must upload the snapshot
  -- object before the validator worker can claim the job. This bounds that
  -- allocation to "re-queue the current run, or open the next one" -- never
  -- rewind, never skip. Unlike attempt_count there is no upper bound: the
  -- agent loop legitimately validates once per step completion.
  if p_validation_run < 1
    or p_validation_run < edit_job.validation_run_count
    or p_validation_run > edit_job.validation_run_count + 1 then
    raise exception 'Candidate validation run is out of sequence.';
  end if;

  expected_path := edit_job.project_id::text
    || '/candidates/cad/'
    || edit_job.resolved_part_id::text
    || '/'
    || edit_job.id::text
    || '/validation-'
    || p_validation_run::text
    || '/model.py';

  if p_candidate_path <> expected_path then
    raise exception 'Candidate source path does not belong to the validation run.';
  end if;

  -- Idempotent reuse. A worker that crashed after queuing and then restarts
  -- with byte-identical content re-derives the same run number (its caller
  -- only opens a new run when the hash moved), re-queues the same
  -- (path, hash) pair, and gets the existing child back instead of a
  -- duplicate.
  --
  -- This deliberately reuses the child regardless of its status, so
  -- re-queuing an unchanged hash after a FAILED validation returns that same
  -- failed verdict immediately. That is correct -- the same bytes cannot
  -- produce a different verdict -- but it means a caller must never re-queue
  -- an unchanged hash expecting a fresh answer. The worker-side no-progress
  -- guard is what upholds that, so it is load-bearing rather than an
  -- optimization.
  --
  -- The type and edit_job_id predicates are new relative to the attempt-based
  -- RPC, which checked neither: validation_job_id is a plain uuid column, so
  -- without them a row belonging to another job -- or a geometry_check rather
  -- than a validation -- could be handed back as this job's verdict.
  if edit_job.validation_job_id is not null then
    select job.*
    into validation_job
    from public.generation_jobs as job
    where job.id = edit_job.validation_job_id;

    if validation_job.id is not null
      and validation_job.type = 'validate_cad'
      and validation_job.source_kind = 'candidate'
      and validation_job.edit_job_id = edit_job.id
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
    validation_run_count = p_validation_run,
    current_candidate_path = p_candidate_path,
    current_candidate_sha256 = p_candidate_sha256,
    validation_job_id = validation_job.id
  where id = edit_job.id;

  return validation_job.id;
end;
$$;

-- Lease-guarded entry point. Same shape as
-- queue_edit_candidate_validation_owned: the nested `for update` on the same
-- row inside one transaction is re-entrant.
create function public.queue_edit_candidate_validation_run_owned(
  p_edit_job_id uuid,
  p_worker_id text,
  p_candidate_path text,
  p_candidate_sha256 text,
  p_validation_run integer
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  owned_job public.edit_jobs;
begin
  select job.*
  into owned_job
  from public.edit_jobs as job
  where job.id = p_edit_job_id
  for update;

  if owned_job.id is null
    or owned_job.status <> 'running'
    or owned_job.worker_id <> btrim(p_worker_id)
    or owned_job.lease_expires_at is null
    or owned_job.lease_expires_at < now() then
    raise exception 'EDIT_LEASE_LOST: CAD editor validation queue lost its lease.';
  end if;

  return public.queue_edit_candidate_validation_run(
    p_edit_job_id,
    p_candidate_path,
    p_candidate_sha256,
    p_validation_run
  );
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

create function public.queue_edit_export_owned(
  p_edit_job_id uuid,
  p_worker_id text,
  p_source_sha256 text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  owned_job public.edit_jobs;
begin
  select job.*
  into owned_job
  from public.edit_jobs as job
  where job.id = p_edit_job_id
  for update;

  if owned_job.id is null
    or owned_job.status <> 'running'
    or owned_job.worker_id <> btrim(p_worker_id)
    or owned_job.lease_expires_at is null
    or owned_job.lease_expires_at < now() then
    raise exception 'EDIT_LEASE_LOST: CAD editor export queue lost its lease.';
  end if;

  return public.queue_edit_export(p_edit_job_id, p_source_sha256);
end;
$$;

-- The terminal row mutation and its public event commit in one transaction.
-- Clients can never observe job.completed/job.failed while the row remains
-- running, and a crash cannot publish a duplicate terminal event.
create function public.finalize_edit_job_owned(
  p_edit_job_id uuid,
  p_worker_id text,
  p_status text,
  p_result jsonb,
  p_error_code text,
  p_error_message text,
  p_event_message text,
  p_event_metadata jsonb,
  p_metrics jsonb default '{}'::jsonb
)
returns public.edit_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  owned_job public.edit_jobs;
  next_sequence integer;
begin
  if p_status not in ('completed', 'failed') then
    raise exception 'CAD editor terminal status must be completed or failed.';
  end if;
  if p_result is null or jsonb_typeof(p_result) <> 'object' then
    raise exception 'CAD editor terminal results must be JSON objects.';
  end if;
  if p_event_message is null
    or btrim(p_event_message) = ''
    or char_length(p_event_message) > 500 then
    raise exception 'Progress messages must contain 1 to 500 characters.';
  end if;
  if p_event_metadata is null or jsonb_typeof(p_event_metadata) <> 'object' then
    raise exception 'Progress metadata must be an object.';
  end if;
  if p_metrics is null or jsonb_typeof(p_metrics) <> 'object' then
    raise exception 'CAD editor run metrics must be an object.';
  end if;
  if p_status = 'failed'
    and (p_error_code is null or btrim(p_error_code) = '') then
    raise exception 'Failed CAD jobs require an error code.';
  end if;

  select job.*
  into owned_job
  from public.edit_jobs as job
  where job.id = p_edit_job_id
  for update;

  if owned_job.id is null
    or owned_job.status <> 'running'
    or owned_job.worker_id <> btrim(p_worker_id)
    or owned_job.lease_expires_at is null
    or owned_job.lease_expires_at < now() then
    raise exception 'EDIT_LEASE_LOST: CAD editor finalization lost its lease.';
  end if;

  next_sequence := owned_job.last_event_sequence + 1;
  insert into public.edit_job_events (
    edit_job_id,
    sequence,
    event_type,
    state,
    message,
    metadata
  )
  values (
    p_edit_job_id,
    next_sequence,
    case when p_status = 'completed' then 'job.completed' else 'job.failed' end,
    p_status,
    btrim(p_event_message),
    p_event_metadata
  );

  update public.edit_jobs
  set
    status = p_status,
    state = p_status,
    result = p_result,
    error_code = case when p_status = 'failed' then btrim(p_error_code) else null end,
    error_message = case
      when p_status = 'failed' then left(p_error_message, 4000)
      else null
    end,
    last_event_sequence = next_sequence,
    -- Written here rather than through patch_edit_job_owned because a run's
    -- totals are only known once it is over, and finalization is the one
    -- moment guaranteed to happen exactly once for both outcomes.
    metrics = p_metrics,
    lease_expires_at = null,
    completed_at = now()
  where id = p_edit_job_id
  returning * into owned_job;

  return owned_job;
end;
$$;

set check_function_bodies = on;

revoke all on function public.reserve_requested_cad_edit_part()
  from public, anon, authenticated, service_role;

revoke all on function public.submit_cad_edit_job(
  uuid, text, jsonb, uuid, text, uuid, text, jsonb
) from public, anon, authenticated;
revoke all on function public.claim_next_edit_job(text, integer)
  from public, anon, authenticated;
revoke all on function public.heartbeat_edit_job(uuid, text, integer)
  from public, anon, authenticated;
revoke all on function public.patch_edit_job_owned(uuid, text, jsonb)
  from public, anon, authenticated;
revoke all on function public.append_edit_job_history_owned(uuid, text, jsonb)
  from public, anon, authenticated;
revoke all on function public.queue_edit_index_build(uuid, text)
  from public, anon, authenticated;
revoke all on function public.queue_edit_index_build_owned(uuid, text, text)
  from public, anon, authenticated;
revoke all on function public.queue_edit_candidate_validation(
  uuid, text, text, integer
) from public, anon, authenticated;
revoke all on function public.queue_edit_candidate_validation_owned(
  uuid, text, text, text, integer
) from public, anon, authenticated;
revoke all on function public.queue_edit_candidate_validation_run(
  uuid, text, text, integer
) from public, anon, authenticated;
revoke all on function public.queue_edit_candidate_validation_run_owned(
  uuid, text, text, text, integer
) from public, anon, authenticated;
revoke all on function public.queue_edit_export(uuid, text)
  from public, anon, authenticated;
revoke all on function public.queue_edit_export_owned(uuid, text, text)
  from public, anon, authenticated;
revoke all on function public.finalize_edit_job_owned(
  uuid, text, text, jsonb, text, text, text, jsonb, jsonb
) from public, anon, authenticated;

-- Local Supabase installs default-grant EXECUTE on every new function in
-- schema public to service_role (see the defaclacl entries `pg_dump`
-- shows for role "postgres"), so the plain "revoke ... from public, anon,
-- authenticated" above is not enough on its own to keep service_role out
-- of the bare (non-"_owned") queueing RPCs -- it must be revoked from
-- service_role explicitly, same as the "_run" variant, which was never
-- granted to service_role in the first place.
revoke execute on function public.queue_edit_index_build(uuid, text)
  from service_role;
revoke execute on function public.queue_edit_candidate_validation(
  uuid, text, text, integer
) from service_role;
revoke execute on function public.queue_edit_candidate_validation_run(
  uuid, text, text, integer
) from service_role;
revoke execute on function public.queue_edit_export(uuid, text)
  from service_role;

-- Service-role callers must enter child queues through the lease-owning
-- wrappers, never through the bare (non-"_owned") RPCs. The wrappers remain
-- able to call these SECURITY DEFINER functions as their database owner.
grant execute on function public.submit_cad_edit_job(
  uuid, text, jsonb, uuid, text, uuid, text, jsonb
) to service_role;
grant execute on function public.claim_next_edit_job(text, integer)
  to service_role;
grant execute on function public.heartbeat_edit_job(uuid, text, integer)
  to service_role;
grant execute on function public.patch_edit_job_owned(uuid, text, jsonb)
  to service_role;
grant execute on function public.append_edit_job_history_owned(uuid, text, jsonb)
  to service_role;
grant execute on function public.queue_edit_index_build_owned(uuid, text, text)
  to service_role;
grant execute on function public.queue_edit_candidate_validation_owned(
  uuid, text, text, text, integer
) to service_role;
grant execute on function public.queue_edit_candidate_validation_run_owned(
  uuid, text, text, text, integer
) to service_role;
grant execute on function public.queue_edit_export_owned(uuid, text, text)
  to service_role;
grant execute on function public.finalize_edit_job_owned(
  uuid, text, text, jsonb, text, text, text, jsonb, jsonb
) to service_role;
