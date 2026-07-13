require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");


function sampleGap(overrides = {}) {
  return {
    id: "gap-1",
    project_id: "project/1",
    scope_type: "chapter",
    primary_story_id: null,
    primary_chapter_id: "chapter-1",
    gap_type: "missing_slot",
    severity: "high",
    target_slot: "chapter_contract",
    title: "章节还没有结构契约",
    description: "需要明确本章目标。",
    fingerprint: "a".repeat(64),
    status: "open",
    detected_by: "rules",
    detection_count: 1,
    resolution_note: "",
    resolved_by: null,
    resolved_by_user_id: null,
    resolved_at: null,
    first_detected_at: "2026-07-13T10:00:00",
    last_detected_at: "2026-07-13T10:00:00",
    claims: [],
    stories: [],
    chapters: [{ chapter_id: "chapter-1", role: "primary" }],
    created_at: "2026-07-13T10:00:00",
    updated_at: "2026-07-13T10:00:00",
    ...overrides,
  };
}

function sampleWorkspace(overrides = {}) {
  return {
    project_id: "project/1",
    chapter_id: "chapter-1",
    chapter_title: "第一次离家",
    contract: {
      id: "contract-1",
      version: 1,
      objective: "说明第一次离家的原因和影响。",
      reader_takeaway: "理解这次经历如何改变了叙述者。",
      time_scope_text: "青年时期",
      status: "confirmed",
      nodes: [{
        id: "node-1",
        node_type: "story",
        title: "离家经过",
        purpose: "呈现离家当天的关键行动。",
        sort_order: 0,
        required: true,
        status: "needs_work",
        stories: [{ id: "link-1", story_id: "story-1", story_role: "primary", sort_order: 0, required: true }],
      }],
    },
    stories: [{
      id: "story-1",
      title: "第一次离家",
      story_type: "event",
      summary: "十八岁时离开家乡。",
      completion_status: "incomplete",
      verification_status: "unreviewed",
      publication_status: "eligible",
      story_version: 1,
      slots: [{
        id: "slot-1",
        slot_key: "background",
        required: true,
        status: "partial",
        assessment_summary: "已有少量背景。",
        claim_ids: ["claim-1"],
      }],
      claims: [{
        id: "claim-1",
        statement: "十八岁时离开家乡。",
        claim_type: "event",
        time_text: "十八岁",
        certainty: "exact",
        verification_status: "unreviewed",
        publication_status: "eligible",
        evidence: [{
          id: "evidence-1",
          evidence_role: "supports",
          evidence_source_type: "transcript",
          utterance_id: "utterance-1",
          transcript_version_id: "version-1",
          audio_file_id: "audio-1",
          image_asset_id: null,
          start_ms: 0,
          end_ms: 3000,
          quote: "十八岁那年，我第一次离开家。",
          evidence_note: null,
          created_at: "2026-07-13T10:00:00",
        }],
      }],
    }],
    gaps: {
      must_handle: [sampleGap()],
      suggested: [],
      accepted_incomplete: [],
    },
    latest_audit_job: null,
    ...overrides,
  };
}

function loadWorkspacePage({ workspace = sampleWorkspace(), voiceApiExports = {} } = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirChapterWorkspace: async () => workspace,
    enqueueMemoirChapterWorkspaceAudit: async () => {
      throw new Error("enqueueMemoirChapterWorkspaceAudit was not mocked");
    },
    resolveMemoirNarrativeGap: async () => {
      throw new Error("resolveMemoirNarrativeGap was not mocked");
    },
    listMemoirFollowupTasks: async () => ({ items: [], total: 0 }),
    createMemoirVoiceSession: async () => {
      throw new Error("createMemoirVoiceSession was not mocked");
    },
    ...voiceApiExports,
  });
  return loadPageModule("../pages/chapter-workspace/index.ts");
}

test("chapter workspace page is registered and exposes structure, evidence, and three gap groups", async () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  assert.ok(appJson.pages.includes("pages/chapter-workspace/index"));

  const { page } = loadWorkspacePage();
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1" });

  assert.equal(context.data.workspace.chapter_title, "第一次离家");
  assert.equal(context.data.contractStatusText, "契约 v1 · 已确认");
  assert.equal(context.data.nodes[0].storyText, "第一次离家");
  assert.equal(context.data.stories[0].claims[0].evidenceCountText, "1 条证据");
  assert.deepEqual(context.data.gapGroups.map((group) => group.title), [
    "必须处理",
    "建议补充",
    "可以接受的不完整",
  ]);
  assert.equal(context.data.gapGroups[0].items[0].severityText, "重要");
  assert.equal(JSON.stringify(context.data).includes("percentage"), false);
});

