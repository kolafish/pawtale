require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

function summary(overrides = {}) {
  return {
    chapter_id: "chapter-1",
    title: "第一次离家",
    sort_order: 0,
    state: "draft_ready",
    next_action: {
      kind: "review_draft",
      title: "通读本章正文",
      detail: "需要时可以修改。",
      action_text: "查看正文",
      disabled: false,
      issue_id: null,
    },
    active_issue: null,
    headline: "正文已经整理好",
    detail: "请通读整章；需要时可以修改。",
    ...overrides,
  };
}

function workspace(overrides = {}) {
  return {
    project_id: "project-1",
    chapter_id: "chapter-1",
    chapter_title: "第一次离家",
    completion_summary: summary(),
    draft_needs_rewrite: false,
    can_rewrite_draft: false,
    pending_assignment_material_count: 0,
    pending_completion_source_count: 0,
    pending_new_source_count: 0,
    sections: [
      {
        id: "section-1",
        title: "离家前的准备",
        section_type: "event",
        sort_order: 0,
        status: "active",
        include_in_book: true,
        requires_user_assignment: false,
        version: 1,
        material_count: 1,
        included_material_count: 1,
        materials: [
          {
            utterance_id: "utterance-1",
            sort_order: 0,
            text: "十八岁那年，我第一次离开家。",
            include_in_book: true,
          },
        ],
      },
    ],
    draft_id: "draft-1",
    draft_version_id: "version-1",
    draft_version: 1,
    draft_status: "draft",
    draft_content: "十八岁那年，我第一次离开家。",
    draft_sections: [
      {
        section_id: "section-1",
        title: "离家前的准备",
        section_type: "event",
        section_version: 1,
        sort_order: 0,
        content: "十八岁那年，我第一次离开家。",
        used_material_ids: ["utterance-1"],
      },
    ],
    ...overrides,
  };
}

function workspaceWithPendingMaterial(overrides = {}) {
  const base = workspace();
  return workspace({
    pending_assignment_material_count: 1,
    can_rewrite_draft: false,
    completion_summary: summary({
      state: "needs_input",
      next_action: {
        kind: "review_sections",
        title: "还有 1 条讲述待选择小节",
        detail: "AI 没有强行归类。",
        action_text: "去选择",
        disabled: false,
        issue_id: null,
      },
      headline: "请先整理待归类素材",
      detail: "全部选择完成后，才能按新的小节结构生成正文。",
    }),
    sections: [
      ...base.sections,
      {
        id: "section-pending",
        title: "待归类素材",
        section_type: "life_fragment",
        sort_order: 1,
        status: "active",
        include_in_book: false,
        requires_user_assignment: true,
        version: 1,
        material_count: 1,
        included_material_count: 1,
        materials: [
          {
            utterance_id: "utterance-pending",
            sort_order: 0,
            text: "这段讲述和前后几个小节都有关系。",
            include_in_book: true,
          },
        ],
      },
    ],
    ...overrides,
  });
}

function loadWorkspacePage({
  currentWorkspace = workspace(),
  voiceApiExports = {},
  voiceSearchExports = {},
  consent = true,
  cacheExports = {},
} = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirChapterWorkspace: async () => currentWorkspace,
    prepareMemoirBookChapter: async () => {},
    reviseMemoirChapterDraftSectionWithAI: async () => currentWorkspace,
    regenerateMemoirChapterSections: async () => {},
    moveMemoirChapterMaterial: async () => {},
    rewriteMemoirBookChapter: async () => {},
    closeMemoirCompletionIssue: async () => {},
    createMemoirVoiceSession: async () => ({ session: { id: "session-1" } }),
    reviseMemoirBookChapterDraft: async () => ({}),
    confirmMemoirBookChapterDraft: async () => ({}),
    createMemoirBookChapterDraftSectionTTS: async () => ({
      audio_file_ids: [],
    }),
    ...voiceApiExports,
  });
  mockModule("../services/navigation.ts", { routeAuthError: () => false });
  mockModule("../services/audit-tracker.ts", { trackAuditAction() {} });
  mockModule("../stores/memoir-read-cache.ts", {
    readCachedMemoirChapterWorkspace: () => ({ status: "miss" }),
    writeCachedMemoirChapterWorkspace: () => {},
    ...cacheExports,
  });
  mockModule("../services/voice-authorization.ts", {
    readVoiceAuthorizationConsent: () => consent,
    syncVoiceAuthorizationConsent: async () => {},
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
  });
  mockModule("../services/memoir-voice-search.ts", {
    recordMemoirVoiceSearchQuery: async () => {
      throw new Error("recordMemoirVoiceSearchQuery was not mocked");
    },
    stopMemoirVoiceSearchRecording: () => {},
    ...voiceSearchExports,
  });
  return loadPageModule("../pages/chapter-workspace/index.ts");
}

