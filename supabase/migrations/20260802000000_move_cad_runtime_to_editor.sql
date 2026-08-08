-- Move the durable CAD reasoning workflow to horizontally scalable Python
-- editor workers. NestJS remains the submission and progress-read control plane.

-- This is a drain-only cutover: stop request admission and all legacy CAD
-- workers before applying it. Old in-flight rows do not contain the Python
-- planning checkpoints required for safe takeover.
do $$
begin
  if exists (
    select 1 from public.edit_jobs where status in ('queued', 'running')
  ) or exists (
    select 1 from public.cad_tool_jobs where status in ('queued', 'running')
  ) then
    raise exception
      'CAD_EDITOR_CUTOVER_REQUIRES_DRAIN: finish or cancel all active CAD edit and tool jobs before applying this migration.';
  end if;
end;
$$;

alter table public.edit_jobs
  drop constraint if exists edit_jobs_state_check;

alter table public.edit_jobs
  add constraint edit_jobs_state_check
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
  );

alter table public.edit_job_events
  drop constraint if exists edit_job_events_event_type_check;

alter table public.edit_job_events
  add constraint edit_job_events_event_type_check
  check (
    event_type in (
      'job.queued',
      'job.started',
      'goal.started',
      'goal.completed',
      'plan.started',
      'plan.completed',
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
  );

-- An expired lease cannot be revived by its former owner. If no replacement
-- has claimed the row yet, the normal claim RPC is still the only way to take
-- ownership again.
create or replace function public.heartbeat_edit_job(
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

-- Public progress uses a separate ordered journal. This is the worker-owned
-- equivalent of append_edit_job_event and prevents stale replicas from
-- publishing events after lease takeover.
create function public.append_edit_job_event_owned(
  p_edit_job_id uuid,
  p_worker_id text,
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
  if p_worker_id is null or btrim(p_worker_id) = '' then
    raise exception 'A non-empty CAD editor worker ID is required.';
  end if;
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
    and status = 'running'
    and worker_id = btrim(p_worker_id)
    and lease_expires_at is not null
    and lease_expires_at >= now()
  returning last_event_sequence into next_sequence;

  if next_sequence is null then
    raise exception 'EDIT_LEASE_LOST: CAD editor event append lost its lease.';
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
  p_event_metadata jsonb
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
    lease_expires_at = null,
    completed_at = now()
  where id = p_edit_job_id
  returning * into owned_job;

  return owned_job;
end;
$$;

-- Relay progress for many subscriptions without using one shared cursor. Each
-- job receives its own bounded page so a noisy/high-cursor job cannot consume
-- the PostgREST row cap and starve another subscriber's backlog.
create function public.edit_job_events_after_cursors(
  p_cursors jsonb,
  p_limit_per_job integer default 100
)
returns setof public.edit_job_events
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  cursor_count integer;
begin
  if p_cursors is null or jsonb_typeof(p_cursors) <> 'object' then
    raise exception 'CAD progress cursors must be a JSON object.';
  end if;
  select count(*) into cursor_count from jsonb_object_keys(p_cursors);
  if cursor_count > 100 then
    raise exception 'CAD progress cursor batches may contain at most 100 jobs.';
  end if;
  if exists (
    select 1
    from jsonb_each_text(p_cursors) as cursor_entry(job_id, sequence)
    where job_id !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
      or sequence !~ '^[0-9]+$'
  ) then
    raise exception 'CAD progress cursors contain an invalid job ID or sequence.';
  end if;

  return query
  with cursors as (
    select
      cursor_entry.job_id::uuid as edit_job_id,
      cursor_entry.sequence::integer as after_sequence
    from jsonb_each_text(p_cursors) as cursor_entry(job_id, sequence)
  ),
  ranked as (
    select
      event.*,
      row_number() over (
        partition by event.edit_job_id
        order by event.sequence
      ) as page_row
    from public.edit_job_events as event
    join cursors as cursor
      on cursor.edit_job_id = event.edit_job_id
     and event.sequence > cursor.after_sequence
  )
  select
    ranked.id,
    ranked.edit_job_id,
    ranked.sequence,
    ranked.event_type,
    ranked.state,
    ranked.message,
    ranked.metadata,
    ranked.created_at
  from ranked
  where ranked.page_row <= least(greatest(p_limit_per_job, 1), 500)
  order by ranked.edit_job_id, ranked.sequence;
end;
$$;

revoke all on function public.append_edit_job_history_owned(uuid, text, jsonb)
  from public, anon, authenticated;
revoke all on function public.append_edit_job_event_owned(
  uuid, text, text, text, text, jsonb
) from public, anon, authenticated;
revoke all on function public.patch_edit_job_owned(uuid, text, jsonb)
  from public, anon, authenticated;
revoke all on function public.reserve_requested_cad_edit_part()
  from public, anon, authenticated, service_role;
revoke all on function public.queue_edit_index_build_owned(uuid, text, text)
  from public, anon, authenticated;
revoke all on function public.queue_edit_candidate_validation_owned(
  uuid, text, text, text, integer
) from public, anon, authenticated;
revoke all on function public.queue_edit_export_owned(uuid, text, text)
  from public, anon, authenticated;
revoke all on function public.finalize_edit_job_owned(
  uuid, text, text, jsonb, text, text, text, jsonb
) from public, anon, authenticated;
revoke all on function public.edit_job_events_after_cursors(jsonb, integer)
  from public, anon, authenticated;

grant execute on function public.append_edit_job_history_owned(uuid, text, jsonb)
  to service_role;
grant execute on function public.append_edit_job_event_owned(
  uuid, text, text, text, text, jsonb
) to service_role;
grant execute on function public.patch_edit_job_owned(uuid, text, jsonb)
  to service_role;
grant execute on function public.queue_edit_index_build_owned(uuid, text, text)
  to service_role;
grant execute on function public.queue_edit_candidate_validation_owned(
  uuid, text, text, text, integer
) to service_role;
grant execute on function public.queue_edit_export_owned(uuid, text, text)
  to service_role;
grant execute on function public.finalize_edit_job_owned(
  uuid, text, text, jsonb, text, text, text, jsonb
) to service_role;
grant execute on function public.edit_job_events_after_cursors(jsonb, integer)
  to service_role;

-- Service-role callers must enter child queues through the lease-owning
-- wrappers. The wrappers remain able to call these SECURITY DEFINER functions
-- as their database owner.
revoke execute on function public.queue_edit_index_build(uuid, text)
  from service_role;
revoke execute on function public.queue_edit_candidate_validation(
  uuid, text, text, integer
) from service_role;
revoke execute on function public.queue_edit_export(uuid, text)
  from service_role;

drop function if exists public.append_edit_job_event(
  uuid, text, text, text, jsonb
);

-- The nested Nest-to-Python execution queue has no runtime consumer after this
-- cutover. The drain guard above makes removal explicit instead of silently
-- stranding legacy queued rows.
drop function if exists public.queue_cad_tool_job(
  uuid, uuid, uuid, integer, text, jsonb
);
drop function if exists public.claim_next_cad_tool_job(text, integer);
drop function if exists public.heartbeat_cad_tool_job(uuid, text, integer);
drop function if exists public.complete_cad_tool_job(uuid, text, jsonb);
drop function if exists public.fail_cad_tool_job(uuid, text, text, text);
drop table public.cad_tool_jobs;
