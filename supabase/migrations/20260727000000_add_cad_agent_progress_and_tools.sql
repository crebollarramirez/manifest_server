-- Add idempotent CAD edit submission, durable progress events, and a bounded
-- Python tool-execution queue for the NestJS CAD agent orchestrator.

alter table public.edit_jobs
  add column client_request_id uuid,
  add column request_fingerprint text,
  add column last_event_sequence integer not null default 0
    check (last_event_sequence >= 0);

alter table public.edit_jobs
  add constraint edit_jobs_request_fingerprint_check
  check (
    request_fingerprint is null
    or request_fingerprint ~ '^[0-9a-f]{64}$'
  );

create unique index edit_jobs_client_request_id_idx
  on public.edit_jobs (client_request_id)
  where client_request_id is not null;

create table public.edit_job_events (
  id uuid primary key default gen_random_uuid(),
  edit_job_id uuid not null references public.edit_jobs(id) on delete cascade,
  sequence integer not null check (sequence > 0),
  event_type text not null
    check (
      event_type in (
        'job.queued',
        'job.started',
        'indexing.started',
        'indexing.completed',
        'context.started',
        'context.completed',
        'planning.started',
        'planning.completed',
        'tools.started',
        'tools.completed',
        'validation.started',
        'validation.passed',
        'validation.failed',
        'repair.started',
        'repair.completed',
        'commit.started',
        'commit.completed',
        'reindex.started',
        'reindex.completed',
        'export.queued',
        'export.warning',
        'job.completed',
        'job.failed'
      )
    ),
  state text not null,
  message text not null check (
    btrim(message) <> ''
    and char_length(message) <= 500
  ),
  metadata jsonb not null default '{}'::jsonb
    check (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz not null default now(),
  unique (edit_job_id, sequence)
);

create index edit_job_events_replay_idx
  on public.edit_job_events (edit_job_id, sequence);

create table public.cad_tool_jobs (
  id uuid primary key default gen_random_uuid(),
  edit_job_id uuid not null references public.edit_jobs(id) on delete cascade,
  project_id uuid not null references public.projects(id) on delete cascade,
  part_id uuid references public.parts(id) on delete cascade,
  attempt integer not null check (attempt between 0 and 3),
  kind text not null
    check (
      kind in (
        'prepare_context',
        'apply_plan',
        'prepare_repair_context'
      )
    ),
  input jsonb not null check (jsonb_typeof(input) = 'object'),
  status text not null default 'queued'
    check (status in ('queued', 'running', 'completed', 'failed')),
  result jsonb,
  error_code text,
  error_message text,
  worker_id text,
  lease_expires_at timestamptz,
  heartbeat_at timestamptz,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  foreign key (project_id, part_id)
    references public.parts(project_id, id)
    on delete cascade,
  unique (edit_job_id, attempt, kind),
  check (
    (status = 'failed' and error_code is not null and error_message is not null)
    or status <> 'failed'
  )
);

create index cad_tool_jobs_claim_idx
  on public.cad_tool_jobs (created_at, id)
  where status in ('queued', 'running');

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

create function public.append_edit_job_event(
  p_edit_job_id uuid,
  p_event_type text,
  p_state text,
  p_message text,
  p_metadata jsonb default '{}'::jsonb
)
returns public.edit_job_events
language plpgsql
security definer
set search_path = public
as $$
declare
  next_sequence integer;
  appended_event public.edit_job_events;
begin
  if p_message is null
    or btrim(p_message) = ''
    or char_length(p_message) > 500 then
    raise exception 'Progress messages must contain 1 to 500 characters.';
  end if;
  if p_metadata is null or jsonb_typeof(p_metadata) <> 'object' then
    raise exception 'Progress metadata must be an object.';
  end if;

  update public.edit_jobs
  set last_event_sequence = last_event_sequence + 1
  where id = p_edit_job_id
  returning last_event_sequence into next_sequence;

  if next_sequence is null then
    raise exception 'Edit job % was not found.', p_edit_job_id;
  end if;

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
    p_event_type,
    p_state,
    btrim(p_message),
    p_metadata
  )
  returning * into appended_event;

  return appended_event;
end;
$$;

create function public.queue_cad_tool_job(
  p_edit_job_id uuid,
  p_project_id uuid,
  p_part_id uuid,
  p_attempt integer,
  p_kind text,
  p_input jsonb
)
returns public.cad_tool_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  existing_job public.cad_tool_jobs;
  queued_job public.cad_tool_jobs;
begin
  select job.*
  into existing_job
  from public.cad_tool_jobs as job
  where job.edit_job_id = p_edit_job_id
    and job.attempt = p_attempt
    and job.kind = p_kind;

  if existing_job.id is not null then
    if existing_job.project_id <> p_project_id
      or existing_job.part_id is distinct from p_part_id
      or existing_job.input <> p_input then
      raise exception 'Persisted CAD tool job does not match the requested work.';
    end if;
    return existing_job;
  end if;

  insert into public.cad_tool_jobs (
    edit_job_id,
    project_id,
    part_id,
    attempt,
    kind,
    input
  )
  values (
    p_edit_job_id,
    p_project_id,
    p_part_id,
    p_attempt,
    p_kind,
    p_input
  )
  returning * into queued_job;

  return queued_job;
