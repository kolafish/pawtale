require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  mockMemoirApiModules,
  clearModule,
  loadPageModule,
  mockModule,
  pageContext,
  unmockedAsync,
} = require("./page-harness");

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
    completion_summary: summary(),
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

function sectionRegenerationJob(overrides = {}) {
  return {
    job_id: "job-1",
    project_id: "project-1",
    chapter_id: "chapter-1",
    status: "running",
    stage: "assigning_materials",
    material_count: 1,
    processed_material_count: 0,
    total_char_count: 14,
    processed_char_count: 0,
    batch_count: 1,
    completed_batch_count: 0,
    assigned_count: 0,
    unassigned_count: 0,
    section_count: 0,
    requested_ai_model: "claude-opus-4-8",
    resolved_ai_model: "claude-opus-4-8",
    can_cancel: true,
    error_message: "",
    ...overrides,
  };
}

function chapterPreparationJob(overrides = {}) {
  return {
    job_id: null,
    project_id: "project-1",
    chapter_id: "chapter-1",
    operation: "prepare",
    status: "idle",
    stage: "idle",
    total_section_count: 1,
    checked_section_count: 0,
    completed_section_count: 0,
    resumed_section_count: 0,
    current_section_id: null,
    current_section_title: "",
    outcome: "",
    requested_ai_model: null,
    resolved_ai_model: null,
    can_cancel: false,
    error_message: "",
    ...overrides,
  };
}

function completionIssue(overrides = {}) {
  return {
    id: "issue-1",
    project_id: "project-1",
    scope: "chapter",
    chapter_id: "chapter-1",
    issue_type: "meaning",
    channel: "interview",
    question: "S1之后的第一晚是什么感受？",
    reason: "补充S1之后的感受会让这一段更丰富。",
    priority: "medium",
    related_material_ids: [],
    input_signature: "signature-1",
    status: "open",
    outcome_note: "",
    created_at: "2026-07-27T00:00:00",
    updated_at: "2026-07-27T00:00:00",
    ...overrides,
  };
}

function loadWorkspacePage({
  currentWorkspace = workspace(),
  voiceApiExports = {},
  consent = true,
  cacheExports = {},
} = {}) {
  mockMemoirApiModules({
    getMemoirChapterWorkspace: async () => currentWorkspace,
    getMemoirChapterSectionRegenerationJob: async () => ({
      job_id: null,
      project_id: "project-1",
      chapter_id: "chapter-1",
      status: "idle",
    }),
    getMemoirChapterPreparationJob: async () => chapterPreparationJob(),
    enqueueMemoirChapterPreparationJob: async () => chapterPreparationJob({
      job_id: "chapter-job-1",
      status: "pending",
      stage: "queued",
      can_cancel: true,
    }),
    cancelMemoirChapterPreparationJob: async () => chapterPreparationJob({
      job_id: "chapter-job-1",
      status: "cancelled",
    }),
    regenerateMemoirChapterSections: async () => {},
    mergeMemoirChapterSections: async () => {},
    renameMemoirChapterSection: async () => {},
    cancelMemoirChapterSectionRegenerationJob: async () => {},
    placeMemoirChapterMaterial: async () => {},
    closeMemoirCompletionIssue: async () => {},
    listMemoirCompletionIssues: async () => ({
      project_id: "project-1",
      issues: [],
    }),
    createMemoirVoiceSession: async () => ({ session: { id: "session-1" } }),
    reviseMemoirBookChapterDraft: async () => ({}),
    confirmMemoirBookChapterDraft: async () => ({}),
    createMemoirBookChapterDraftTTS: async () => ({
      audio_file_ids: [],
    }),
    createMemoirBookChapterDraftSectionTTS: async () => ({
      audio_file_ids: [],
    }),
    ...voiceApiExports,
  });
  mockModule("../services/navigation.ts", {
    redirectToBook: () => wx.redirectTo({ url: "/pages/book/index" }),
    returnToBook: () => wx.redirectTo({ url: "/pages/book/index" }),
    routeAuthError: () => false,
  });
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
  mockModule("../services/memoir-api-client.ts", {
    memoirProjectDictationUrl: () =>
      "ws://backend.local/api/memoir/projects/project-1/dictation",
  });
  clearModule("../pages/chapter-workspace/chapter-workspace-draft-workflow.ts");
  clearModule("../pages/chapter-workspace/chapter-workspace-job-workflow.ts");
  clearModule("../pages/chapter-workspace/chapter-workspace-model.ts");
  return loadPageModule("../pages/chapter-workspace/index.ts");
}

