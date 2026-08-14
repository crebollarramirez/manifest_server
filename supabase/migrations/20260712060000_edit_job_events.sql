-- Durable, ordered public progress events for a CAD edit job.
create table public.edit_job_events (
  id uuid primary key default gen_random_uuid(),
  edit_job_id uuid not null references public.edit_jobs(id) on delete cascade,
  sequence integer not null check (sequence > 0),
  event_type text not null
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
        'validation.completed',
        'repair.started',
        'repair.appended',
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

alter table public.edit_job_events enable row level security;

-- Public progress uses a separate ordered journal, worker-owned so stale
-- replicas cannot publish events after lease takeover.
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

revoke all on function public.append_edit_job_event_owned(
  uuid, text, text, text, text, jsonb
) from public, anon, authenticated;
revoke all on function public.edit_job_events_after_cursors(jsonb, integer)
  from public, anon, authenticated;

grant execute on function public.append_edit_job_event_owned(
  uuid, text, text, text, text, jsonb
) to service_role;
grant execute on function public.edit_job_events_after_cursors(jsonb, integer)
  to service_role;
