-- A blank CAD part is designed from scratch before it has indexable features.
alter table public.edit_jobs
  add column workflow_mode text not null default 'edit'
    check (workflow_mode in ('edit', 'initial_design'));

alter table public.edit_jobs
  drop constraint if exists edit_jobs_state_check;

alter table public.edit_jobs
  add constraint edit_jobs_state_check
  check (
    state in (
      'received',
      'ensuring_index',
      'resolving_target',
      'retrieving_context',
      'planning_edit',
      'validating_plan',
      'applying_edit',
      'planning_initial_design',
      'planning_initial_repair',
      'applying_initial_design',
      'validating_candidate',
      'classifying_error',
      'retrieving_repair_context',
      'planning_repair',
      'applying_repair',
      'committing',
      'reindexing',
      'queueing_export',
      'completed',
      'failed',
      'cancelled'
    )
  );
