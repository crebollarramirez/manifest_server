-- Project-scoped jobs for building and querying CAD source indexes.
create table public.index_jobs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  type text not null check (type in ('build_index', 'test_getter')),
  request_text text,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'completed', 'failed', 'cancelled')),
  result jsonb,
  error_message text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  check (
    (type = 'build_index' and request_text is null)
    or (
      type = 'test_getter'
      and request_text is not null
      and btrim(request_text) <> ''
    )
  )
);

create index index_jobs_queued_idx
  on public.index_jobs (created_at, id)
  where status = 'queued';

create index index_jobs_project_status_idx
  on public.index_jobs (project_id, status);

create unique index index_jobs_one_active_build_per_project_idx
  on public.index_jobs (project_id)
  where type = 'build_index' and status in ('queued', 'running');

alter table public.index_jobs enable row level security;

create function public.claim_next_index_job()
returns setof public.index_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed_job public.index_jobs;
begin
  select job.*
  into claimed_job
  from public.index_jobs as job
  where job.status = 'queued'
  order by job.created_at, job.id
  for update skip locked
  limit 1;

  if claimed_job.id is null then
    return;
  end if;

  update public.index_jobs
  set
    status = 'running',
    started_at = now(),
    completed_at = null,
    error_message = null
  where id = claimed_job.id
  returning * into claimed_job;

  return next claimed_job;
end;
$$;

revoke all on function public.claim_next_index_job()
  from public, anon, authenticated;
grant execute on function public.claim_next_index_job()
  to service_role;