function installWx({ editableContent = "新小节" } = {}) {
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
      options.success?.({
        confirm: true,
        cancel: false,
        content: options.editable ? editableContent : undefined,
      });
    },
  };
  return { navigations, navigationTitles, toasts, modals, scrolls };
}

test("chapter workspace loads a chapter draft with visible small sections", async () => {
  const { navigationTitles, navigations } = installWx();
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
  assert.equal(context.data.chapterHeroKicker, "这一章已经整理成");
  assert.equal(context.data.chapterStoryCountText, "1 个故事");
  assert.equal(context.data.chapterHasReadableDraft, true);
  assert.equal(context.data.chapterEditingHint, "");
  assert.equal(context.data.sectionStructureSummary, "1 个故事 · 1 段回忆");
  assert.equal(context.data.sectionViews[0].title, "离家前的准备");
  assert.equal(context.data.sectionViews[0].indexText, "01");
  assert.equal(context.data.sectionViews[0].typeText, "一段经历");
  assert.equal(context.data.sectionViews[0].expanded, false);
  assert.equal(context.data.sectionViews[0].metaText, "1 段回忆");
  assert.equal(context.data.sectionViews[0].draftContent, "十八岁那年，我第一次离开家。");
  assert.equal(context.data.canConfirmChapter, true);
  assert.equal(context.data.editingToolsExpanded, false);
  assert.deepEqual(navigationTitles, ["第一次离家"]);
  assert.doesNotMatch(
    JSON.stringify(context.data.workspace),
    /contract|nodes|draft_blocks|answer_updates/,
  );

  page.toggleEditingTools.call(context);
  assert.equal(context.data.editingToolsExpanded, true);

  page.openStorySection.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });
  assert.equal(
    navigations.at(-1),
    "/pages/chapter-workspace/index?projectId=project-1&chapterId=chapter-1&sectionId=section-1",
  );
});

test("chapter workspace opens a focused section selected from the story tree", async () => {
  const base = workspace();
  const currentWorkspace = workspace({
    sections: [
      ...base.sections,
      {
        ...base.sections[0],
        id: "section-2",
        title: "抵达陌生城市",
        sort_order: 1,
        materials: [{
          ...base.sections[0].materials[0],
          utterance_id: "utterance-2",
        }],
      },
    ],
  });
  const { navigationTitles, navigations, scrolls } = installWx();
  const { page } = loadWorkspacePage({
    currentWorkspace,
    voiceApiExports: {
      listMemoirCompletionIssues: async () => ({
        project_id: "project-1",
        issues: [
          completionIssue(),
          completionIssue({ id: "issue-required", channel: "manual_review" }),
        ],
      }),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
    sectionId: "section-1",
  });

  assert.equal(context.data.focusedSectionId, "section-1");
  assert.equal(context.data.focusedSectionTitle, "离家前的准备");
  assert.equal(context.data.sectionViews.length, 1);
  assert.equal(context.data.sectionViews[0].expanded, true);
  assert.equal(context.data.expandedSectionIds["section-1"], true);
  assert.equal(context.data.requiredIssues.length, 0);
  assert.equal(context.data.optionalIssues.length, 1);
  assert.deepEqual(scrolls, []);
  assert.deepEqual(navigationTitles, ["离家前的准备"]);

  page.openMaterialEditor.call(context, {
    currentTarget: { dataset: { utteranceId: "utterance-1" } },
  });
  assert.equal(
    navigations.at(-1),
    "/pages/transcript-detail/index?id=utterance-1",
  );

  page.openFocusedSectionMedia.call(context);
  assert.equal(
    navigations.at(-1),
    "/pages/image-assets/index?projectId=project-1&scope=section&chapterId=chapter-1&sectionId=section-1",
  );
});

test("chapter workspace falls back to full chapter for a stale section link", async () => {
  installWx();
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      listMemoirCompletionIssues: async () => ({
        project_id: "project-1",
        issues: [completionIssue({ channel: "manual_review" })],
      }),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
    sectionId: "stale-section",
  });

  assert.equal(context.data.focusedSectionId, "");
  assert.equal(context.data.sectionViews.length, 1);
  assert.equal(context.data.requiredIssues.length, 1);
});