end;
$$;

create function public.claim_next_cad_tool_job(
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns setof public.cad_tool_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed_job public.cad_tool_jobs;
  lease_seconds integer := greatest(coalesce(p_lease_seconds, 300), 30);
begin
  if p_worker_id is null or btrim(p_worker_id) = '' then
    raise exception 'A non-empty CAD tool worker ID is required.';
  end if;

  select job.*
  into claimed_job
  from public.cad_tool_jobs as job
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

  update public.cad_tool_jobs
  set
    status = 'running',
    worker_id = btrim(p_worker_id),
    started_at = coalesce(started_at, now()),
    heartbeat_at = now(),
    lease_expires_at = now() + make_interval(secs => lease_seconds),
    completed_at = null,
    error_code = null,
    error_message = null
  where id = claimed_job.id
  returning * into claimed_job;

  return next claimed_job;
end;
$$;

create function public.heartbeat_cad_tool_job(
  p_tool_job_id uuid,
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
  update public.cad_tool_jobs
  set
    heartbeat_at = now(),
    lease_expires_at = now() + make_interval(secs => lease_seconds)
  where id = p_tool_job_id
    and status = 'running'
    and worker_id = btrim(p_worker_id);

  get diagnostics updated_count = row_count;
  return updated_count = 1;
end;
$$;

create function public.complete_cad_tool_job(
  p_tool_job_id uuid,
  p_worker_id text,
  p_result jsonb
)
returns public.cad_tool_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  completed_job public.cad_tool_jobs;
begin
  update public.cad_tool_jobs
  set
    status = 'completed',
    result = coalesce(p_result, '{}'::jsonb),
    error_code = null,
    error_message = null,
    lease_expires_at = null,
    heartbeat_at = now(),
    completed_at = now()
  where id = p_tool_job_id
    and status = 'running'
    and worker_id = btrim(p_worker_id)
  returning * into completed_job;

  if completed_job.id is null then
    raise exception 'CAD tool job completion lost its lease.';
  end if;
  return completed_job;
end;
$$;

create function public.fail_cad_tool_job(
  p_tool_job_id uuid,
  p_worker_id text,
  p_error_code text,
  p_error_message text
)
returns public.cad_tool_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  failed_job public.cad_tool_jobs;
begin
  if p_error_code is null or btrim(p_error_code) = ''
    or p_error_message is null or btrim(p_error_message) = '' then
    raise exception 'CAD tool failures require a code and message.';
  end if;

  update public.cad_tool_jobs
  set
    status = 'failed',
    result = null,
    error_code = btrim(p_error_code),
    error_message = left(btrim(p_error_message), 1000),
    lease_expires_at = null,
    heartbeat_at = now(),
    completed_at = now()
  where id = p_tool_job_id
    and status = 'running'
    and worker_id = btrim(p_worker_id)
  returning * into failed_job;

  if failed_job.id is null then
    raise exception 'CAD tool job failure lost its lease.';
  end if;
  return failed_job;
end;
$$;

alter table public.edit_job_events enable row level security;
alter table public.cad_tool_jobs enable row level security;

revoke all on function public.submit_cad_edit_job(
  uuid, text, jsonb, uuid, text, uuid, text, jsonb
) from public, anon, authenticated;
revoke all on function public.append_edit_job_event(
  uuid, text, text, text, jsonb
) from public, anon, authenticated;
revoke all on function public.queue_cad_tool_job(
  uuid, uuid, uuid, integer, text, jsonb
) from public, anon, authenticated;
revoke all on function public.claim_next_cad_tool_job(text, integer)
  from public, anon, authenticated;
revoke all on function public.heartbeat_cad_tool_job(uuid, text, integer)
  from public, anon, authenticated;
revoke all on function public.complete_cad_tool_job(uuid, text, jsonb)
  from public, anon, authenticated;
revoke all on function public.fail_cad_tool_job(uuid, text, text, text)
  from public, anon, authenticated;

grant execute on function public.submit_cad_edit_job(
  uuid, text, jsonb, uuid, text, uuid, text, jsonb
) to service_role;
grant execute on function public.append_edit_job_event(
  uuid, text, text, text, jsonb
) to service_role;
grant execute on function public.queue_cad_tool_job(
  uuid, uuid, uuid, integer, text, jsonb
) to service_role;
grant execute on function public.claim_next_cad_tool_job(text, integer)
  to service_role;
grant execute on function public.heartbeat_cad_tool_job(uuid, text, integer)
  to service_role;
grant execute on function public.complete_cad_tool_job(uuid, text, jsonb)
  to service_role;
grant execute on function public.fail_cad_tool_job(uuid, text, text, text)
  to service_role;