function installWx() {
  const navigations = [];
  const toasts = [];
  const modals = [];
  const navigationTitles = [];
  const scrolls = [];
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_memoir_tts_settings") {
        return {
          book: { voice: "zh_female_xiaohe_uranus_bigtts", speedRatio: 1.5 },
        };
      }
      return "";
    },
    navigateTo(options) { navigations.push(options.url); },
    navigateBack() {},
    setNavigationBarTitle(options) { navigationTitles.push(options.title); },
    pageScrollTo(options) { scrolls.push(options.selector); },
    stopPullDownRefresh() {},
    showToast(options) { toasts.push(options); },
    showModal(options) {
      modals.push(options);
      options.success?.({ confirm: true, cancel: false });
    },
  };
  return { navigations, navigationTitles, toasts, modals, scrolls };
}

test("chapter workspace row actions avoid native button shrink wrapping", () => {
  const template = fs.readFileSync(
    path.join(__dirname, "../pages/chapter-workspace/index.wxml"),
    "utf8",
  );

  assert.match(template, /<view\s+class="section-toggle"/);
  assert.doesNotMatch(template, /<button\s+class="section-toggle"/);
  assert.match(template, /<view\s+class="primary-action/);
  assert.doesNotMatch(template, /<button\s+class="primary-action/);
  assert.match(template, /<view\s+class="structure-regenerate-action/);
  assert.match(template, /bindtap="toggleStructure"/);
  assert.match(template, /wx:if="{{structureExpanded}}"/);
  assert.match(template, /<view\s+class="rewrite-action/);
  assert.match(template, /rewriteDraftBusyText\s*:\s*rewriteDraftActionText/);
  assert.match(template, /<picker[\s\S]*bindchange="moveUnassignedMaterial"/);
  assert.match(template, /wx:for="{{draftSectionViews}}"/);
  assert.match(template, /bindtap="toggleDraftSection"/);
  assert.match(template, /bindtap="startDraftSectionAIEdit"/);
  assert.match(template, /bindtap="toggleDraftSectionVoiceInstruction"/);
  assert.match(template, /bindtap="submitDraftSectionAIInstruction"/);
  assert.match(template, /bindtap="playDraftSection"/);
  assert.match(template, /operationKind !== ''/);
  assert.match(template, /audioPhase === "preparing"/);
  assert.doesNotMatch(
    template,
    /processBusy|regeneratingSections|regeneratingDraftSectionId|ttsBusySectionId/,
  );
});

test("chapter workspace loads a chapter draft with visible small sections", async () => {
  const { navigationTitles } = installWx();
  let loadCount = 0;
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      getMemoirChapterWorkspace: async () => {
        loadCount += 1;
        return workspace();
      },
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  assert.equal(loadCount, 1);
  assert.equal(context.data.workspace.draft_content, "十八岁那年，我第一次离开家。");
  assert.equal(context.data.workspace.draft_sections[0].title, "离家前的准备");
  assert.equal(context.data.draftSectionViews[0].expanded, false);
  assert.equal(context.data.draftSectionViews[0].metaText, "1 条讲述整理 · 14 字");
  assert.equal(context.data.sectionStructureSummary, "1 个小节 · 1 条讲述");
  assert.equal(context.data.structureExpanded, false);
  assert.equal(context.data.sectionViews[0].title, "离家前的准备");
  assert.equal(context.data.sectionViews[0].typeText, "一段经历");
  assert.equal(context.data.canConfirmChapter, true);
  assert.deepEqual(navigationTitles, ["第一次离家"]);
  assert.doesNotMatch(
    JSON.stringify(context.data.workspace),
    /contract|nodes|draft_blocks|answer_updates/,
  );
  delete global.wx;
});

test("chapter workspace accepts a refresh started after cache invalidation", async () => {
  installWx();
  let invalidationRevision = 0;
  let loadCount = 0;
  const freshWorkspace = workspaceWithPendingMaterial();
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      getMemoirChapterWorkspace: async () => {
        loadCount += 1;
        return loadCount === 1 ? workspace() : freshWorkspace;
      },
    },
    cacheExports: {
      readCachedMemoirChapterWorkspace: () => ({
        status: "stale",
        payload: workspace(),
        localInvalidationRevision: invalidationRevision,
      }),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });
  invalidationRevision = 1;
  await page.loadPageData.call(context, true);

  assert.equal(loadCount, 2);
  assert.equal(context.data.workspace.pending_assignment_material_count, 1);
  assert.equal(context.data.nextAction.kind, "review_sections");
  delete global.wx;
});

test("chapter workspace keeps draft bodies collapsed until the selected section is opened", async () => {
  installWx();
  const { page } = loadWorkspacePage();
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(context.data.draftSectionViews[0].expanded, false);
  page.toggleDraftSection.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });
  assert.equal(context.data.draftSectionViews[0].expanded, true);
  page.toggleDraftSection.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });
  assert.equal(context.data.draftSectionViews[0].expanded, false);
  delete global.wx;
});

