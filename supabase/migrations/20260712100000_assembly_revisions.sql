-- Immutable, content-digested snapshots of an AssemblySpec.
create table public.assembly_revisions (
  id uuid primary key default gen_random_uuid(),
  -- restrict, not cascade: an immutability trigger below rejects every
  -- UPDATE/DELETE on this table, including cascade-triggered ones. A
  -- cascade FK would mean deleting a project/assembly with any published
  -- revision fails with a raw, confusing trigger exception mid-cascade.
  -- restrict makes deletion fail cleanly and immediately at the FK layer
  -- instead, which cad-actions.service.ts then translates into a clear
  -- WorkflowError.
  assembly_id uuid not null references public.assemblies(id) on delete restrict,
  revision integer not null check (revision >= 1),
  parent_revision integer check (parent_revision is null or parent_revision >= 1),
  design_request_id uuid not null
    references public.project_planning_jobs(id) on delete restrict,
  schema_version integer not null check (schema_version > 0),
  definition_digest text not null check (definition_digest ~ '^[0-9a-f]{64}$'),
  definition_json jsonb not null check (jsonb_typeof(definition_json) = 'object'),
  created_at timestamptz not null default now(),
  -- One completed planning job publishes at most once, ever. Re-planning
  -- an edit produces a new design_request_id, so "edit and republish" is
  -- unaffected -- this only prevents an accidental double-publish of the
  -- exact same job from silently minting two revisions.
  unique (design_request_id),
  unique (assembly_id, revision),
  check (parent_revision is null or parent_revision < revision),
  check ((revision = 1) = (parent_revision is null)),
  foreign key (assembly_id, parent_revision)
    references public.assembly_revisions (assembly_id, revision)
);

create index assembly_revisions_assembly_idx
  on public.assembly_revisions (assembly_id, revision desc);
create index assembly_revisions_design_request_idx
  on public.assembly_revisions (design_request_id);

alter table public.assembly_revisions enable row level security;

-- Immutable once inserted. The trigger is authoritative (rejects
-- everything regardless of caller privilege); the revoke below is defense
-- in depth for the expected caller (service_role).
create function public.reject_assembly_revision_mutation()
returns trigger
language plpgsql
as $$
begin
  raise exception
    'assembly_revisions rows are immutable once published (id=%).', old.id;
end;
$$;

create trigger assembly_revisions_immutable
before update or delete on public.assembly_revisions
for each row execute function public.reject_assembly_revision_mutation();

-- The one write path for assemblies/assembly_revisions/assembly_part_bindings.
-- One transaction: locks/looks up the source planning job, locks (or
-- creates) the target assembly, computes the next revision number,
-- inserts the immutable revision, advances head_revision, and derives
-- assembly_part_bindings straight from definition_json's own nodes (not a
-- second, parallel parameter that could drift from what was persisted).
-- definition_digest is only ever regex-validated for shape here, never
-- recomputed -- always computed client-side, per this schema's convention.
create function public.publish_assembly_revision(
  p_project_id uuid,
  p_design_request_id uuid,
  p_assembly_id uuid,
  p_schema_version integer,
  p_definition_digest text,
  p_definition_json jsonb
)
returns public.assembly_revisions
language plpgsql
security definer
set search_path = public
as $$
declare
  planning_job public.project_planning_jobs;
  assembly public.assemblies;
  next_revision integer;
  parent integer;
  new_revision public.assembly_revisions;
  node jsonb;
begin
  if p_definition_digest !~ '^[0-9a-f]{64}$' then
    raise exception 'Definition digest is invalid.';
  end if;
  if p_definition_json is null or jsonb_typeof(p_definition_json) <> 'object' then
    raise exception 'Definition JSON must be a JSON object.';
  end if;

  select job.* into planning_job
  from public.project_planning_jobs as job
  where job.id = p_design_request_id
  for update;

  if planning_job.id is null then
    raise exception 'Project planning job % was not found.', p_design_request_id;
  end if;
  if planning_job.project_id <> p_project_id then
    raise exception
      'Project planning job % does not belong to project %.',
      p_design_request_id, p_project_id;
  end if;
  if planning_job.status <> 'completed' then
    raise exception 'Project planning job % is not completed.', p_design_request_id;
  end if;

  if p_assembly_id is null then
    insert into public.assemblies (project_id, head_revision)
    values (p_project_id, 0)
    returning * into assembly;
  else
    select a.* into assembly
    from public.assemblies as a
    where a.id = p_assembly_id
    for update;

    if assembly.id is null then
      raise exception 'Assembly % was not found.', p_assembly_id;
    end if;
    if assembly.project_id <> p_project_id then
      raise exception 'Assembly % does not belong to project %.', p_assembly_id, p_project_id;
    end if;
  end if;

  next_revision := assembly.head_revision + 1;
  parent := nullif(assembly.head_revision, 0);

  insert into public.assembly_revisions (
    assembly_id, revision, parent_revision, design_request_id,
    schema_version, definition_digest, definition_json
  )
  values (
    assembly.id, next_revision, parent, p_design_request_id,
    p_schema_version, p_definition_digest, p_definition_json
  )
  returning * into new_revision;

  update public.assemblies
  set head_revision = next_revision
  where id = assembly.id;

  for node in select * from jsonb_array_elements(p_definition_json -> 'nodes')
  loop
    if node -> 'binding' ->> 'mode' = 'existing' then
      insert into public.assembly_part_bindings (assembly_id, node_id, part_id)
      values (
        assembly.id,
        (node ->> 'node_id')::uuid,
        (node -> 'binding' ->> 'part_id')::uuid
      );
    end if;
  end loop;

  return new_revision;
end;
$$;

revoke all on function public.publish_assembly_revision(uuid, uuid, uuid, integer, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.publish_assembly_revision(uuid, uuid, uuid, integer, text, jsonb)
  to service_role;

revoke update, delete on public.assembly_revisions from service_role;
