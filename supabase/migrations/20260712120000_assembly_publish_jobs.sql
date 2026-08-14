-- The async queue a human/CLI uses to canonicalize + persist a completed
-- project_planning_jobs row as a new AssemblyRevision.
create table public.assembly_publish_jobs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  design_request_id uuid not null
    references public.project_planning_jobs(id) on delete cascade,
  target_assembly_id uuid references public.assemblies(id) on delete cascade,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'completed', 'failed', 'cancelled')),
  assembly_revision jsonb
    check (assembly_revision is null or jsonb_typeof(assembly_revision) = 'object'),
  error_code text,
  error_message text,
  error_details jsonb
    check (error_details is null or jsonb_typeof(error_details) = 'object'),
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  check (status <> 'completed' or assembly_revision is not null)
);

create index assembly_publish_jobs_queued_idx
  on public.assembly_publish_jobs (created_at, id)
  where status = 'queued';

create index assembly_publish_jobs_project_status_idx
  on public.assembly_publish_jobs (project_id, status);

create unique index assembly_publish_jobs_one_active_per_assembly_idx
  on public.assembly_publish_jobs (target_assembly_id)
  where status in ('queued', 'running') and target_assembly_id is not null;

create unique index assembly_publish_jobs_one_active_per_design_request_idx
  on public.assembly_publish_jobs (design_request_id)
  where status in ('queued', 'running');

alter table public.assembly_publish_jobs enable row level security;

create function public.claim_next_assembly_publish_job()
returns setof public.assembly_publish_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed_job public.assembly_publish_jobs;
begin
  select job.*
  into claimed_job
  from public.assembly_publish_jobs as job
  where job.status = 'queued'
  order by job.created_at, job.id
  for update skip locked
  limit 1;

  if claimed_job.id is null then
    return;
  end if;

  update public.assembly_publish_jobs
  set
    status = 'running',
    started_at = now(),
    completed_at = null,
    error_code = null,
    error_message = null
  where id = claimed_job.id
  returning * into claimed_job;

  return next claimed_job;
end;
$$;

revoke all on function public.claim_next_assembly_publish_job()
  from public, anon, authenticated;
grant execute on function public.claim_next_assembly_publish_job()
  to service_role;
