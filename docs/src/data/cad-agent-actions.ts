export interface CadAgentAction {
  action: string;
  category: "Projects" | "Parts" | "Jobs and status" | "Chat";
  description: string;
  requestExample: string;
  responseExample: string;
  notes?: string;
}

const projectId = "11111111-1111-4111-8111-111111111111";
const partId = "22222222-2222-4222-8222-222222222222";
const jobId = "33333333-3333-4333-8333-333333333333";
const clientRequestId = "55555555-5555-4555-8555-555555555555";

export const cadAgentActions: CadAgentAction[] = [
  {
    action: "create_project",
    category: "Projects",
    description: "Creates a uniquely named project and returns its durable identifier.",
    requestExample: JSON.stringify({
      action: "create_project",
      project_name: "Desk Mount",
    }, null, 2),
    responseExample: JSON.stringify({
      message: 'Created and linked project "Desk Mount".',
      status: "created",
      project: { id: projectId, project_name: "Desk Mount" },
    }, null, 2),
  },
  {
    action: "link_project",
    category: "Projects",
    description: "Finds one existing project by name and returns the record for client-side linking.",
    requestExample: JSON.stringify({
      action: "link_project",
      project_name: "Desk Mount",
    }, null, 2),
    responseExample: JSON.stringify({
      message: 'Linked project "Desk Mount".',
      status: "linked",
      project: { id: projectId, project_name: "Desk Mount" },
    }, null, 2),
  },
  {
    action: "list_projects",
    category: "Projects",
    description: "Lists every project ordered by project name.",
    requestExample: JSON.stringify({ action: "list_projects" }, null, 2),
    responseExample: JSON.stringify({
      message: "Projects:\n- Desk Mount",
      status: "listed",
      projects: [{ id: projectId, project_name: "Desk Mount" }],
    }, null, 2),
  },
  {
    action: "delete_project",
    category: "Projects",
    description: "Cancels applicable queued work, blocks on running work, removes the project storage prefix, and deletes the project row.",
    requestExample: JSON.stringify({
      action: "delete_project",
      project_name: "Desk Mount",
    }, null, 2),
    responseExample: JSON.stringify({
      message: 'Deleted project "Desk Mount" and all of its parts.',
      status: "deleted",
      project: { id: projectId, project_name: "Desk Mount" },
    }, null, 2),
    notes: "Returns HTTP 409 instead when an applicable index, edit, validation, or export job is running.",
  },
  {
    action: "create_part",
    category: "Parts",
    description: "Creates a CAD or mesh part and initializes its model.py and params.json storage objects. CAD creation also queues a project index build.",
    requestExample: JSON.stringify({
      action: "create_part",
      project_id: projectId,
      part_name: "Bracket",
      part_type: "cad",
    }, null, 2),
    responseExample: JSON.stringify({
      message: 'Created and linked cad part "Bracket".',
      status: "created",
      part: {
        id: partId,
        project_id: projectId,
        part_name: "Bracket",
        part_type: "cad",
      },
      index_job_id: jobId,
      index_status: "queued",
      index_job_type: "build_index",
      warnings: [],
    }, null, 2),
    notes: "If automatic indexing cannot be queued, CAD creation still succeeds with index_job_id null, index_status not_queued, and a warning to call index_project. Mesh responses do not include index metadata.",
  },
  {
    action: "link_part",
    category: "Parts",
    description: "Finds one named part inside the selected project and returns its record.",
    requestExample: JSON.stringify({
      action: "link_part",
      project_id: projectId,
      part_name: "Bracket",
    }, null, 2),
    responseExample: JSON.stringify({
      message: 'Linked cad part "Bracket".',
      status: "linked",
      part: {
        id: partId,
        project_id: projectId,
        part_name: "Bracket",
        part_type: "cad",
      },
    }, null, 2),
  },
  {
    action: "list_parts",
    category: "Parts",
    description: "Lists every part in a project, ordered by part name.",
    requestExample: JSON.stringify({
      action: "list_parts",
      project_id: projectId,
    }, null, 2),
    responseExample: JSON.stringify({
      message: 'Parts in "Desk Mount":\n- Bracket [cad] id=' + partId,
      status: "listed",
      project: { id: projectId, project_name: "Desk Mount" },
      parts: [{
        id: partId,
        project_id: projectId,
        part_name: "Bracket",
        part_type: "cad",
      }],
    }, null, 2),
  },
  {
    action: "delete_part",
    category: "Parts",
    description: "Cancels applicable queued work, blocks on running work, removes source/export/candidate prefixes, and deletes the part row.",
    requestExample: JSON.stringify({
      action: "delete_part",
      project_id: projectId,
      part_name: "Bracket",
    }, null, 2),
    responseExample: JSON.stringify({
      message: 'Deleted cad part "Bracket".',
      status: "deleted",
      part: {
        id: partId,
        project_id: projectId,
        part_name: "Bracket",
        part_type: "cad",
      },
    }, null, 2),
    notes: "Returns HTTP 409 when an applicable edit, validation, or export job is running.",
  },
  {
    action: "export_part",
    category: "Jobs and status",
    description: "Queues export_cad for a CAD part or export_mesh for a mesh part. CAD jobs include the current model.py SHA-256.",
    requestExample: JSON.stringify({
      action: "export_part",
      part_id: partId,
    }, null, 2),
    responseExample: JSON.stringify({
      message: 'Queued export_cad for cad part "Bracket". Job: ' + jobId,
      status: "queued",
      job_type: "export_cad",
      project_id: projectId,
      part_id: partId,
      job_id: jobId,
    }, null, 2),
  },
  {
    action: "validate_part",
    category: "Jobs and status",
    description: "Queues a hash-bound validate_cad generation job. Mesh parts are rejected.",
    requestExample: JSON.stringify({
      action: "validate_part",
      part_id: partId,
    }, null, 2),
    responseExample: JSON.stringify({
      message: 'Queued validate_cad for cad part "Bracket". Job: ' + jobId,
      status: "validating",
      job_type: "validate_cad",
      project_id: projectId,
      part_id: partId,
      job_id: jobId,
    }, null, 2),
  },
  {
    action: "index_project",
    category: "Jobs and status",
    description: "Queues a full build_index job for a project containing CAD parts, or returns the existing active build.",
    requestExample: JSON.stringify({
      action: "index_project",
      project_id: projectId,
    }, null, 2),
    responseExample: JSON.stringify({
      message: 'Index job for project "Desk Mount" is queued. Job: ' + jobId,
      status: "queued",
      job_type: "build_index",
      project_id: projectId,
      job_id: jobId,
    }, null, 2),
  },
  {
    action: "test_index",
    category: "Jobs and status",
    description: "Queues a read-only test_getter job against the current project index.",
    requestExample: JSON.stringify({
      action: "test_index",
      project_id: projectId,
      request_text: "find mounting holes",
    }, null, 2),
    responseExample: JSON.stringify({
      message: 'Queued index Getter test for project "Desk Mount". Job: ' + jobId,
      status: "queued",
      job_type: "test_getter",
      project_id: projectId,
      job_id: jobId,
    }, null, 2),
  },
  {
    action: "get_index_job",
    category: "Jobs and status",
    description: "Reads one index job by project and job ID without advancing it.",
    requestExample: JSON.stringify({
      action: "get_index_job",
      project_id: projectId,
      job_id: jobId,
    }, null, 2),
    responseExample: JSON.stringify({
      message: `Index job ${jobId} is completed.`,
      status: "completed",
      job: {
        id: jobId,
        project_id: projectId,
        type: "build_index",
        request_text: null,
        status: "completed",
        result: { artifact_path: `${projectId}/index/semantic_index.json` },
        error_message: null,
        created_at: "2026-07-26T10:00:00Z",
        started_at: "2026-07-26T10:00:01Z",
        completed_at: "2026-07-26T10:00:03Z",
      },
    }, null, 2),
  },
  {
    action: "get_edit_job",
    category: "Jobs and status",
    description: "Reads durable edit state and ordered public progress after an optional acknowledged sequence.",
    requestExample: JSON.stringify({
      action: "get_edit_job",
      job_id: jobId,
      after_sequence: 3,
    }, null, 2),
    responseExample: JSON.stringify({
      message: `CAD edit job ${jobId} is running (validating_candidate).`,
      status: "running",
      job: {
        id: jobId,
        client_request_id: clientRequestId,
        project_id: projectId,
        requested_part_id: partId,
        workflow_mode: "edit",
        resolved_part_id: partId,
        resolved_targets: [],
        status: "running",
        state: "validating_candidate",
        attempt_count: 1,
        max_attempts: 3,
        validation_job_id: "44444444-4444-4444-8444-444444444444",
        index_job_id: null,
        export_job_id: null,
        history: [],
        result: null,
        error_code: null,
        error_message: null,
        last_event_sequence: 4,
      },
      events: [{
        sequence: 4,
        event_type: "validating",
        state: "validating_candidate",
        message: "Validating candidate attempt 1.",
        metadata: { attempt: 1 },
      }],
    }, null, 2),
    notes: "The Nest GET endpoint exposes the same durable status/replay pattern at /v1/cad-edits/:jobId?after_sequence=3. WebSocket subscriptions use the last acknowledged sequence to recover missed events.",
  },
  {
    action: "chat",
    category: "Chat",
    description: "Submits CAD work through the idempotent durable edit-job contract. A linked blank part uses initial_design; established linked work remains bound to that part; unlinked work requires an unambiguous project-wide target.",
    requestExample: JSON.stringify({
      action: "chat",
      client_request_id: clientRequestId,
      project_id: projectId,
      part_id: partId,
      messages: [
        { role: "user", content: "Make the mounting holes deeper" },
      ],
    }, null, 2),
    responseExample: JSON.stringify({
      message: 'Queued a project-scoped CAD edit for "Desk Mount". Job: ' + jobId,
      status: "queued",
      job_type: "edit_cad",
      project_id: projectId,
      part_id: partId,
      job_id: jobId,
      client_request_id: clientRequestId,
    }, null, 2),
    notes: "Repeating the same client_request_id and request returns the existing job; reusing it for different content returns 409. The Nest API also accepts direct POST /v1/cad-edits and WebSocket submit messages. Linked mesh chat keeps its existing synchronous generation/export path.",
  },
];
