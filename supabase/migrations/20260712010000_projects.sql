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

alter table public.projects enable row level security;

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

revoke all on function public.find_project_by_name(text) from public, anon, authenticated;

grant execute on function public.find_project_by_name(text) to service_role;