test("chapter workspace lists follow-up questions and starts a task-bound human interview", async () => {
  const createdSessions = [];
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const followupTask = {
    id: "task/1",
    project_id: "project/1",
    gap_id: "gap-1",
    chapter_id: "chapter-1",
    story_id: "story-1",
    target_slot: "outcome",
    question: "第一次离家以后，生活发生了什么变化？",
    purpose: "补足故事结果和影响。",
    known_context_summary: "十八岁离开家乡。",
    priority: 3200,
    status: "deferred",
    assigned_session_id: null,
    defer_until: null,
    defer_reason: "之前暂缓",
    created_by: "system",
    started_at: null,
    completed_at: null,
    claims: [],
    utterances: [],
    created_at: "2026-07-13T10:00:00",
    updated_at: "2026-07-13T10:00:00",
  };
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      listMemoirFollowupTasks: async () => ({ items: [followupTask], total: 1 }),
      createMemoirVoiceSession: async (input, options) => {
        createdSessions.push({ input, options });
        return { session: { id: "voice/1" } };
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1", tab: "followups" });

  assert.equal(context.data.followupTasks[0].statusText, "以后再补");
  assert.equal(context.data.followupTasks[0].canInterview, true);
  const starting = page.startHumanFollowupInterview.call(context, {
    currentTarget: { dataset: { taskId: "task/1" } },
  });
  assert.equal(context.data.startingFollowupTaskId, "task/1");
  assert.equal(context.data.auditBusy, false);
  await starting;

  assert.deepEqual(createdSessions[0].options, { projectId: "project/1" });
  assert.equal(createdSessions[0].input.interview_type, "human_interview");
  assert.equal(createdSessions[0].input.session_goal, "book_completion");
  assert.equal(createdSessions[0].input.current_followup_task_id, "task/1");
  assert.match(navigations[0], /sessionGoal=book_completion/);
  assert.equal(context.data.startingFollowupTaskId, "");
  delete global.wx;
});
test("starting an audit only marks the audit control busy while the background job is submitted", async () => {
  let releaseAudit;
  const auditSubmitted = new Promise((resolve) => {
    releaseAudit = resolve;
  });
  const succeeded = sampleWorkspace({
    latest_audit_job: {
      id: "job-1",
      project_id: "project/1",
      chapter_id: "chapter-1",
      status: "succeeded",
      result_json: {},
      error_message: "",
      created_at: "2026-07-13T10:00:00",
      updated_at: "2026-07-13T10:01:00",
    },
  });
  let workspace = sampleWorkspace();
  const { page } = loadWorkspacePage({
    workspace,
    voiceApiExports: {
      enqueueMemoirChapterWorkspaceAudit: async () => {
        await auditSubmitted;
        workspace = succeeded;
        return succeeded.latest_audit_job;
      },
      getMemoirChapterWorkspace: async () => workspace,
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1" });

  const request = page.startAudit.call(context);
  assert.equal(context.data.auditBusy, true);
  assert.equal(context.data.actionBusyGapId, "");
  assert.equal(context.data.gapGroups[0].items[0].status, "open");

  releaseAudit();
  await request;
  assert.equal(context.data.auditBusy, false);
  assert.equal(context.data.auditStatusText, "检查已更新");
});

test("gap actions persist a specific user decision without disabling unrelated gaps", async () => {
  const calls = [];
  const openWorkspace = sampleWorkspace({
    gaps: {
      must_handle: [sampleGap(), sampleGap({ id: "gap-2", fingerprint: "b".repeat(64), title: "第二个问题" })],
      suggested: [],
      accepted_incomplete: [],
    },
  });
  let workspace = openWorkspace;
  global.wx = {
    showToast() {},
  };
  const { page } = loadWorkspacePage({
    workspace,
    voiceApiExports: {
      getMemoirChapterWorkspace: async () => workspace,
      resolveMemoirNarrativeGap: async (projectId, gapId, action, data) => {
        calls.push({ projectId, gapId, action, data });
        workspace = sampleWorkspace({
          gaps: {
            must_handle: [openWorkspace.gaps.must_handle[1]],
            suggested: [],
            accepted_incomplete: [sampleGap({
              status: "accepted_incomplete",
              resolution_note: data.note,
              resolved_by: "user",
            })],
          },
        });
        return workspace.gaps.accepted_incomplete[0];
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1" });
  page.changeGapNote.call(context, {
    currentTarget: { dataset: { gapId: "gap-1" } },
    detail: { value: "家人确认暂时没有更多素材" },
  });

  const action = page.applyGapAction.call(context, {
    currentTarget: { dataset: { gapId: "gap-1", action: "accept-incomplete" } },
  });
  assert.equal(context.data.actionBusyGapId, "gap-1");
  assert.equal(context.data.gapGroups[0].items[1].id, "gap-2");
  await action;

  assert.equal(calls.length, 1);
  assert.equal(calls[0].data.note, "家人确认暂时没有更多素材");
  assert.match(calls[0].data.idempotency_key, /^workspace:gap-1:accept-incomplete:/);
  assert.equal(context.data.gapGroups[2].items[0].status, "accepted_incomplete");
  assert.equal(context.data.actionBusyGapId, "");
  delete global.wx;
});
