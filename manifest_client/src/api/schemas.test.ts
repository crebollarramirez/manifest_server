import { describe, expect, it } from "vitest";
import { ApiError, apiErrorFromStatus } from "./errors";
import { createFixtureClient } from "./fixtureClient";
import {
  EDIT_JOB_STATES,
  chatResponseSchema,
  editJobStateSchema,
  getEditJobResponseSchema,
  partRecordSchema,
} from "./schemas";
import {
  FIXTURE_BLANK_PART_ID,
  FIXTURE_CAD_PART_ID,
  FIXTURE_MESH_PART_ID,
  FIXTURE_PROJECT_ID,
  FIXTURE_PROJECT_NAME,
} from "./fixtureIds";

/**
 * These tests encode the contract (CONTRACT.md + Amendments) mechanically:
 * schema strictness, the state enum, error taxonomy, part-identity and
 * idempotency conventions, and the full fixture chat -> edit -> export walk.
 */

describe("schemas", () => {
  it("edit_jobs.state enum matches the migration exactly (Amendment A5)", () => {
    expect(EDIT_JOB_STATES).toHaveLength(21);
    expect(EDIT_JOB_STATES).toContain("planning_initial_repair");
    expect(editJobStateSchema.safeParse("resolving_target").success).toBe(true);
    expect(editJobStateSchema.safeParse("not_a_state").success).toBe(false);
  });

  it("rejects malformed part records loudly", () => {
    expect(
      partRecordSchema.safeParse({
        id: "not-a-uuid",
        project_id: FIXTURE_PROJECT_ID,
        part_name: "x",
        part_type: "cad",
      }).success,
    ).toBe(false);
    expect(
      partRecordSchema.safeParse({
        id: FIXTURE_CAD_PART_ID,
        project_id: FIXTURE_PROJECT_ID,
        part_name: "x",
        part_type: "solid", // unknown part_type
      }).success,
    ).toBe(false);
  });

  it("rejects an edit job carrying an unknown state", () => {
    const result = getEditJobResponseSchema.safeParse({
      message: "m",
      status: "running",
      job: { id: FIXTURE_CAD_PART_ID, state: "brand_new_state" },
      events: [],
    });
    expect(result.success).toBe(false);
  });

  it("chat responses discriminate on job_type; mesh has no client_request_id (A2)", () => {
    const mesh = chatResponseSchema.parse({
      message: "m",
      status: "queued",
      job_type: "export_mesh",
      project_id: FIXTURE_PROJECT_ID,
      part_id: FIXTURE_MESH_PART_ID,
      job_id: FIXTURE_CAD_PART_ID,
    });
    expect(mesh.job_type).toBe("export_mesh");
    expect("client_request_id" in mesh).toBe(false);

    // edit_cad REQUIRES client_request_id.
    expect(
      chatResponseSchema.safeParse({
        message: "m",
        status: "queued",
        job_type: "edit_cad",
        project_id: FIXTURE_PROJECT_ID,
        part_id: null,
        job_id: FIXTURE_CAD_PART_ID,
      }).success,
    ).toBe(false);
  });
});

describe("error taxonomy", () => {
  it("maps status codes to kinds", () => {
    expect(apiErrorFromStatus(400, "x").kind).toBe("validation");
    expect(apiErrorFromStatus(404, "x").kind).toBe("not_found");
    expect(apiErrorFromStatus(409, "x").kind).toBe("conflict");
    expect(apiErrorFromStatus(500, "x").kind).toBe("backend");
  });
});

