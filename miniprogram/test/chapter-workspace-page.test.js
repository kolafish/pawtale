require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

function sampleGap(overrides = {}) {
  return {
    id: "gap-1",
    project_id: "project-1",
    scope_type: "chapter",
    primary_story_id: null,
    primary_chapter_id: "chapter-1",
    gap_type: "time_conflict",
    severity: "high",
    target_slot: "time",
    title: "离家时间有两个说法",
    description: "一处是十八岁，一处是十九岁。",
    status: "open",
    claims: [],
    stories: [],
    chapters: [],
    ...overrides,
  };
}

function sampleWorkspace(overrides = {}) {
  return {
    project_id: "project-1",
    chapter_id: "chapter-1",
    chapter_title: "第一次离家",
    contract: {
      id: "contract-1",
      version: 1,
      objective: "写清第一次离家的经过和影响。",
      reader_takeaway: "理解这次经历为什么重要。",
      time_scope_text: "青年时期",
      time_start: null,
      time_end: null,
      core_questions: [],
      constraints: {
        include_story_ids: ["story-1"],
        exclude_story_ids: [],
        include_topics: [],
        exclude_topics: [],
      },
      status: "confirmed",
      nodes: [{
        id: "node-1",
        node_type: "story",
        title: "离家当天",
        purpose: "写清当天的经过。",
        sort_order: 0,
        required: true,
        status: "ready",
        stories: [],
      }],
    },
    stories: [{
      id: "story-1",
      title: "十八岁离开家乡",
      story_type: "event",
      summary: "第一次独自去外地。",
      completion_status: "sufficient",
      verification_status: "confirmed",
      publication_status: "eligible",
      story_version: 1,
      slots: [],
      claims: [{
        id: "claim-1",
        statement: "十八岁时离开家乡。",
        claim_type: "event",
        time_text: "十八岁",
        certainty: "exact",
        verification_status: "confirmed",
        publication_status: "eligible",
        evidence: [{
          id: "evidence-1",
          evidence_role: "supports",
          quote: "十八岁那年，我第一次离开家。",
          evidence_note: null,
        }],
      }],
    }],
    available_stories: [],
    answer_updates: [{
      id: "update-1",
      task_id: "task-confirm",
      status: "needs_confirmation",
      question: "您离家时到底是十八岁还是十九岁？",
      answer_text: "应该是十八岁。",
      summaries: [],
      created_at: "2026-07-15T10:00:00",
    }],
    gaps: {
      must_handle: [sampleGap()],
      suggested: [sampleGap({ id: "gap-2", severity: "medium", title: "可以再讲讲到达后的感受" })],
      accepted_incomplete: [],
    },
    latest_audit_job: {
      id: "audit-1",
      status: "succeeded",
      error_message: "",
    },
    ...overrides,
  };
}

function sampleTask(overrides = {}) {
  return {
    id: "task-1",
    project_id: "project-1",
    gap_id: "gap-2",
    chapter_id: "chapter-1",
    story_id: "story-1",
    target_slot: "reflection",
    question: "到达外地后的第一晚，您是什么感受？",
    purpose: "补充这次离家的影响。",
    known_context_summary: "您已经讲过当天坐车的经过。",
    priority: 2000,
    status: "pending",
    assigned_session_id: null,
    defer_reason: "",
    created_by: "system",
    claims: [],
    utterances: [],
    created_at: "2026-07-15T10:00:00",
    updated_at: "2026-07-15T10:00:00",
    ...overrides,
  };
}

function sampleDraftBlock(overrides = {}) {
  return {
    id: "block-1",
    project_id: "project-1",
    chapter_id: "chapter-1",
    node_id: "node-1",
    block_role: "factual",
    sort_order: 0,
    editorial_status: "draft",
    freshness_status: "current",
    locked: false,
    current_version_id: "version-1",
    legacy_draft_id: null,
    current_version: {
      id: "version-1",
      content: "十八岁那年，我第一次离开家。",
      version: 1,
      claims: [],
      stories: [],
    },
    version_count: 1,
    ...overrides,
  };
}

