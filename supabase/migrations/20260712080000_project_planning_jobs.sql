-- Project-scoped, single-shot AI planning jobs (Project-Scoped AI Planner,
-- Step 1). One blocking LLM call produces a ProjectPlan, which is
-- deterministically validated and converted into an AssemblySpec. Unlike
-- edit_jobs, this table has no lease/heartbeat/worker_id columns and no
-- singleton-per-project constraint: it is not a resumable multi-step
-- workflow, and multiple concurrent planning runs per project are allowed.
--
-- target_assembly_id (an FK to public.assemblies) is added by
-- 20260712090000_assemblies.sql once that table exists.
create table public.project_planning_jobs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  request_text text not null check (btrim(request_text) <> ''),
  status text not null default 'queued'
    check (status in ('queued', 'running', 'completed', 'failed', 'cancelled')),
  project_plan jsonb
    check (project_plan is null or jsonb_typeof(project_plan) = 'object'),
  assembly_spec jsonb
    check (assembly_spec is null or jsonb_typeof(assembly_spec) = 'object'),
  error_code text,
  error_message text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  completed_at timestamptz,
  -- The project-planner repair loop produces a structured record of every
  -- validation violation found and every repair attempt made, so a failed
  -- job's full diagnostic detail is visible through get_project_plan, not
  -- just worker logs.
  error_details jsonb
    check (error_details is null or jsonb_typeof(error_details) = 'object'),
  -- Lets a caller opt out of the default "plan, then wait for an explicit
  -- /publish" workflow: when true, project_planner_worker.py publishes a
  -- revision inline, in the same job tick, right after the planning job is
  -- marked completed.
  auto_publish boolean not null default false,
  -- A completed job always has both artifacts; nothing else does.
  check (
    status <> 'completed'
    or (project_plan is not null and assembly_spec is not null)
  )
);

create index project_planning_jobs_queued_idx
  on public.project_planning_jobs (created_at, id)
  where status = 'queued';

create index project_planning_jobs_project_status_idx
  on public.project_planning_jobs (project_id, status);

alter table public.project_planning_jobs enable row level security;

create function public.claim_next_project_planning_job()
returns setof public.project_planning_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed_job public.project_planning_jobs;
begin
  select job.*
  into claimed_job
  from public.project_planning_jobs as job
  where job.status = 'queued'
  order by job.created_at, job.id
  for update skip locked
  limit 1;

  if claimed_job.id is null then
    return;
  end if;

  update public.project_planning_jobs
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

revoke all on function public.claim_next_project_planning_job()
  from public, anon, authenticated;
grant execute on function public.claim_next_project_planning_job()
  to service_role;
