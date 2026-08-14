-- The live, mutable pointer from an abstract node (identified by node_id,
-- which only exists inside a frozen definition_json blob -- nothing
-- relational to FK node_id against) to a concrete parts row. Only
-- binding.mode="existing" nodes get a row here at publish time. Deliberately
-- decoupled from the frozen revision content so a future part-materializing
-- coordinator can update it without minting a new revision.
create table public.assembly_part_bindings (
  assembly_id uuid not null references public.assemblies(id) on delete cascade,
  node_id uuid not null,
  part_id uuid not null references public.parts(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (assembly_id, node_id)
);

create index assembly_part_bindings_part_idx
  on public.assembly_part_bindings (part_id);

alter table public.assembly_part_bindings enable row level security;
