-- Add hash-bound CAD validation jobs and structured validation reports.
alter table public.generation_jobs
  drop constraint generation_jobs_type_check;

alter table public.generation_jobs
  add column source_sha256 text,
  add column result jsonb;

alter table public.generation_jobs
  add constraint generation_jobs_type_check
  check (type in ('export_cad', 'export_mesh', 'validate_cad')),
  add constraint generation_jobs_source_sha256_check
  check (source_sha256 is null or source_sha256 ~ '^[0-9a-f]{64}$'),
  add constraint generation_jobs_validation_hash_check
  check (type <> 'validate_cad' or source_sha256 is not null);

drop index if exists public.generation_jobs_queued_export_idx;

create index generation_jobs_queued_type_idx
  on public.generation_jobs (type, created_at, id)
  where status = 'queued'
    and type in ('export_cad', 'export_mesh', 'validate_cad');

create function public.complete_cad_validation_and_queue_export(
  p_validation_job_id uuid,
  p_source_sha256 text,
  p_result jsonb
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  validation_job public.generation_jobs;
  export_job_id uuid;
begin
  select job.*
  into validation_job
  from public.generation_jobs as job
  where job.id = p_validation_job_id
  for update;

  if validation_job.id is null then
    raise exception 'Validation job % was not found.', p_validation_job_id;
  end if;

  if validation_job.type <> 'validate_cad' then
    raise exception 'Job % is not a CAD validation job.', p_validation_job_id;
  end if;

  if validation_job.status <> 'running' then
    raise exception 'Validation job % is not running.', p_validation_job_id;
  end if;

  if validation_job.source_sha256 <> p_source_sha256 then
    raise exception 'Validation job source hash does not match.';
  end if;

  insert into public.generation_jobs (
    project_id,
    part_id,
    type,
    status,
    source_sha256
  )
  values (
    validation_job.project_id,
    validation_job.part_id,
    'export_cad',
    'queued',
    p_source_sha256
  )
  returning id into export_job_id;

  update public.generation_jobs
  set
    status = 'completed',
    error_message = null,
    result = coalesce(p_result, '{}'::jsonb) || jsonb_build_object(
      'export_job_id', export_job_id
    )
  where id = p_validation_job_id;

  return export_job_id;
end;
$$;

revoke all on function public.complete_cad_validation_and_queue_export(
  uuid,
  text,
  jsonb
) from public, anon, authenticated;
grant execute on function public.complete_cad_validation_and_queue_export(
  uuid,
  text,
  jsonb
) to service_role;
