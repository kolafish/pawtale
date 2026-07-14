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
      time_start: null,
      time_end: null,
      core_questions: [{ key: "meaning", question: "这段经历为什么重要？" }],
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
    available_stories: [{
      id: "story-2",
      title: "与李阅的关系",
      story_type: "relationship",
      summary: "多年朋友关系中的共同经历。",
      completion_status: "incomplete",
      verification_status: "needs_verification",
      story_version: 1,
      excluded_from_chapter: false,
    }],
    answer_updates: [],
    gaps: {
      must_handle: [sampleGap()],
      suggested: [],
      accepted_incomplete: [],
    },
    latest_audit_job: null,
    ...overrides,
  };
}

function sampleDraftBlock(overrides = {}) {
  return {
    id: "block-1",
    project_id: "project/1",
    chapter_id: "chapter-1",
    node_id: "node-1",
    block_role: "factual",
    sort_order: 0,
    editorial_status: "draft",
    freshness_status: "current",
    locked: false,
    current_version_id: "block-version-1",
    legacy_draft_id: null,
    current_version: {
      id: "block-version-1",
      project_id: "project/1",
      block_id: "block-1",
      version: 1,
      content: "十八岁那年，我第一次离开家。",
      created_by: "ai",
      model: "test-model",
      prompt_snapshot: "",
      feedback: null,
      legacy_draft_version_id: null,
      claims: [{ claim_id: "claim-1", usage_role: "core" }],
      stories: [{ story_id: "story-1", story_revision_id: "story-revision-1", usage_role: "primary" }],
      created_at: "2026-07-13T10:00:00",
    },
    version_count: 1,
    created_at: "2026-07-13T10:00:00",
    updated_at: "2026-07-13T10:00:00",
    ...overrides,
  };
}

function loadWorkspacePage({ workspace = sampleWorkspace(), draftBlocks = [], voiceApiExports = {} } = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirChapterWorkspace: async () => workspace,
    createMemoirChapterContractRevision: async () => {
      throw new Error("createMemoirChapterContractRevision was not mocked");
    },
    suggestMemoirChapterDirection: async () => {
      throw new Error("suggestMemoirChapterDirection was not mocked");
    },
    confirmMemoirChapterContract: async () => {
      throw new Error("confirmMemoirChapterContract was not mocked");
    },
    includeMemoirStoryInChapter: async () => {
      throw new Error("includeMemoirStoryInChapter was not mocked");
    },
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
    listMemoirDraftBlocks: async () => ({ chapter_id: workspace.chapter_id, blocks: draftBlocks }),
    enqueueMemoirDraftBlockGeneration: async () => {
      throw new Error("enqueueMemoirDraftBlockGeneration was not mocked");
    },
    getMemoirDraftBlockJob: async () => {
      throw new Error("getMemoirDraftBlockJob was not mocked");
    },
    manuallyReviseMemoirDraftBlock: async () => {
      throw new Error("manuallyReviseMemoirDraftBlock was not mocked");
    },
    decideMemoirDraftBlock: async () => {
      throw new Error("decideMemoirDraftBlock was not mocked");
    },
    enqueueMemoirDraftBlockRevision: async () => {
      throw new Error("enqueueMemoirDraftBlockRevision was not mocked");
    },
    applyMemoirDraftBlockRevision: async () => {
      throw new Error("applyMemoirDraftBlockRevision was not mocked");
    },
    assembleMemoirChapterDraftBlocks: async () => {
      throw new Error("assembleMemoirChapterDraftBlocks was not mocked");
    },
    ...voiceApiExports,
  });
  mockModule("../services/voice-authorization.ts", {
    readVoiceAuthorizationConsent: () => true,
    syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
  });
  return loadPageModule("../pages/chapter-workspace/index.ts");
}