test("chapter workspace serializes mutations through one operation state", () => {
  installWx();
  const { page } = loadWorkspacePage();
  const context = pageContext(page);

  assert.equal(
    page.beginOperation.call(context, "regenerate_sections"),
    true,
  );
  assert.equal(context.data.operationKind, "regenerate_sections");
  assert.equal(
    page.beginOperation.call(context, "rewrite_chapter"),
    false,
  );
  page.finishOperation.call(context, "rewrite_chapter");
  assert.equal(context.data.operationKind, "regenerate_sections");
  page.finishOperation.call(context, "regenerate_sections");
  assert.equal(context.data.operationKind, "");
  assert.equal(context.data.operationTargetId, "");
  context.setData({
    editingMode: "manual",
    editingSectionId: "section-1",
  });
  assert.equal(page.beginOperation.call(context, "prepare"), false);
  assert.equal(
    page.beginOperation.call(context, "save_draft_section", "section-1"),
    true,
  );
  page.finishOperation.call(context, "save_draft_section", "section-1");
  context.setData({ editingMode: "ai" });
  assert.equal(
    page.beginOperation.call(
      context,
      "revise_draft_section_with_ai",
      "section-1",
    ),
    true,
  );
  page.finishOperation.call(
    context,
    "revise_draft_section_with_ai",
    "section-1",
  );
  delete global.wx;
});

test("chapter workspace blocks row actions while another operation is active", async () => {
  const { modals } = installWx();
  const ttsRequests = [];
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      createMemoirBookChapterDraftSectionTTS: async (...args) => {
        ttsRequests.push(args);
        return { audio_file_ids: ["audio-1"] };
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });
  context.setData({ operationKind: "prepare" });
  const sectionEvent = {
    currentTarget: { dataset: { id: "section-1" } },
  };

  page.startDraftSectionEdit.call(context, sectionEvent);
  await page.playDraftSection.call(context, sectionEvent);

  assert.equal(context.data.editingSectionId, "");
  assert.equal(modals.length, 0);
  assert.deepEqual(ttsRequests, []);
  delete global.wx;
});