describe("fixture client (contract behaviors)", () => {
  it("lists the seeded project and parts, sorted by name", async () => {
    const client = createFixtureClient();
    const projects = await client.listProjects();
    expect(projects.projects.map((p) => p.project_name)).toContain(
      FIXTURE_PROJECT_NAME,
    );
    const parts = await client.listParts(FIXTURE_PROJECT_ID);
    expect(parts.parts).toHaveLength(4);
    const names = parts.parts.map((p) => p.part_name);
    expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b)));
  });

  it("runs the full initial-design walk: chat -> edit job -> export -> artifacts", async () => {
    const client = createFixtureClient();
    const chat = await client.chat({
      projectId: FIXTURE_PROJECT_ID,
      partId: FIXTURE_BLANK_PART_ID,
      messages: [{ role: "user", content: "design a hook" }],
    });
    expect(chat.job_type).toBe("initial_cad_design");
    // Amendment A1: the sent part id is echoed back.
    expect(chat.part_id).toBe(FIXTURE_BLANK_PART_ID);

    let lastSequence = 0;
    let editJob = await client.getEditJob(chat.job_id, lastSequence);
    for (let poll = 0; poll < 30 && editJob.status !== "completed"; poll += 1) {
      expect(editJob.events.every((e) => e.sequence > lastSequence)).toBe(true);
      lastSequence = editJob.job.last_event_sequence;
      editJob = await client.getEditJob(chat.job_id, lastSequence);
    }
    expect(editJob.status).toBe("completed");
    expect(editJob.job.resolved_part_id).toBe(FIXTURE_BLANK_PART_ID);
    const exportJobId = editJob.job.export_job_id;
    expect(exportJobId).not.toBeNull();

    let exportJob = await client.getExportJob(exportJobId as string);
    for (let poll = 0; poll < 10 && exportJob.status !== "completed"; poll += 1) {
      exportJob = await client.getExportJob(exportJobId as string);
    }
    expect(exportJob.status).toBe("completed");
    expect(exportJob.job.source_sha256).toMatch(/^[0-9a-f]{64}$/);
    const files = (exportJob.artifacts ?? []).map((a) => a.file);
    expect(files).toContain("model.stl");
  });

  it("is idempotent on client_request_id for CAD chats", async () => {
    const client = createFixtureClient();
    const requestId = crypto.randomUUID();
    const first = await client.chat({
      projectId: FIXTURE_PROJECT_ID,
      clientRequestId: requestId,
      messages: [{ role: "user", content: "widen the bracket" }],
    });
    const second = await client.chat({
      projectId: FIXTURE_PROJECT_ID,
      clientRequestId: requestId,
      messages: [{ role: "user", content: "widen the bracket" }],
    });
    expect(second.job_id).toBe(first.job_id);
    // Project-scoped chat: part_id is null in the response (Amendment A1).
    expect(first.part_id).toBeNull();
  });

  it("rejects a part-scoped chat while that part has an active edit (A3)", async () => {
    const client = createFixtureClient();
    await client.chat({
      projectId: FIXTURE_PROJECT_ID,
      partId: FIXTURE_CAD_PART_ID,
      messages: [{ role: "user", content: "add a fillet" }],
    });
    await expect(
      client.chat({
        projectId: FIXTURE_PROJECT_ID,
        partId: FIXTURE_CAD_PART_ID,
        messages: [{ role: "user", content: "add a chamfer" }],
      }),
    ).rejects.toMatchObject({ kind: "conflict", status: 409 });
  });

  it("blocks deletion while an edit is running (409), like prepareForDeletion", async () => {
    const client = createFixtureClient();
    const chat = await client.chat({
      projectId: FIXTURE_PROJECT_ID,
      partId: FIXTURE_CAD_PART_ID,
      messages: [{ role: "user", content: "add a rib" }],
    });
    await client.getEditJob(chat.job_id); // queued -> running
    await expect(
      client.deleteProject(FIXTURE_PROJECT_NAME),
    ).rejects.toMatchObject({ kind: "conflict", status: 409 });
  });

  it("mesh chat responds synchronously with a queued export job", async () => {
    const client = createFixtureClient();
    const chat = await client.chat({
      projectId: FIXTURE_PROJECT_ID,
      partId: FIXTURE_MESH_PART_ID,
      messages: [{ role: "user", content: "make the hull longer" }],
    });
    expect(chat.job_type).toBe("export_mesh");
    let exportJob = await client.getExportJob(chat.job_id);
    for (let poll = 0; poll < 10 && exportJob.status !== "completed"; poll += 1) {
      exportJob = await client.getExportJob(chat.job_id);
    }
    expect(exportJob.status).toBe("completed");
    // Mesh exports are not hash-bound (CONTRACT.md §5).
    expect(exportJob.job.source_sha256).toBeNull();
    expect((exportJob.artifacts ?? []).map((a) => a.file)).toEqual(
      expect.arrayContaining(["model.stl", "model.glb"]),
    );
  });

  it("404s wear the not_found kind", async () => {
    const client = createFixtureClient();
    await expect(
      client.listParts("99999999-9999-4999-8999-999999999999"),
    ).rejects.toSatisfy(
      (error: unknown) =>
        error instanceof ApiError && error.kind === "not_found",
    );
  });
});
