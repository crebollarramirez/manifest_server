-- Development schema for project-scoped CAD generation and exports.
insert into storage.buckets (id, name, public)
values ('3dProjects', '3dProjects', false)
on conflict (id) do nothing;

create table public.projects (
  id uuid primary key default gen_random_uuid(),
  project_name text not null check (btrim(project_name) <> ''),
  created_at timestamptz not null default now()
);

create unique index projects_project_name_ci_idx
  on public.projects (lower(btrim(project_name)));

create table public.parts (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  part_name text not null check (btrim(part_name) <> ''),
  part_type text not null check (part_type in ('cad', 'mesh')),
  created_at timestamptz not null default now(),
  unique (project_id, id)
);

create unique index parts_project_id_part_name_ci_idx
  on public.parts (project_id, lower(btrim(part_name)));

create table public.generation_jobs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  part_id uuid not null,
  type text not null,
  status text not null default 'queued'
    check (status in ('queued', 'running', 'completed', 'failed', 'cancelled')),
  error_message text,
  created_at timestamptz not null default now(),
  foreign key (project_id, part_id)
    references public.parts(project_id, id)
    on delete cascade
);

create index generation_jobs_queued_export_cad_idx
  on public.generation_jobs (created_at)
  where status = 'queued' and type = 'export_cad';

create index generation_jobs_project_part_status_idx
  on public.generation_jobs (project_id, part_id, status);

create function public.find_project_by_name(p_project_name text)
returns setof public.projects
language sql
stable
security definer
set search_path = public
as $$
  select project
  from public.projects as project
  where lower(btrim(project.project_name)) = lower(btrim(p_project_name))
  limit 1;
$$;

create function public.find_part_by_name(
  p_project_id uuid,
  p_part_name text
)
returns setof public.parts
language sql
stable
security definer
set search_path = public
as $$
  select part
  from public.parts as part
  where part.project_id = p_project_id
    and lower(btrim(part.part_name)) = lower(btrim(p_part_name))
  limit 1;
$$;

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

alter table public.projects enable row level security;
alter table public.parts enable row level security;
alter table public.generation_jobs enable row level security;

revoke all on function public.find_project_by_name(text) from public, anon, authenticated;
revoke all on function public.find_part_by_name(uuid, text) from public, anon, authenticated;
revoke all on function public.claim_next_generation_job(text) from public, anon, authenticated;

grant execute on function public.find_project_by_name(text) to service_role;
grant execute on function public.find_part_by_name(uuid, text) to service_role;
grant execute on function public.claim_next_generation_job(text) to service_role;
