-- Add Blender mesh exports without changing existing project and part records.
alter table public.generation_jobs
  add constraint generation_jobs_type_check
  check (type in ('export_cad', 'export_mesh')) not valid;

alter table public.generation_jobs
  validate constraint generation_jobs_type_check;

drop index if exists public.generation_jobs_queued_export_cad_idx;

create index generation_jobs_queued_export_idx
  on public.generation_jobs (created_at, id)
  where status = 'queued' and type in ('export_cad', 'export_mesh');

create function public.claim_next_supported_generation_job(
  p_job_types text[]
)
returns setof public.generation_jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed_job public.generation_jobs;
begin
  if p_job_types is null or cardinality(p_job_types) = 0 then
    return;
  end if;

  select job.*
  into claimed_job
  from public.generation_jobs as job
  where job.status = 'queued'
    and job.type = any(p_job_types)
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

revoke all on function public.claim_next_supported_generation_job(text[])
  from public, anon, authenticated;
grant execute on function public.claim_next_supported_generation_job(text[])
  to service_role;