test("chapter workspace protects unsaved edits before returning to the book flow", () => {
  const tabNavigations = [];
  const modalResults = [
    { confirm: false, cancel: true },
    { confirm: true, cancel: false },
  ];
  global.wx = {
    navigateBack(options) {
      options.fail?.();
    },
    switchTab(options) {
      tabNavigations.push(options.url);
    },
    showModal(options) {
      options.success?.(modalResults.shift());
    },
  };
  const { page } = loadWorkspacePage();
  const context = pageContext(page, {
    workspace: workspace(),
    editingSectionId: "section-1",
    editingSectionContent: "还没有保存的修改",
  });

  page.returnToBookFlow.call(context);
  assert.deepEqual(tabNavigations, []);

  page.returnToBookFlow.call(context);
  assert.deepEqual(tabNavigations, ["/pages/book/index"]);
  delete global.wx;
});

test("chapter workspace expands materials under their section", async () => {
  installWx();
  const { page } = loadWorkspacePage();
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });
  page.toggleSection.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });

  assert.equal(context.data.sectionViews[0].expanded, true);
  assert.equal(
    context.data.sectionViews[0].visibleMaterials[0].text,
    "十八岁那年，我第一次离开家。",
  );
  delete global.wx;
});

test("chapter workspace regenerates the current section structure after confirmation", async () => {
  const { toasts } = installWx();
  const calls = [];
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      regenerateMemoirChapterSections: async (...args) => {
        calls.push(args);
        return {
          project_id: "project-1",
          chapter_id: "chapter-1",
          material_count: 1,
          assigned_count: 1,
          unassigned_count: 0,
          section_count: 2,
        };
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  page.regenerateSections.call(context);
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [[
    "project-1",
    "chapter-1",
    [{ id: "section-1", version: 1 }],
  ]]);
  assert.equal(toasts.at(-1).title, "已整理为2个小节");
  assert.equal(context.data.operationKind, "");
  delete global.wx;
});

test("section regeneration opens pending material instead of running a stale next action", async () => {
  const { modals, scrolls } = installWx();
  const prepareCalls = [];
  let loadCount = 0;
  const pendingWorkspace = workspaceWithPendingMaterial({
    draft_needs_rewrite: true,
  });
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      getMemoirChapterWorkspace: async () => {
        loadCount += 1;
        return loadCount === 1 ? workspace() : pendingWorkspace;
      },
      regenerateMemoirChapterSections: async () => ({
        project_id: "project-1",
        chapter_id: "chapter-1",
        material_count: 2,
        assigned_count: 1,
        unassigned_count: 1,
        section_count: 1,
      }),
      prepareMemoirBookChapter: async (...args) => prepareCalls.push(args),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  page.regenerateSections.call(context);
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(
    modals.map((modal) => modal.title),
    ["重新整理小节分组", "有讲述需要选择小节"],
  );
  assert.deepEqual(prepareCalls, []);
  assert.equal(context.data.nextAction.kind, "review_sections");
  assert.equal(context.data.structureExpanded, true);
  assert.equal(context.data.sectionViews.at(-1).expanded, true);
  assert.equal(scrolls.at(-1), "#chapter-structure");
  delete global.wx;
});