test("chapter workspace template opens assigned materials in their existing editor", () => {
  const template = fs.readFileSync(
    path.join(__dirname, "../pages/chapter-workspace/index.wxml"),
    "utf8",
  );
  const materialStart = template.indexOf('wx:for="{{item.visibleMaterials}}"');
  const materialEnd = template.indexOf("</picker>", materialStart);
  const materialTemplate = template.slice(materialStart, materialEnd);

  assert.match(materialTemplate, /bindtap="openMaterialEditor"/);
  assert.doesNotMatch(
    materialTemplate,
    /wx:if="\{\{item\.requires_user_assignment\}\}"[\s\S]*bindtap="openMaterialEditor"/,
  );
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
  assert.equal(context.data.nextAction.kind, "review_draft");
});

test("chapter workspace uses one expansion state for materials and draft", async () => {
  installWx();
  const { page } = loadWorkspacePage();
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(context.data.sectionViews[0].expanded, false);
  page.toggleSection.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });
  assert.equal(context.data.sectionViews[0].expanded, true);
  assert.equal(context.data.sectionViews[0].visibleMaterials.length, 1);
  assert.equal(context.data.sectionViews[0].hasDraft, true);
  page.toggleSection.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });
  assert.equal(context.data.sectionViews[0].expanded, false);
});

test("chapter workspace maps drafts by section id and keeps obsolete drafts separate", async () => {
  installWx();
  const base = workspace();
  const currentWorkspace = workspace({
    draft_needs_rewrite: true,
    can_rewrite_draft: true,
    sections: [
      { ...base.sections[0], version: 2 },
      {
        ...base.sections[0],
        id: "section-2",
        title: "抵达陌生城市",
        sort_order: 1,
        version: 1,
      },
    ],
    draft_sections: [
      {
        ...base.draft_sections[0],
        section_id: "old-section",
        title: "旧的小节",
        content: "这段旧正文只应保留在历史版本。",
      },
      {
        ...base.draft_sections[0],
        content: "这一版正文对应第一小节。",
      },
    ],
  });
  const { page } = loadWorkspacePage({ currentWorkspace });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(context.data.sectionViews[0].draftContent, "这一版正文对应第一小节。");
  assert.equal(context.data.sectionViews[0].draftNeedsUpdate, true);
  assert.equal(context.data.sectionViews[1].hasDraft, false);
  assert.equal(context.data.sectionViews[1].draftContent, "");
  assert.equal(context.data.retainedDraftSectionCount, 1);
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
});

