-- Preserve an explicit linked CAD target without marking it semantically resolved.
alter table public.edit_jobs
  add column requested_part_id uuid;

alter table public.edit_jobs
  add constraint edit_jobs_requested_part_fk
  foreign key (project_id, requested_part_id)
  references public.parts(project_id, id)
  on delete cascade;

create index edit_jobs_requested_part_idx
  on public.edit_jobs (project_id, requested_part_id)
  where requested_part_id is not null;