test("chapter workspace exposes pending materials and moves one after confirmation", async () => {
  const { toasts } = installWx();
  const calls = [];
  const pendingWorkspace = workspaceWithPendingMaterial();
  const { page } = loadWorkspacePage({
    currentWorkspace: pendingWorkspace,
    voiceApiExports: {
      moveMemoirChapterMaterial: async (...args) => calls.push(args),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  const pendingSection = context.data.sectionViews[1];
  assert.equal(context.data.structureExpanded, true);
  assert.equal(pendingSection.title, "待归类素材");
  assert.equal(pendingSection.expanded, true);
  assert.equal(pendingSection.visibleMaterials[0].assignmentOptions[0].id, "section-1");
  assert.equal(
    context.data.sectionStructureSummary,
    "1 个小节 · 2 条讲述 · 1 条待归类",
  );
  assert.equal(context.data.canConfirmChapter, false);

  page.toggleSection.call(context, {
    currentTarget: { dataset: { id: "section-pending" } },
  });
  assert.equal(context.data.sectionViews[1].expanded, false);

  page.moveUnassignedMaterial.call(context, {
    currentTarget: {
      dataset: { utteranceId: "utterance-pending" },
    },
    detail: { value: 0 },
  });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [[
    "project-1",
    "utterance-pending",
    "section-1",
  ]]);
  assert.equal(toasts.at(-1).title, "已选择小节");
  assert.equal(context.data.operationKind, "");
  delete global.wx;
});

test("chapter workspace confirms the current completion issue then prepares again", async () => {
  installWx();
  const calls = [];
  const confirmationSummary = summary({
    state: "needs_input",
    next_action: {
      kind: "confirm",
      title: "离家年龄有两个说法",
      detail: "请确认准确说法。",
      action_text: "确认正确说法",
      disabled: false,
      issue_id: "issue-1",
    },
    active_issue: {
      id: "issue-1",
      question: "离家年龄有两个说法",
      status: "pending",
    },
  });
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({ completion_summary: confirmationSummary }),
    voiceApiExports: {
      closeMemoirCompletionIssue: async (...args) => calls.push(["confirm", ...args]),
      prepareMemoirBookChapter: async (...args) => calls.push(["prepare", ...args]),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  page.confirmCurrentIssue.call(context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [
    ["confirm", "project-1", "issue-1", {
      outcome: "confirmed",
      note: "用户已确认按当前建议处理",
    }],
    ["prepare", "project-1", "chapter-1"],
  ]);
  delete global.wx;
});

test("chapter workspace starts completion interview in the existing voice chat", async () => {
  const { navigations } = installWx();
  const interviewSummary = summary({
    state: "needs_input",
    next_action: {
      kind: "start_interview",
      title: "离家后的第一晚是什么感受？",
      detail: "讲完后会自动继续整理。",
      action_text: "开始讲",
      disabled: false,
      issue_id: "issue-1",
    },
    active_issue: {
      id: "issue-1",
      question: "离家后的第一晚是什么感受？",
      status: "pending",
      active_session_id: null,
    },
  });
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({ completion_summary: interviewSummary }),
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  await page.runNextAction.call(context);

  assert.match(navigations[0], /pages\/voice-chat\/index/);
  assert.match(navigations[0], /voiceSessionId=session-1/);
  delete global.wx;
});

test("chapter workspace edits one small section with optimistic version control", async () => {
  const { toasts } = installWx();
  const calls = [];
  const currentWorkspace = workspace();
  currentWorkspace.draft_sections.push({
    section_id: "section-2",
    title: "抵达后的第一晚",
    section_type: "life_fragment",
    section_version: 1,
    sort_order: 1,
    content: "抵达以后，我很久没有睡着。",
    used_material_ids: ["utterance-2"],
  });
  const { page } = loadWorkspacePage({
    currentWorkspace,
    voiceApiExports: {
      reviseMemoirBookChapterDraft: async (...args) => calls.push(args),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  page.startDraftSectionEdit.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });
  page.changeDraftSectionContent.call(context, {
    detail: { value: "修改后的第一小节正文" },
  });
  page.startDraftSectionEdit.call(context, {
    currentTarget: { dataset: { id: "section-2" } },
  });
  assert.equal(context.data.editingSectionId, "section-1");
  assert.equal(context.data.editingSectionContent, "修改后的第一小节正文");
  await page.saveDraftSectionEdit.call(context);

  assert.deepEqual(calls, [[
    "project-1",
    "chapter-1",
    {
      expected_version_id: "version-1",
      sections: [
        {
          section_id: "section-1",
          content: "修改后的第一小节正文",
        },
        {
          section_id: "section-2",
          content: "抵达以后，我很久没有睡着。",
        },
      ],
    },
  ]]);
  assert.equal(toasts[0].title, "小节已保存");
  delete global.wx;
});

test("chapter workspace sends a typed instruction to edit one draft section", async () => {
  const { toasts } = installWx();
  const calls = [];
  const ttsCalls = [];
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      reviseMemoirChapterDraftSectionWithAI: async (...args) => {
        calls.push(args);
        return workspace({
          draft_version_id: "version-2",
          draft_version: 2,
        });
      },
      createMemoirBookChapterDraftSectionTTS: async (...args) => {
        ttsCalls.push(args);
        return { audio_file_ids: ["audio-1"] };
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  page.startDraftSectionAIEdit.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });
  assert.equal(context.data.editingMode, "ai");
  assert.equal(context.data.editingSectionId, "section-1");
  await page.playDraftSection.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });
  assert.deepEqual(ttsCalls, []);
  page.changeDraftSectionAIInstruction.call(context, {
    detail: { value: "保留离家的事实，把语气写得更平实。" },
  });
  await page.submitDraftSectionAIInstruction.call(context);

  assert.deepEqual(calls, [[
    "project-1",
    "chapter-1",
    "section-1",
    "version-1",
    "保留离家的事实，把语气写得更平实。",
  ]]);
  assert.equal(toasts.at(-1).title, "已按要求修改");
  assert.equal(context.data.editingMode, "");
  assert.equal(context.data.editingSectionId, "");
  assert.equal(context.data.draftSectionAIInstruction, "");
  assert.equal(context.data.draftSectionVoicePhase, "idle");
  delete global.wx;
});

test("chapter workspace keeps the instruction when AI makes no change", async () => {
  const { toasts } = installWx();
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      reviseMemoirChapterDraftSectionWithAI: async () => workspace(),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });
  page.startDraftSectionAIEdit.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });
  page.changeDraftSectionAIInstruction.call(context, {
    detail: { value: "再多写一些没有提供的细节" },
  });

  await page.submitDraftSectionAIInstruction.call(context);

  assert.equal(context.data.editingMode, "ai");
  assert.equal(
    context.data.draftSectionAIInstruction,
    "再多写一些没有提供的细节",
  );
  assert.equal(context.data.draftSectionVoicePhase, "idle");
  assert.match(context.data.error, /没有改动/);
  assert.deepEqual(toasts, []);
  assert.equal(context.data.operationKind, "");
  delete global.wx;
});