test("chapter workspace protects unsaved edits before returning to the book flow", () => {
  const redirects = [];
  const modalResults = [
    { confirm: false, cancel: true },
    { confirm: true, cancel: false },
  ];
  global.wx = {
    navigateBack(options) {
      options.fail?.();
    },
    redirectTo(options) {
      redirects.push(options.url);
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
  assert.deepEqual(redirects, []);

  page.returnToBookFlow.call(context);
  assert.deepEqual(redirects, ["/pages/book/index"]);
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
});

test("chapter workspace renames a section through the existing section update API", async () => {
  const { toasts } = installWx({ editableContent: "出发前的准备" });
  const calls = [];
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      renameMemoirChapterSection: async (...args) => calls.push(args),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  await page.renameSection.call(context, {
    currentTarget: {
      dataset: {
        id: "section-1",
        title: "离家前的准备",
        version: "1",
      },
    },
  });

  assert.deepEqual(calls, [[
    "project-1",
    "section-1",
    "出发前的准备",
    1,
  ]]);
  assert.equal(toasts.at(-1).title, "小节名称已更新");
  assert.equal(context.data.operationKind, "");
});

test("confirmed chapter can reopen its editing tools and merge with the previous story", async () => {
  const { modals, toasts } = installWx();
  const calls = [];
  const base = workspace();
  const confirmedWorkspace = workspace({
    completion_summary: summary({
      state: "confirmed",
      headline: "本章已经确认",
    }),
    draft_status: "confirmed",
    sections: [
      ...base.sections,
      {
        ...base.sections[0],
        id: "section-2",
        title: "抵达陌生城市",
        sort_order: 1,
        materials: [{
          ...base.sections[0].materials[0],
          utterance_id: "utterance-2",
          text: "到站以后，我第一次看见那么大的城市。",
        }],
      },
    ],
  });
  const { page } = loadWorkspacePage({
    currentWorkspace: confirmedWorkspace,
    voiceApiExports: {
      mergeMemoirChapterSections: async (...args) => calls.push(args),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(context.data.sectionViews[1].mergeTargetId, "section-1");
  page.toggleEditingTools.call(context);
  assert.equal(context.data.editingToolsExpanded, true);

  page.mergeSectionIntoPrevious.call(context, {
    currentTarget: {
      dataset: {
        sourceId: "section-2",
        sourceTitle: "抵达陌生城市",
        targetId: "section-1",
        targetTitle: "离家前的准备",
      },
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [["project-1", "section-1", "section-2"]]);
  assert.equal(modals.at(-1).title, "与上一小节合并？");
  assert.equal(toasts.at(-1).title, "小节已合并");
});

test("chapter workspace regenerates the current section structure after confirmation", async () => {
  const { toasts } = installWx();
  const calls = [];
  let jobReads = 0;
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      regenerateMemoirChapterSections: async (...args) => {
        calls.push(args);
        return sectionRegenerationJob();
      },
      getMemoirChapterSectionRegenerationJob: async () => (
        ++jobReads === 1
          ? sectionRegenerationJob({ job_id: null, status: "idle" })
          : sectionRegenerationJob({
            status: "succeeded",
            stage: "completed",
            processed_material_count: 1,
            processed_char_count: 14,
            completed_batch_count: 1,
            assigned_count: 1,
            section_count: 2,
            can_cancel: false,
          })
      ),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  page.regenerateSections.call(context);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(context.data.operationKind, "regenerate_sections");
  assert.equal(context.data.sectionRegenerationTitle, "正在归类讲述");
  await page.refreshSectionRegenerationJob.call(context);

  assert.deepEqual(calls, [[
    "project-1",
    "chapter-1",
    [{ id: "section-1", version: 1 }],
  ]]);
  assert.equal(toasts.at(-1).title, "已整理为2个小节");
  assert.equal(context.data.operationKind, "");
  assert.equal(context.data.sectionRegenerationJob, null);
});

test("chapter workspace resumes and cancels an active section regeneration job", async () => {
  const { toasts } = installWx();
  const cancelled = [];
  const activeJob = sectionRegenerationJob({
    processed_material_count: 1,
    processed_char_count: 7,
  });
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      getMemoirChapterSectionRegenerationJob: async () => activeJob,
      cancelMemoirChapterSectionRegenerationJob: async (...args) => {
        cancelled.push(args);
        return sectionRegenerationJob({
          status: "cancelled",
          stage: "cancelled",
          can_cancel: false,
        });
      },
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });
  assert.equal(context.data.operationKind, "regenerate_sections");
  assert.equal(context.data.sectionRegenerationProgress, 50);
  assert.equal(context.data.sectionRegenerationProgressMode, "determinate");

  await page.cancelSectionRegeneration.call(context);

  assert.deepEqual(cancelled, [["project-1", "chapter-1", "job-1"]]);
  assert.equal(context.data.operationKind, "");
  assert.equal(context.data.sectionRegenerationJob, null);
  assert.equal(toasts.at(-1).title, "已取消整理");
});

test("section regeneration opens pending material instead of running a stale next action", async () => {
  const { modals, scrolls } = installWx();
  const prepareCalls = [];
  let loadCount = 0;
  let jobReads = 0;
  const pendingWorkspace = workspaceWithPendingMaterial({
    draft_needs_rewrite: true,
  });
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      getMemoirChapterWorkspace: async () => {
        loadCount += 1;
        return loadCount === 1 ? workspace() : pendingWorkspace;
      },
      regenerateMemoirChapterSections: async () => sectionRegenerationJob({
        material_count: 2,
        total_char_count: 28,
      }),
      getMemoirChapterSectionRegenerationJob: async () => (
        ++jobReads === 1
          ? sectionRegenerationJob({ job_id: null, status: "idle" })
          : sectionRegenerationJob({
            status: "succeeded",
            stage: "completed",
            material_count: 2,
            processed_material_count: 2,
            total_char_count: 28,
            processed_char_count: 28,
            completed_batch_count: 1,
            assigned_count: 1,
            unassigned_count: 1,
            section_count: 1,
            can_cancel: false,
          })
      ),
      enqueueMemoirChapterPreparationJob: async (...args) => {
        prepareCalls.push(args);
        return chapterPreparationJob({
          job_id: "chapter-job-1",
          status: "pending",
          stage: "queued",
          can_cancel: true,
        });
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
  await page.refreshSectionRegenerationJob.call(context);

  assert.deepEqual(
    modals.map((modal) => modal.title),
    ["重新整理小节分组", "有讲述暂未归类"],
  );
  assert.deepEqual(prepareCalls, []);
  assert.notEqual(context.data.nextAction.kind, "review_sections");
  assert.equal(context.data.sectionViews.at(-1).expanded, true);
  assert.equal(scrolls.at(-1), "#chapter-sections");
});

test("chapter workspace keeps pending materials optional and opens their editor", async () => {
  const { navigations, toasts } = installWx();
  const calls = [];
  const pendingWorkspace = workspaceWithPendingMaterial();
  const { page } = loadWorkspacePage({
    currentWorkspace: pendingWorkspace,
    voiceApiExports: {
      placeMemoirChapterMaterial: async (...args) => calls.push(args),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  const pendingSection = context.data.sectionViews[1];
  assert.equal(pendingSection.title, "待归类素材");
  assert.equal(pendingSection.expanded, true);
  assert.equal(pendingSection.typeText, "暂不纳入正文");
  assert.equal(pendingSection.hasDraft, false);
  assert.equal(pendingSection.draftContent, "");
  assert.match(pendingSection.assignmentHint, /不会纳入本章正文/);
  assert.match(pendingSection.assignmentHint, /编辑或删除/);
  assert.equal(pendingSection.visibleMaterials[0].assignmentOptions[0].id, "section-1");
  assert.equal(
    pendingSection.visibleMaterials[0].assignmentOptions.at(-1).action,
    "create_section",
  );
  assert.equal(
    context.data.sectionStructureSummary,
    "1 个故事 · 1 段回忆 · 1 段待整理",
  );
  assert.equal(context.data.chapterEditingHint, "1 段回忆待整理");
  assert.equal(context.data.canConfirmChapter, true);

  page.openMaterialEditor.call(context, {
    currentTarget: {
      dataset: { utteranceId: "utterance/pending id" },
    },
  });
  assert.equal(
    navigations.at(-1),
    "/pages/transcript-detail/index?id=utterance%2Fpending%20id",
  );

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
    { target_section_id: "section-1" },
  ]]);
  assert.equal(toasts.at(-1).title, "已选择小节");
  assert.equal(context.data.operationKind, "");
});

test("chapter workspace creates a small section while assigning pending material", async () => {
  const { toasts } = installWx({ editableContent: "山桃花开的心动" });
  const calls = [];
  const { page } = loadWorkspacePage({
    currentWorkspace: workspaceWithPendingMaterial(),
    voiceApiExports: {
      placeMemoirChapterMaterial: async (...args) => calls.push(args),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  const options = context.data.sectionViews[1]
    .visibleMaterials[0]
    .assignmentOptions;
  page.moveUnassignedMaterial.call(context, {
    currentTarget: {
      dataset: { utteranceId: "utterance-pending" },
    },
    detail: { value: options.length - 1 },
  });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [[
    "project-1",
    "utterance-pending",
    {
      target_chapter_id: "chapter-1",
      new_section_title: "山桃花开的心动",
    },
  ]]);
  assert.equal(toasts.at(-1).title, "已新建小节");
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
      enqueueMemoirChapterPreparationJob: async (...args) => {
        calls.push(["prepare", ...args]);
        return chapterPreparationJob({
          job_id: "chapter-job-1",
          status: "pending",
          stage: "queued",
          can_cancel: true,
        });
      },
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
    ["prepare", "project-1", "chapter-1", "prepare"],
  ]);
});

test("chapter workspace separates optional suggestions from required decisions", async () => {
  installWx();
  const optionalIssue = completionIssue();
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({
      completion_summary: summary({ detail: "S1之后需要补充。" }),
    }),
    voiceApiExports: {
      listMemoirCompletionIssues: async () => ({
        project_id: "project-1",
        issues: [optionalIssue],
      }),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  assert.equal(context.data.requiredIssues.length, 0);
  assert.equal(context.data.optionalIssues.length, 1);
  assert.equal(
    context.data.readinessDetail,
    "第1小节“离家前的准备”之后需要补充。",
  );
  assert.equal(
    context.data.optionalIssues[0].question,
    "第1小节“离家前的准备”之后的第一晚是什么感受？",
  );
  assert.equal(
    context.data.optionalIssues[0].reason,
    "补充第1小节“离家前的准备”之后的感受会让这一段更丰富。",
  );
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
});

test("chapter workspace opens the shared revision workbench for AI edits", async () => {
  const { navigations } = installWx();
  const { page } = loadWorkspacePage();
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  page.startDraftSectionAIEdit.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });
  assert.deepEqual(
    navigations,
    [
      "/pages/revision-workbench/index?scope=chapter_section&projectId=project-1&chapterId=chapter-1&sectionId=section-1&sectionTitle=%E7%A6%BB%E5%AE%B6%E5%89%8D%E7%9A%84%E5%87%86%E5%A4%87",
    ],
  );
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
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });
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
  assert.equal(context.data.audioTarget, "");
});

test("chapter workspace opens the full chapter reading preview", async () => {
  const { navigations } = installWx();
  const { page } = loadWorkspacePage();
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  page.openChapterPreview.call(context);

  assert.deepEqual(navigations, [
    "/pages/book-reader/index?projectId=project-1&chapterId=chapter-1",
  ]);
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
      enqueueMemoirChapterPreparationJob: async (...args) => {
        calls.push(args);
        return chapterPreparationJob({
          job_id: "chapter-job-1",
          operation: "rewrite",
          status: "pending",
          stage: "queued",
          can_cancel: true,
        });
      },
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(
    context.data.draftRecoveryTitle,
    "现在可以更新 1 个有变化的小节正文",
  );
  assert.equal(
    context.data.draftRecoveryActionText,
    "更新 1 个有变化的小节正文",
  );
  assert.equal(context.data.draftRecoveryActionKind, "rewrite");

  await page.runDraftRecoveryAction.call(context);
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [["project-1", "chapter-1", "rewrite"]]);
  assert.equal(context.data.chapterPreparationTitle, "正文更新任务已排队");
  assert.equal(context.data.chapterPreparationProgressMode, "indeterminate");
  assert.equal(toasts.length, 0);
});

test("chapter rewrite reports persisted section progress at its action surface", async () => {
  installWx();
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({
      draft_needs_rewrite: true,
      can_rewrite_draft: true,
    }),
    voiceApiExports: {
      getMemoirChapterPreparationJob: async () => chapterPreparationJob({
        job_id: "chapter-job-1",
        operation: "rewrite",
        status: "running",
        stage: "generating_sections",
        total_section_count: 9,
        completed_section_count: 3,
        current_section_id: "section-4",
        current_section_title: "院子里的雨声",
        can_cancel: true,
      }),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(context.data.operationKind, "rewrite_chapter");
  assert.equal(context.data.chapterPreparationTitle, "正在更新有变化的小节");
  assert.match(context.data.chapterPreparationDetail, /已更新 3\/9 个小节/);
  assert.match(context.data.chapterPreparationDetail, /院子里的雨声/);
  assert.equal(context.data.chapterPreparationProgress, 33);
  assert.equal(context.data.chapterPreparationProgressMode, "determinate");
  page.onUnload.call(context);
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
  assert.match(
    context.data.rewriteNoticeDetail,
    /当前 1 个素材或结构发生变化的小节/,
  );
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
});

test("required review keeps an existing draft from being confirmed", async () => {
  installWx();
  const reviewSummary = summary({
    state: "needs_input",
    next_action: {
      kind: "confirm",
      title: "需要确认真实姓名是否保留",
      detail: "涉及隐私边界。",
      action_text: "确认",
      disabled: false,
      issue_id: "issue-1",
    },
    active_issue: {
      id: "issue-1",
      question: "是否保留真实姓名？",
      status: "pending",
    },
  });
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({ completion_summary: reviewSummary }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  assert.equal(context.data.workspace.draft_content, "十八岁那年，我第一次离开家。");
  assert.equal(context.data.canConfirmChapter, false);
});

test("chapter workspace derives readable review text without mutating the API response", () => {
  installWx();
  const apiWorkspace = workspace({
    completion_summary: summary({
      detail: "请核对 S1。",
      next_action: {
        ...summary().next_action,
        detail: "先查看 S1。",
      },
      active_issue: {
        id: "issue-1",
        question: "S1 是否准确？",
        status: "pending",
      },
    }),
  });
  const { page } = loadWorkspacePage({ currentWorkspace: apiWorkspace });
  const context = pageContext(page);

  page.applyWorkspace.call(context, apiWorkspace);

  assert.equal(apiWorkspace.completion_summary.detail, "请核对 S1。");
  assert.equal(apiWorkspace.completion_summary.next_action.detail, "先查看 S1。");
  assert.equal(apiWorkspace.completion_summary.active_issue.question, "S1 是否准确？");
  assert.match(context.data.workspace.completion_summary.detail, /第1小节/);
  assert.match(context.data.workspace.completion_summary.active_issue.question, /第1小节/);
});

test("chapter workspace can rewrite while optional suggestions remain open", async () => {
  const { toasts, modals } = installWx();
  const calls = [];
  const baseWorkspace = workspace();
  const currentSections = Array.from({ length: 4 }, (_, index) => ({
    ...baseWorkspace.sections[0],
    id: `section-${index + 1}`,
    title: `当前小节 ${index + 1}`,
    sort_order: index,
    version: index === 2 ? 2 : 1,
    materials: [
      {
        ...baseWorkspace.sections[0].materials[0],
        utterance_id: `utterance-${index + 1}`,
        text: `第 ${index + 1} 个小节的讲述。`,
      },
    ],
  }));
  const draftSections = currentSections.map((section) => ({
    section_id: section.id,
    title: section.title,
    section_type: section.section_type,
    section_version: 1,
    sort_order: section.sort_order,
    content: `${section.title}原有正文。`,
    used_material_ids: [section.materials[0].utterance_id],
  }));
  const staleWorkspace = workspace({
    draft_needs_rewrite: true,
    can_rewrite_draft: true,
    pending_completion_source_count: 1,
    sections: currentSections,
    draft_sections: draftSections,
    completion_summary: summary(),
  });
  const { page } = loadWorkspacePage({
    currentWorkspace: staleWorkspace,
    voiceApiExports: {
      enqueueMemoirChapterPreparationJob: async (...args) => {
        calls.push(args);
        return chapterPreparationJob({
          job_id: "chapter-job-1",
          operation: "rewrite",
          status: "pending",
          stage: "queued",
          can_cancel: true,
        });
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(
    context.data.rewriteNoticeTitle,
    "1 条补充回答尚未写入正文",
  );
  assert.equal(context.data.narrativeSectionCount, 4);
  assert.equal(
    context.data.rewriteDraftActionText,
    "更新 1 个有变化的小节正文",
  );
  assert.equal(
    context.data.rewriteDraftBusyText,
    "正在更新 1 个小节正文…",
  );
  assert.match(context.data.rewriteNoticeDetail, /只重新生成当前 1 个/);
  assert.match(context.data.rewriteNoticeDetail, /其他小节沿用现有正文/);
  context.data.operationKind = "prepare";
  page.rewriteChapter.call(context);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, []);
  context.data.operationKind = "";
  page.rewriteChapter.call(context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [["project-1", "chapter-1", "rewrite"]]);
  assert.equal(modals.at(-1).title, "更新 1 个有变化的小节正文");
  assert.match(modals.at(-1).content, /只重新生成当前 1 个/);
  assert.equal(modals.at(-1).confirmText, "更新正文");
  assert.equal(context.data.chapterPreparationTitle, "正文更新任务已排队");
  assert.equal(context.data.chapterPreparationProgressMode, "indeterminate");
  assert.equal(toasts.length, 0);
});

test("candidate remains separate from edited draft and only acceptance writes it", async () => {
  let accepted = 0;
  const candidate = chapterPreparationJob({ job_id: "candidate-job", status: "succeeded", outcome: "needs_review",
    candidate_content: "十八岁那年，我第一次坐上长途火车。",
    candidate_base_version_id: "version-1",
    candidate_diff_segments: [
      { kind: "equal", text: "十八岁那年，我第一次" },
      { kind: "deleted", text: "离开家" },
      { kind: "modified", text: "坐上长途火车" },
      { kind: "equal", text: "。" },
    ],
    candidate_sections: [{
      section_id: "section-1",
      title: "离家前的准备",
      content: "十八岁那年，我第一次坐上长途火车。",
    }],
  });
  const { page } = loadWorkspacePage({ voiceApiExports: {
    acceptMemoirChapterCandidate: async (projectId, chapterId, jobId) => {
      assert.deepEqual([projectId, chapterId, jobId], ["project-1", "chapter-1", "candidate-job"]);
      accepted++;
    },
  } });
  const context = pageContext(page, { projectId: "project-1", chapterId: "chapter-1", workspace: workspace() });
  context.pageVisible = true;
  context.loadPageData = async () => {};
  global.wx = { showToast() {} };
  await context.applyChapterPreparationJob(candidate, true);
  assert.equal(context.data.chapterPreparationJob.candidate_content, candidate.candidate_content);
  assert.equal(context.data.workspace.draft_content, "十八岁那年，我第一次离开家。");
  assert.equal(context.data.candidateComparisonAvailable, true);
  assert.equal(context.data.candidateMode, "changes");
  assert.equal(context.data.candidatePreviewVersion, "after");
  assert.match(context.data.candidateSummaryText, /1 个小节有变化/);
  assert.deepEqual(
    context.data.candidateDiffSegments.map(({ kind, text }) => ({ kind, text })),
    candidate.candidate_diff_segments,
  );
  context.switchCandidateView({ currentTarget: { dataset: { mode: "whole" } } });
  context.switchCandidateView({ currentTarget: { dataset: { version: "before" } } });
  assert.equal(context.data.candidateWholeContent, "十八岁那年，我第一次离开家。");
  context.switchCandidateView({ currentTarget: { dataset: { version: "after" } } });
  assert.equal(context.data.candidateWholeContent, candidate.candidate_content);
  assert.equal(accepted, 0);
  await context.acceptChapterCandidate();
  assert.equal(accepted, 1);
  assert.equal(context.data.chapterPreparationJob, null);
  assert.equal(context.data.candidateWholeContent, "");
});

test("stale candidate acceptance preserves original and surfaces conflict", async () => {
  const { page } = loadWorkspacePage({ voiceApiExports: { acceptMemoirChapterCandidate: async () => { throw Error("正文已更新，请重新检查候选稿"); } } });
  const candidate = chapterPreparationJob({ job_id: "candidate-job", status: "succeeded", outcome: "needs_review", candidate_content: "候选" });
  const context = pageContext(page, { projectId: "project-1", chapterId: "chapter-1", workspace: workspace() });
  context.pageVisible = true;
  await context.applyChapterPreparationJob(candidate, true);
  assert.equal(context.data.candidateComparisonAvailable, false);
  assert.equal(context.data.candidateMode, "whole");
  assert.equal(context.data.candidateWholeContent, "候选");
  await context.acceptChapterCandidate();
  assert.match(context.data.error, /正文已更新/);
  assert.equal(context.data.chapterPreparationJob, candidate);
  assert.equal(context.data.workspace.draft_content, "十八岁那年，我第一次离开家。");
  assert.equal(context.data.operationKind, "");
});