function loadWorkspacePage({
  workspace = sampleWorkspace(),
  tasks = [sampleTask(), sampleTask({ id: "task-confirm", gap_id: "gap-1", priority: 3000 })],
  blocks = [sampleDraftBlock()],
  voiceApiExports = {},
} = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirChapterWorkspace: async () => workspace,
    listMemoirFollowupTasks: async () => ({ items: tasks, total: tasks.length }),
    listMemoirDraftBlocks: async () => ({ chapter_id: workspace.chapter_id, blocks }),
    suggestMemoirChapterDirection: async () => {
      throw new Error("suggestMemoirChapterDirection was not mocked");
    },
    confirmMemoirChapterContract: async () => {
      throw new Error("confirmMemoirChapterContract was not mocked");
    },
    enqueueMemoirChapterWorkspaceAudit: async () => {
      throw new Error("enqueueMemoirChapterWorkspaceAudit was not mocked");
    },
    resolveMemoirNarrativeGap: async () => {
      throw new Error("resolveMemoirNarrativeGap was not mocked");
    },
    createMemoirVoiceSession: async () => {
      throw new Error("createMemoirVoiceSession was not mocked");
    },
    enqueueMemoirDraftBlockGeneration: async () => {
      throw new Error("enqueueMemoirDraftBlockGeneration was not mocked");
    },
    getMemoirDraftBlockJob: async () => {
      throw new Error("getMemoirDraftBlockJob was not mocked");
    },
    manuallyReviseMemoirDraftBlock: async () => {
      throw new Error("manuallyReviseMemoirDraftBlock was not mocked");
    },
    assembleMemoirChapterDraftBlocks: async () => {
      throw new Error("assembleMemoirChapterDraftBlocks was not mocked");
    },
    ...voiceApiExports,
  });
  mockModule("../services/navigation.ts", { routeAuthError: () => false });
  mockModule("../services/audit-tracker.ts", { trackAuditAction: () => {} });
  mockModule("../services/memoir-draft-job-state.ts", {
    clearMemoirDraftJobCursor: () => {},
    readMemoirDraftJobCursor: () => null,
    writeMemoirDraftJobCursor: () => {},
  });
  mockModule("../services/voice-authorization.ts", {
    readVoiceAuthorizationConsent: () => true,
    syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
  });
  return loadPageModule("../pages/chapter-workspace/index.ts");
}

test("chapter overview reports the whole chapter without exposing internal model names", () => {
  const { chapterWorkspaceViewPatch } = loadWorkspacePage().mod;
  const patch = chapterWorkspaceViewPatch(
    sampleWorkspace(),
    [
      sampleTask(),
      sampleTask({ id: "task-2", priority: 1000 }),
      sampleTask({ id: "task-confirm", gap_id: "gap-1", priority: 3000 }),
    ],
    [sampleDraftBlock()],
  );

  assert.equal(patch.recordedCount, 1);
  assert.equal(patch.mustConfirmCount, 1);
  assert.equal(patch.suggestionCount, 2);
  assert.equal(patch.acceptedIncompleteCount, 0);
  assert.equal(patch.directions.length, 2);
  assert.equal(patch.reviewGaps.length, 0);
  assert.equal(patch.outlineTitles[0], "离家当天");
  assert.doesNotMatch(JSON.stringify(patch), /Story|Claim|slot|Contract|supports=|confirmed=/);
});

test("a required fact check stays ahead of optional interview directions", () => {
  const { chapterNextAction } = loadWorkspacePage().mod;
  const workspace = sampleWorkspace({ answer_updates: [] });

  const action = chapterNextAction(workspace, [sampleTask()], [sampleDraftBlock()]);

  assert.equal(action.kind, "open_checks");
  assert.equal(action.title, "离家时间有两个说法");
});

test("selected completion direction starts the existing realtime voice-chat chain", async () => {
  const created = [];
  const navigations = [];
  global.wx = {
    navigateTo({ url }) {
      navigations.push(url);
    },
    showToast() {},
  };
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      createMemoirVoiceSession: async (input, options) => {
        created.push({ input, options });
        return { session: { id: "voice-1" } };
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  await page.startFollowupInterview.call(context, {
    currentTarget: { dataset: { taskId: "task-1" } },
  });

  assert.equal(created[0].input.current_followup_task_id, "task-1");
  assert.equal(created[0].input.session_goal, "book_completion");
  assert.equal(created[0].options.projectId, "project-1");
  assert.match(navigations[0], /pages\/voice-chat\/index/);
  assert.match(navigations[0], /voiceSessionId=voice-1/);
  delete global.wx;
});

test("draft generation reports both newly created and refreshed paragraphs", async () => {
  global.wx = { showToast() {} };
  const succeededJob = {
    id: "job-1",
    status: "succeeded",
    result_json: {
      created_block_ids: ["block-2"],
      updated_block_ids: ["block-1"],
    },
    error_message: "",
  };
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      enqueueMemoirDraftBlockGeneration: async () => succeededJob,
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  await page.generateDraftBlocks.call(context);

  assert.equal(context.data.draftGenerationBusy, false);
  assert.equal(context.data.draftGenerationStatus, "已整理 2 段正文");
  delete global.wx;
});

test("a follow-up read failure keeps the chapter overview visible and blocks an unreliable next step", async () => {
  global.wx = {};
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      listMemoirFollowupTasks: async () => {
        throw new Error("补充方向暂时不可用");
      },
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  assert.equal(context.data.workspace.chapter_title, "第一次离家");
  assert.equal(context.data.recordedCount, 1);
  assert.equal(context.data.followupError, "补充方向暂时不可用");
  assert.equal(context.data.nextAction.disabled, true);
  delete global.wx;
});