test("chapter workspace transcribes a voice instruction before editing one section", async () => {
  installWx();
  const calls = [];
  const phases = [];
  let recordOptions;
  let context;
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      reviseMemoirChapterDraftSectionWithAI: async (...args) => {
        calls.push(args);
        return workspace({
          draft_version_id: "version-2",
          draft_version: 2,
        });
      },
    },
    voiceSearchExports: {
      recordMemoirVoiceSearchQuery: async (options) => {
        recordOptions = options;
        phases.push(context.data.draftSectionVoicePhase);
        options.onRecordingEnd();
        phases.push(context.data.draftSectionVoicePhase);
        return "删掉重复的句子，结尾写得克制一点。";
      },
    },
  });
  context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });
  page.startDraftSectionAIEdit.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });

  await page.toggleDraftSectionVoiceInstruction.call(context);

  assert.equal(recordOptions.stopMode, "manual");
  assert.equal(recordOptions.maxDurationMs, 30000);
  assert.deepEqual(phases, ["listening", "transcribing"]);
  assert.equal(
    context.data.draftSectionAIInstruction,
    "删掉重复的句子，结尾写得克制一点。",
  );
  assert.deepEqual(calls, []);

  await page.submitDraftSectionAIInstruction.call(context);

  assert.deepEqual(calls, [[
    "project-1",
    "chapter-1",
    "section-1",
    "version-1",
    "删掉重复的句子，结尾写得克制一点。",
  ]]);
  assert.equal(context.data.editingMode, "");
  assert.equal(context.data.draftSectionVoicePhase, "idle");
  delete global.wx;
});

