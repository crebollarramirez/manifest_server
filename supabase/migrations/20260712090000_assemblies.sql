-- Canonical, immutably-revisioned persistence for AssemblySpec: assemblies
-- holds stable identity + a current head-revision pointer.
create table public.assemblies (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  -- Denormalized cache, not a FK: assembly_revisions.assembly_id already
  -- references this table, so the reverse relationship would be circular
  -- at table-creation time. publish_assembly_revision() is the only
  -- writer and keeps this consistent transactionally (locks this row with
  -- `for update` before computing the next revision number).
  head_revision integer not null default 0 check (head_revision >= 0),
  created_at timestamptz not null default now()
);

create index assemblies_project_idx on public.assemblies (project_id);

alter table public.assemblies enable row level security;

-- Just an input hint recorded at request time, not a source of truth for
-- anything -- assembly_revisions.assembly_id is. "on delete set null" (not
-- restrict/cascade) so deleting the assembly this pointed at never blocks
-- or cascades a historical planning job's own deletion.
alter table public.project_planning_jobs
  add column target_assembly_id uuid references public.assemblies(id) on delete set null;