test("chapter workspace maps draft block sources and keeps AI revision as a visible pending suggestion", async () => {
  const pendingJob = {
    id: "job-1",
    project_id: "project/1",
    chapter_id: "chapter-1",
    job_type: "generate_draft_blocks",
    status: "pending",
    result_json: {},
    error_message: "",
    created_at: "2026-07-13T10:00:00",
    updated_at: "2026-07-13T10:00:00",
  };
  const { page } = loadWorkspacePage({
    draftBlocks: [sampleDraftBlock()],
    voiceApiExports: {
      enqueueMemoirDraftBlockRevision: async () => pendingJob,
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1", tab: "drafts" });

  const block = context.data.draftBlocks[0];
  assert.equal(block.nodeTitle, "离家经过");
  assert.equal(block.sourceStories[0].title, "第一次离家");
  assert.equal(block.sourceClaims[0].statement, "十八岁时离开家乡。");
  assert.equal(block.sourceClaims[0].evidenceText, "十八岁那年，我第一次离开家。");

  page.changeDraftRevisionFeedback.call(context, {
    currentTarget: { dataset: { blockId: "block-1" } },
    detail: { value: "只把第一句改得更自然" },
  });
  await page.requestDraftBlockRevision.call(context, {
    currentTarget: { dataset: { blockId: "block-1" } },
  });

  assert.equal(context.data.revisionBlockId, "block-1");
  assert.equal(context.data.revisionStatus, "修改建议已排队");
  assert.equal(context.data.draftGenerationBusy, false);
  assert.equal(context.data.draftBlocks[0].busy, false);
  assert.equal(context.data.draftBlocks[0].current_version.content, "十八岁那年，我第一次离开家。");
  page.stopDraftPolling.call(context);
});

test("chapter workspace restores a completed draft suggestion after the page is recreated", async () => {
  const storage = new Map();
  global.wx = {
    getStorageSync(key) { return storage.get(key); },
    setStorageSync(key, value) { storage.set(key, value); },
    removeStorageSync(key) { storage.delete(key); },
  };
  const { writeMemoirDraftJobCursor } = require("../services/memoir-draft-job-state.ts");
  writeMemoirDraftJobCursor({
    projectId: "project/1",
    chapterId: "chapter-1",
    jobId: "job-restored",
    mode: "revise",
    blockId: "block-1",
  });
  const { page } = loadWorkspacePage({
    draftBlocks: [sampleDraftBlock()],
    voiceApiExports: {
      getMemoirDraftBlockJob: async () => ({
        id: "job-restored",
        project_id: "project/1",
        chapter_id: "chapter-1",
        job_type: "generate_draft_blocks",
        status: "succeeded",
        result_json: { content: "十八岁那年，我独自离开了家。" },
        error_message: "",
        created_at: "2026-07-13T10:00:00",
        updated_at: "2026-07-13T10:01:00",
      }),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project%2F1",
    chapterId: "chapter-1",
    tab: "drafts",
  });

  assert.equal(context.data.revisionBlockId, "block-1");
  assert.equal(context.data.revisionJobId, "job-restored");
  assert.equal(context.data.revisionSuggestion, "十八岁那年，我独自离开了家。");
  assert.equal(context.data.revisionStatus, "修改建议已生成，确认后才会替换当前版本");
  delete global.wx;
});

test("chapter workspace page is registered and exposes structure, evidence, and three gap groups", async () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  assert.ok(appJson.pages.includes("pages/chapter-workspace/index"));

  const { page } = loadWorkspacePage();
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1" });

  assert.equal(context.data.workspace.chapter_title, "第一次离家");
  assert.equal(context.data.activeTab, "overview");
  assert.equal(context.data.contractStatusText, "本章方向已确认");
  assert.equal(context.data.nextAction.kind, "start_audit");
  assert.equal(context.data.nodes[0].storyText, "第一次离家");
  assert.equal(context.data.stories[0].claims[0].evidenceCountText, "1 段原话依据");
  assert.equal(context.data.stories[0].claims[0].verificationStatusText, "待核对");
  assert.deepEqual(context.data.gapGroups.map((group) => group.title), [
    "必须处理",
    "建议补充",
    "可以接受的不完整",
  ]);
  assert.equal(context.data.gapGroups[0].items[0].severityText, "重要");
  assert.equal(JSON.stringify(context.data).includes("percentage"), false);
});

test("chapter overview turns current chapter state into one plain-language next action", () => {
  const { chapterNextAction } = loadWorkspacePage().mod;
  const workspace = sampleWorkspace({
    gaps: { must_handle: [], suggested: [], accepted_incomplete: [] },
    latest_audit_job: {
      id: "audit-1",
      project_id: "project/1",
      chapter_id: "chapter-1",
      status: "succeeded",
      result_json: {},
      error_message: "",
      created_at: "2026-07-13T10:00:00",
      updated_at: "2026-07-13T10:01:00",
    },
  });
  const task = {
    id: "task-1",
    status: "pending",
    question: "离家那天，是谁送您到车站的？",
  };

  const action = chapterNextAction(workspace, [task], []);

  assert.equal(action.kind, "open_followups");
  assert.equal(action.title, "离家那天，是谁送您到车站的？");
  assert.equal(action.actionText, "回答这个问题");
  assert.doesNotMatch(`${action.eyebrow}${action.title}${action.detail}`, /Story|Claim|slot|Contract/);
});

test("chapter overview creates a missing direction instead of opening an empty detail view", async () => {
  const confirmed = sampleWorkspace();
  const draft = {
    ...confirmed,
    contract: { ...confirmed.contract, status: "draft" },
  };
  const missing = { ...confirmed, contract: null };
  let current = missing;
  const suggestions = [];
  const { page } = loadWorkspacePage({
    workspace: missing,
    voiceApiExports: {
      getMemoirChapterWorkspace: async () => current,
      suggestMemoirChapterDirection: async (projectId, chapterId) => {
        suggestions.push({ projectId, chapterId });
        current = draft;
        return draft.contract;
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1" });

  assert.equal(context.data.nextAction.kind, "create_direction");
  assert.equal(context.data.nextAction.actionText, "整理本章方向");
  await page.runNextAction.call(context);

  assert.deepEqual(suggestions, [{ projectId: "project/1", chapterId: "chapter-1" }]);
  assert.equal(context.data.nextAction.kind, "confirm_direction");
});

test("chapter overview confirms a plain-language direction without exposing contract controls", async () => {
  const confirmed = sampleWorkspace({
    gaps: { must_handle: [], suggested: [], accepted_incomplete: [] },
    latest_audit_job: {
      id: "audit-1",
      project_id: "project/1",
      chapter_id: "chapter-1",
      status: "succeeded",
      result_json: {},
      error_message: "",
      created_at: "2026-07-13T10:00:00",
      updated_at: "2026-07-13T10:01:00",
    },
  });
  const draft = {
    ...confirmed,
    contract: { ...confirmed.contract, status: "draft" },
  };
  let current = draft;
  const confirmations = [];
  global.wx = { showToast() {} };
  const { page } = loadWorkspacePage({
    workspace: draft,
    voiceApiExports: {
      getMemoirChapterWorkspace: async () => current,
      confirmMemoirChapterContract: async (projectId, contractId) => {
        confirmations.push({ projectId, contractId });
        current = confirmed;
        return confirmed.contract;
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1" });

  assert.equal(context.data.nextAction.kind, "confirm_direction");
  assert.equal(context.data.nextAction.actionText, "这个方向可以");
  await page.runNextAction.call(context);

  assert.deepEqual(confirmations, [{ projectId: "project/1", contractId: "contract-1" }]);
  assert.equal(context.data.contractStatusText, "本章方向已确认");
  delete global.wx;
});

test("chapter workspace creates an explicit draft revision before editing a confirmed structure", async () => {
  const confirmed = sampleWorkspace();
  const draft = sampleWorkspace({
    contract: { ...confirmed.contract, id: "contract-2", version: 2, status: "draft" },
  });
  let current = confirmed;
  const calls = [];
  const { page } = loadWorkspacePage({
    workspace: confirmed,
    voiceApiExports: {
      getMemoirChapterWorkspace: async () => current,
      createMemoirChapterContractRevision: async (projectId, chapterId, data) => {
        calls.push({ projectId, chapterId, data });
        current = draft;
        return draft.contract;
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project%2F1",
    chapterId: "chapter-1",
    tab: "stories",
  });

  await page.createContractRevision.call(context);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].projectId, "project/1");
  assert.equal(calls[0].chapterId, "chapter-1");
  assert.equal(calls[0].data.idempotency_key, "workspace:revision:contract-1");
  assert.equal(calls[0].data.copy_from_contract_id, "contract-1");
  assert.equal(calls[0].data.copy_nodes, true);
  assert.deepEqual(calls[0].data.core_questions, confirmed.contract.core_questions);
  assert.equal(context.data.workspace.contract.status, "draft");
  assert.equal(context.data.activeTab, "stories");
  assert.equal(context.data.contractRevisionBusy, false);
});

test("including a Story only marks that Story busy and exposes request failures", async () => {
  const base = sampleWorkspace();
  const secondCandidate = {
    ...base.available_stories[0],
    id: "story-3",
    title: "在成都求学",
    excluded_from_chapter: true,
  };
  const draft = sampleWorkspace({
    contract: { ...base.contract, status: "draft" },
    available_stories: [...base.available_stories, secondCandidate],
  });
  let current = draft;
  let releaseInclude;
  const pending = new Promise((resolve) => { releaseInclude = resolve; });
  const calls = [];
  global.wx = { showToast() {} };
  const { page } = loadWorkspacePage({
    workspace: draft,
    voiceApiExports: {
      getMemoirChapterWorkspace: async () => current,
      includeMemoirStoryInChapter: async (projectId, contractId, storyId, data) => {
        calls.push({ projectId, contractId, storyId, data });
        if (storyId === "story-3") {
          throw new Error("章节结构已发生变化");
        }
        await pending;
        current = sampleWorkspace({
          contract: { ...base.contract, status: "draft" },
          available_stories: [secondCandidate],
        });
        return current.contract;
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1", tab: "stories" });

  const firstRequest = page.includeAvailableStory.call(context, {
    currentTarget: { dataset: { storyId: "story-2" } },
  });
  assert.equal(context.data.availableStories[0].busy, true);
  assert.equal(context.data.availableStories[1].busy, false);
  assert.equal(context.data.contractRevisionBusy, false);
  await page.includeAvailableStory.call(context, {
    currentTarget: { dataset: { storyId: "story-3" } },
  });
  assert.equal(context.data.availableStories[0].busy, true);
  assert.equal(context.data.availableStories[1].busy, false);
  assert.equal(context.data.availableStories[1].error, "章节结构已发生变化");

  releaseInclude();
  await firstRequest;

  assert.equal(calls[0].contractId, "contract-1");
  assert.equal(calls[0].storyId, "story-2");
  assert.match(calls[0].data.idempotency_key, /^workspace:include:contract-1:story-2:/);
  assert.deepEqual(context.data.availableStories.map((story) => story.id), ["story-3"]);
  assert.equal(context.data.availableStories[0].busy, false);
  assert.equal(context.data.availableStories[0].error, "章节结构已发生变化");
  delete global.wx;
});

test("chapter workspace lists follow-up questions and starts the task-bound AI interview", async () => {
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
  const starting = page.startFollowupInterview.call(context, {
    currentTarget: { dataset: { taskId: "task/1" } },
  });
  assert.equal(context.data.startingFollowupTaskId, "task/1");
  assert.equal(context.data.auditBusy, false);
  await starting;

  assert.deepEqual(createdSessions[0].options, { projectId: "project/1" });
  assert.equal(createdSessions[0].input.interview_type, "ai_interview");
  assert.equal(createdSessions[0].input.session_goal, "book_completion");
  assert.equal(createdSessions[0].input.current_followup_task_id, "task/1");
  assert.match(navigations[0], /sessionGoal=book_completion/);
  assert.equal(context.data.startingFollowupTaskId, "");
  delete global.wx;
});

test("chapter overview continues ambiguous fact confirmation in the existing AI interview", async () => {
  const createdSessions = [];
  const navigations = [];
  const followupTask = {
    id: "task-fact-1",
    project_id: "project/1",
    gap_id: "gap-1",
    chapter_id: "chapter-1",
    story_id: null,
    target_slot: "",
    answer_use: "fact_resolution",
    question: "经理和邀请创业的人是同一个人吗？",
    purpose: "核对人物关系",
    known_context_summary: "书中有两个称呼",
    priority: 3000,
    status: "pending",
    assigned_session_id: null,
    defer_until: null,
    defer_reason: "",
    created_by: "system",
    started_at: null,
    completed_at: null,
    claims: [],
    utterances: [],
    created_at: "2026-07-14T10:00:00",
    updated_at: "2026-07-14T10:00:00",
  };
  const workspace = sampleWorkspace({
    answer_updates: [{
      id: "relation-1",
      task_id: followupTask.id,
      status: "needs_confirmation",
      question: "您说的同一个人，是工作时的经理和后来邀请创业的人吗？",
      answer_text: "",
      summaries: [],
      created_at: "2026-07-14T10:05:00",
    }],
  });
  global.wx = {
    navigateTo({ url }) { navigations.push(url); },
  };
  const { page } = loadWorkspacePage({
    workspace,
    voiceApiExports: {
      listMemoirFollowupTasks: async () => ({ items: [followupTask], total: 1 }),
      createMemoirVoiceSession: async (input, options) => {
        createdSessions.push({ input, options });
        return { session: { id: "voice-fact-1" } };
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1" });

  assert.equal(context.data.nextAction.kind, "continue_fact_confirmation");
  assert.equal(context.data.nextAction.title, workspace.answer_updates[0].question);
  await page.runNextAction.call(context);

  assert.equal(createdSessions[0].input.interview_type, "ai_interview");
  assert.equal(createdSessions[0].input.current_followup_task_id, followupTask.id);
  assert.equal(createdSessions[0].options.projectId, "project/1");
  assert.match(navigations[0], /voiceSessionId=voice-fact-1/);
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