test("chapter workspace reads one draft section with saved book voice settings", async () => {
  installWx();
  const requests = [];
  const played = [];
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      createMemoirBookChapterDraftSectionTTS: async (...args) => {
        requests.push(args);
        return { audio_file_ids: ["audio-1", "audio-2"] };
      },
    },
  });
  const context = pageContext(page);
  let token = 0;
  context.audioPlayback = {
    nextToken() {
      token += 1;
      return token;
    },
    currentToken() {
      return token;
    },
    stop() {
      token += 1;
    },
    async playFile(audioFileId, playToken) {
      played.push([audioFileId, playToken]);
    },
  };
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  await page.playDraftSection.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });

  assert.deepEqual(requests, [[
    "project-1",
    "chapter-1",
    "section-1",
    {
      voice: "zh_female_xiaohe_uranus_bigtts",
      speed_ratio: 1.5,
    },
  ]]);
  assert.deepEqual(played, [["audio-1", 2], ["audio-2", 2]]);
  assert.equal(context.data.audioPhase, "idle");
  assert.equal(context.data.audioSectionId, "");
  delete global.wx;
});

test("chapter workspace does not confirm a legacy flat draft without section boundaries", async () => {
  installWx();
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({ draft_sections: [] }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(context.data.canConfirmChapter, false);
  delete global.wx;
});

test("legacy flat draft brings the required next step into the empty state", async () => {
  const { navigations } = installWx();
  const interviewSummary = summary({
    state: "needs_input",
    next_action: {
      kind: "start_interview",
      title: "建议补讲一件事",
      detail: "再补充一次见面的细节。",
      action_text: "去补充",
      disabled: false,
      issue_id: "issue-legacy",
    },
    active_issue: {
      id: "issue-legacy",
      question: "第一次见面还有哪些细节？",
      status: "pending",
      active_session_id: null,
    },
  });
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({
      completion_summary: interviewSummary,
      draft_needs_rewrite: true,
      can_rewrite_draft: false,
      draft_sections: [],
    }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(context.data.draftRecoveryTitle, "先完成本章当前一步");
  assert.match(context.data.draftRecoveryDetail, /完成当前步骤/);
  assert.match(context.data.draftRecoveryDetail, /全部完成后/);
  assert.match(
    context.data.draftRecoveryDetail,
    /重新生成全部 1 个小节正文/,
  );
  assert.match(context.data.draftRecoveryDetail, /不是当前必做步骤/);
  assert.equal(context.data.draftRecoveryActionText, "去补充");
  assert.equal(context.data.draftRecoveryActionKind, "next");

  await page.runDraftRecoveryAction.call(context);

  assert.match(navigations[0], /pages\/voice-chat\/index/);
  assert.match(navigations[0], /voiceSessionId=session-1/);
  delete global.wx;
});

test("legacy flat draft exposes direct section draft regeneration when ready", async () => {
  const { toasts } = installWx();
  const calls = [];
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({
      draft_needs_rewrite: true,
      can_rewrite_draft: true,
      draft_sections: [],
    }),
    voiceApiExports: {
      rewriteMemoirBookChapter: async (...args) => calls.push(args),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(
    context.data.draftRecoveryTitle,
    "现在可以重新生成全部 1 个小节正文",
  );
  assert.equal(
    context.data.draftRecoveryActionText,
    "重新生成全部 1 个小节正文",
  );
  assert.equal(context.data.draftRecoveryActionKind, "rewrite");

  await page.runDraftRecoveryAction.call(context);
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [["project-1", "chapter-1"]]);
  assert.equal(toasts.at(-1).title, "小节正文已更新");
  delete global.wx;
});

test("chapter workspace does not confirm a draft after its section structure changes", async () => {
  installWx();
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({
      draft_needs_rewrite: true,
      can_rewrite_draft: true,
    }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(context.data.canConfirmChapter, false);
  delete global.wx;
});

test("chapter workspace explains that an old draft is preserved after regrouping", async () => {
  installWx();
  const changedSection = {
    ...workspace().sections[0],
    id: "section-new",
    title: "重新整理后的小节",
  };
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({
      draft_needs_rewrite: true,
      can_rewrite_draft: true,
      sections: [changedSection],
    }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(context.data.rewriteNoticeTitle, "现有正文仍按原来的小节保留");
  assert.match(context.data.rewriteNoticeDetail, /旧正文没有丢失/);
  assert.match(context.data.rewriteNoticeDetail, /当前 1 个小节/);
  delete global.wx;
});

test("chapter workspace confirms the current structured draft without an assembly step", async () => {
  const { toasts } = installWx();
  const calls = [];
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      confirmMemoirBookChapterDraft: async (...args) => calls.push(args),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  await page.confirmChapter.call(context);

  assert.deepEqual(calls, [["project-1", "chapter-1"]]);
  assert.equal(toasts[0].title, "本章正文已确认");
  delete global.wx;
});

test("open completion work keeps an existing draft from being confirmed", async () => {
  installWx();
  const interviewSummary = summary({
    state: "needs_input",
    next_action: {
      kind: "start_interview",
      title: "离家后的第一晚是什么感受？",
      detail: "讲完后会自动继续整理。",
      action_text: "开始讲",
      disabled: false,
      issue_id: "issue-1",
    },
    active_issue: {
      id: "issue-1",
      question: "离家后的第一晚是什么感受？",
      status: "pending",
      active_session_id: null,
    },
  });
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({ completion_summary: interviewSummary }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  assert.equal(context.data.workspace.draft_content, "十八岁那年，我第一次离开家。");
  assert.equal(context.data.canConfirmChapter, false);
  delete global.wx;
});

test("chapter workspace can rewrite with completion answers instead of forcing another interview", async () => {
  const { toasts, modals } = installWx();
  const calls = [];
  const baseWorkspace = workspace();
  const currentSections = Array.from({ length: 4 }, (_, index) => ({
    ...baseWorkspace.sections[0],
    id: `section-${index + 1}`,
    title: `当前小节 ${index + 1}`,
    sort_order: index,
    materials: [
      {
        ...baseWorkspace.sections[0].materials[0],
        utterance_id: `utterance-${index + 1}`,
        text: `第 ${index + 1} 个小节的讲述。`,
      },
    ],
  }));
  const staleWorkspace = workspace({
    draft_needs_rewrite: true,
    can_rewrite_draft: true,
    pending_completion_source_count: 2,
    sections: currentSections,
    completion_summary: summary({
      state: "needs_input",
      next_action: {
        kind: "start_interview",
        title: "还可以继续补充",
        detail: "也可以先按现有素材整理。",
        action_text: "继续补充",
        disabled: false,
        issue_id: "issue-3",
      },
      active_issue: {
        id: "issue-3",
        question: "还记得更多细节吗？",
        status: "pending",
        active_session_id: null,
      },
    }),
  });
  const { page } = loadWorkspacePage({
    currentWorkspace: staleWorkspace,
    voiceApiExports: {
      rewriteMemoirBookChapter: async (...args) => calls.push(args),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(
    context.data.rewriteNoticeTitle,
    "2 条补充回答尚未写入正文",
  );
  assert.equal(context.data.narrativeSectionCount, 4);
  assert.equal(
    context.data.rewriteDraftActionText,
    "重新生成全部 4 个小节正文",
  );
  assert.equal(
    context.data.rewriteDraftBusyText,
    "正在生成 4 个小节正文…",
  );
  assert.match(context.data.rewriteNoticeDetail, /上方当前 4 个小节/);
  assert.match(context.data.rewriteNoticeDetail, /不会重新划分小节/);
  context.data.operationKind = "prepare";
  page.rewriteChapter.call(context);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, []);
  context.data.operationKind = "";
  page.rewriteChapter.call(context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [["project-1", "chapter-1"]]);
  assert.equal(modals.at(-1).title, "重新生成全部 4 个小节正文");
  assert.match(modals.at(-1).content, /上方当前 4 个小节/);
  assert.equal(modals.at(-1).confirmText, "生成全部");
  assert.equal(toasts.at(-1).title, "小节正文已更新");
  delete global.wx;
});
