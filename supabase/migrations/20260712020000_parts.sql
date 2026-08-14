-- Project-scoped CAD/mesh parts.
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

alter table public.parts enable row level security;

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

revoke all on function public.find_part_by_name(uuid, text) from public, anon, authenticated;

grant execute on function public.find_part_by_name(uuid, text) to service_role;
