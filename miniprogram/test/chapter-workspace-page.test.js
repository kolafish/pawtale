require("./register-ts");
const { memoirAudioPlanLoader } = require("../services/memoir-book-api.ts");
const { sampleAudioPlan } = require("./book-fixtures");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  flushAsyncWork,
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
    memoirAudioPlanLoader: (...args) => memoirAudioPlanLoader(...args, voiceApiExports.prepareMemoirAudio || unmockedAsync("prepareMemoirAudio")),
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
    getMemoirBookPlan: async () => ({ chapters: [{ id: "chapter-1", include_in_book: true }] }),
    saveMemoirShareCardSelection: async (_projectId, selection) => selection,
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
  clearModule("../services/reading-share.ts");
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
  assert.match(context.data.chapterSummaryText, /1 小节 · 14 字/);
  assert.equal(context.data.chapterHasReadableDraft, true);
  assert.equal(context.data.chapterEditingHint, "");
  assert.equal(context.data.sectionStructureSummary, "1 小节 · 1 段回忆");
  assert.equal(context.data.sectionViews[0].title, "离家前的准备");
  assert.equal(context.data.sectionViews[0].indexText, "01");
  assert.equal(context.data.sectionViews[0].typeText, "一段经历");
  assert.equal(context.data.sectionViews[0].expanded, false);
  assert.equal(context.data.sectionViews[0].metaText, "1 段回忆");
  assert.equal(context.data.sectionViews[0].draftContent, "十八岁那年，我第一次离开家。");
  assert.equal("canConfirmChapter" in context.data, false);
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

test("chapter workspace guides empty chapters to narration and retains authoritative failure or processing state", () => {
  const { mod } = loadWorkspacePage();
  const emptyWorkspace = workspace({
    sections: [],
    draft_id: null,
    draft_version_id: null,
    draft_content: "",
    draft_sections: [],
    completion_summary: summary({ state: "not_started" }),
  });
  const empty = mod.chapterWorkspaceViewPatch(emptyWorkspace);

  assert.equal(empty.chapterHasReadableDraft, false);
  assert.match(empty.chapterSummaryText, /暂无小节/);
  assert.doesNotMatch(empty.chapterSummaryText, /\d+ 字/);
  assert.match(empty.readinessTitle, /需要讲述素材/);
  assert.match(empty.readinessDetail, /继续讲述.*当前步骤/);
  assert.doesNotMatch(empty.chapterStoryDetail, /正在|自动/);

  for (const state of ["failed", "preparing"]) {
    const authoritativeSummary = summary({
      state,
      headline: state === "failed" ? "上次检查没有完成" : "正在检查本章",
      detail: state === "failed" ? "原始素材保留，请重新检查。" : "检查任务正在处理。",
    });
    const view = mod.chapterWorkspaceViewPatch({ ...emptyWorkspace, completion_summary: authoritativeSummary });
    assert.equal(view.readinessTitle, authoritativeSummary.headline);
    assert.equal(view.readinessDetail, authoritativeSummary.detail);
    assert.equal(view.chapterStoryDetail, authoritativeSummary.detail);
    assert.equal(view.nextAction.kind, authoritativeSummary.next_action.kind);
  }
});

test("chapter summary counts the saved draft without whitespace and excludes pending or hidden sections", () => {
  const { mod } = loadWorkspacePage();
  const data = workspaceWithPendingMaterial({ draft_content: `\n ${"故".repeat(1200)} \t\n` });
  data.sections.push({ ...data.sections[0], id: "excluded", include_in_book: false });
  const view = mod.chapterWorkspaceViewPatch(data);
  assert.equal(view.narrativeSectionCount, 1);
  assert.match(view.chapterSummaryText, /1 小节 · 1,200 字/);
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
  assert.equal(context.data.focusedSection.id, "section-1");
  assert.equal(context.data.focusedStoryExpanded, false);
  assert.equal(context.data.sectionViews.length, 1);
  assert.equal(context.data.sectionViews[0].expanded, true);
  assert.equal(context.data.expandedSectionIds["section-1"], true);
  assert.equal(context.data.requiredIssues.length, 0);
  assert.equal(context.data.optionalIssues.length, 1);
  assert.deepEqual(scrolls, []);
  assert.deepEqual(navigationTitles, ["离家前的准备"]);

  page.toggleFocusedStory.call(context);
  assert.equal(context.data.focusedStoryExpanded, false);

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

test("focused story detail keeps reading content before memory summaries", () => {
  const template = fs.readFileSync(
    path.join(__dirname, "../pages/chapter-workspace/index.wxml"),
    "utf8",
  );
  const focusedStart = template.indexOf('<block wx:if="{{focusedSectionId && focusedSection}}">');
  const editingStart = template.indexOf('<view wx:if="{{!focusedSectionId && editingToolsExpanded}}"');
  const focusedTemplate = template.slice(focusedStart, editingStart);
  const storyCard = focusedTemplate.indexOf('class="focused-story-card');
  const memoryCard = focusedTemplate.indexOf('class="focused-memories-card');

  assert.ok(focusedStart >= 0, "应复用章节工作台中的聚焦小节详情态");
  assert.ok(storyCard >= 0 && memoryCard > storyCard, "整理后的小节应先于讲过的回忆");
  assert.match(focusedTemplate, />整理后的小节/);
  assert.match(focusedTemplate, />讲过的回忆</);
  assert.match(focusedTemplate, /bindtap="toggleFocusedStory"/);
  assert.match(focusedTemplate, /draftCanExpand && !focusedStoryExpanded/);
  assert.match(focusedTemplate, /assets\/chapter-workspace\/speaker\.svg/);
  assert.match(focusedTemplate, /assets\/chapter-workspace\/pencil\.svg/);
  assert.match(focusedTemplate, /assets\/chapter-workspace\/wand\.svg/);
  assert.doesNotMatch(focusedTemplate, />这小节</);
  assert.doesNotMatch(focusedTemplate, />查看与修改/);
});

test("focused story detail expands a long organized story on demand", async () => {
  const base = workspace();
  const longContent = "十八岁那年，我第一次离开家。".repeat(24);
  installWx();
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({
      draft_sections: [{
        ...base.draft_sections[0],
        content: longContent,
      }],
    }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
    sectionId: "section-1",
  });

  assert.equal(context.data.focusedSection.draftCanExpand, true);
  assert.equal(context.data.focusedStoryExpanded, false);
  page.toggleFocusedStory.call(context);
  assert.equal(context.data.focusedStoryExpanded, true);
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

test("chapter workspace routes browse and editing story entries to one detail state", () => {
  const template = fs.readFileSync(
    path.join(__dirname, "../pages/chapter-workspace/index.wxml"),
    "utf8",
  );
  const overviewStart = template.indexOf('class="content-card story-overview-card');
  const overviewEnd = template.indexOf('class="editing-entry', overviewStart);
  const overviewTemplate = template.slice(overviewStart, overviewEnd);
  const entryMarkStart = template.indexOf('class="editing-entry-mark"');
  const entryMarkEnd = template.indexOf("</view>", entryMarkStart);
  const entryMarkTemplate = template.slice(entryMarkStart, entryMarkEnd);
  const sectionListStart = template.indexOf('class="section-list unified-section-list"');
  const regroupStart = template.indexOf('class="structure-actions"');
  const editingSectionTemplate = template.slice(sectionListStart, regroupStart);

  assert.match(
    template.slice(Math.max(0, overviewStart - 120), overviewStart),
    /!editingToolsExpanded/,
  );
  assert.doesNotMatch(overviewTemplate, /story-overview-meta/);
  assert.match(overviewTemplate, /bindtap="openStorySection"/);
  assert.match(entryMarkTemplate, /editing-entry-chevron/);
  assert.doesNotMatch(entryMarkTemplate, /收起/);
  assert.match(template, />小节与回忆</);
  assert.doesNotMatch(template, /小节、回忆与正文|>改名</);
  assert.ok(regroupStart > sectionListStart, "重新分组应位于小节编辑列表之后");
  assert.match(editingSectionTemplate, /bindtap="openStorySection"/);
  assert.match(editingSectionTemplate, />查看与编辑小节</);
  assert.doesNotMatch(editingSectionTemplate, /class="draft-section-(?:actions|body)/);
  assert.match(template, />本章正文已更新</);
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
      prepareMemoirAudio: async (...args) => {
        ttsRequests.push(args);
        return sampleAudioPlan();
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

  page.openDraftSectionEditor.call(context, sectionEvent);
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

test("chapter workspace warns on native back while a section edit is unsaved", () => {
  const alerts = [];
  global.wx = {
    enableAlertBeforeUnload(options) { alerts.push(options.message); },
    disableAlertBeforeUnload() { alerts.push("disabled"); },
  };
  const { page } = loadWorkspacePage();
  const context = pageContext(page, { workspace: workspace() });
  const event = { currentTarget: { dataset: { id: "section-1" } } };

  context.openDraftSectionEditor(event);
  context.changeDraftSectionContent({ detail: { value: "未保存的新正文" } });
  assert.match(alerts[0], /尚未保存/);

  context.cancelDraftSectionEdit();
  assert.equal(alerts.at(-1), "disabled");
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
  assert.equal(toasts.at(-1).title, "小节标题已更新");
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
  assert.equal(context.data.sectionRegenerationTitle, "正在归类回忆");
  await page.refreshSectionRegenerationJob.call(context);

  assert.deepEqual(calls, [[
    "project-1",
    "chapter-1",
    [{ id: "section-1", version: 1 }],
  ]]);
  assert.equal(toasts.at(-1).title, "已整理为2小节");
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
    ["重新整理小节分组", "有回忆暂未归类"],
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
    "1 小节 · 1 段回忆 · 1 段待整理",
  );
  assert.equal(context.data.chapterEditingHint, "1 段回忆待整理");
  assert.equal(context.data.chapterHasReadableDraft, true);

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

test("chapter workspace opens the shared answer editor before confirming a fact", async () => {
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

  const opened = [];
  context.selectComponent = () => ({ openRequiredAnswer: (issueId) => opened.push(issueId) });
  page.confirmCurrentIssue.call(context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(opened, ["issue-1"]);
  assert.deepEqual(calls, []);
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
  currentWorkspace.input_signature = "source-before-edit";
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

  page.openDraftSectionEditor.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });
  page.changeDraftSectionContent.call(context, {
    detail: { value: "修改后的第一小节正文" },
  });
  page.openDraftSectionEditor.call(context, {
    currentTarget: { dataset: { id: "section-2" } },
  });
  assert.equal(context.data.editingSectionId, "section-1");
  assert.equal(context.data.editingSectionContent, "修改后的第一小节正文");
  context.data.workspace = { ...context.data.workspace, input_signature: "source-after-refresh", draft_version_id: "version-after-refresh" };
  await page.saveDraftSectionEdit.call(context);

  assert.deepEqual(calls, [[
    "project-1",
    "chapter-1",
    {
      expected_version_id: "version-1",
      expected_input_signature: "source-before-edit",
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
      prepareMemoirAudio: async (...args) => {
        requests.push(args);
        return sampleAudioPlan(["audio-1", "audio-2"]);
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
    async prefetchFile() {},
    async playFile(audioFileId, playToken) {
      played.push([audioFileId, playToken]);
    },
  };

  await page.playDraftSection.call(context, {
    currentTarget: { dataset: { id: "section-1" } },
  });

  assert.equal(requests.length, 2);
  assert.deepEqual(requests[0], ["project-1", {
    kind: "section", chapterId: "chapter-1", sectionId: "section-1", versionId: "version-1",
  }, { voice: "linjiajiejie", speed_ratio: 1.5, start_index: 0, retry_failed: true }]);
  assert.equal(requests[1][2].start_index, 1);
  assert.equal(requests[1][2].source_text_hash, "hash");
  assert.equal(requests[1][2].version_id, "version-1");
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
  context.setData({ operationKind: "chapter_settings" });
  context.openChapterPreview();
  context.setData({ operationKind: "", editingSectionId: "section-1" });
  context.openChapterPreview();
  assert.equal(navigations.length, 1);
});

test("chapter workspace keeps a legacy flat draft readable without section boundaries", async () => {
  installWx();
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({ draft_sections: [] }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(context.data.chapterHasReadableDraft, true);
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
    "现在可以更新相关小节",
  );
  assert.equal(
    context.data.draftRecoveryActionText,
    "更新相关小节",
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
  assert.equal(context.data.chapterPreparationTitle, "正在更新相关小节");
  assert.match(context.data.chapterPreparationDetail, /已更新 3\/9 小节/);
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

  assert.equal(context.data.chapterHasReadableDraft, true);
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
    /1 小节的素材或结构发生变化/,
  );
});

test("chapter workspace reads the current structured draft without a confirmation step", async () => {
  const { navigations } = installWx();
  const { page } = loadWorkspacePage();
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });
  page.openChapterPreview.call(context);
  assert.match(navigations.at(-1), /\/pages\/book-reader\/index/);
  assert.equal(typeof page.confirmChapter, "undefined");
});

test("chapter workspace exposes current draft and update paths from the chapter summary", async () => {
  const { scrolls } = installWx();
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({
      draft_needs_rewrite: true,
      can_rewrite_draft: true,
      completion_summary: summary({ state: "not_started" }),
    }),
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  page.showDraftUpdate.call(context);
  assert.equal(context.data.editingToolsExpanded, true);
  assert.equal(scrolls.at(-1), "#chapter-sections");

  context.setData({ editingToolsExpanded: false, nextAction: { ...context.data.nextAction, kind: "review_draft" } });
  await page.runNextAction.call(context);
  assert.equal(context.data.editingToolsExpanded, true);
  assert.equal(scrolls.at(-1), "#chapter-sections");

  const template = fs.readFileSync(path.join(__dirname, "../pages/chapter-workspace/index.wxml"), "utf8");
  assert.match(template, /bindtap="showDraftUpdate"/);
  assert.doesNotMatch(template, /bindtap="confirmChapter"/);
});

test("required review keeps its decision visible alongside an existing draft", async () => {
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
  assert.equal(context.data.nextAction.kind, "confirm");
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
    "更新相关小节",
  );
  assert.equal(
    context.data.rewriteDraftBusyText,
    "正在更新相关小节…",
  );
  assert.match(context.data.rewriteNoticeDetail, /重点更新相关内容/);
  assert.match(context.data.rewriteNoticeDetail, /有联系的小节衔接/);
  context.data.operationKind = "prepare";
  page.rewriteChapter.call(context);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, []);
  context.data.operationKind = "";
  page.rewriteChapter.call(context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [["project-1", "chapter-1", "rewrite"]]);
  assert.equal(modals.at(-1).title, "更新相关小节");
  assert.match(modals.at(-1).content, /有联系的小节衔接/);
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
  assert.match(context.data.candidateSummaryText, /1 小节有变化/);
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

test("chapter workspace offers the migrated chapter actions and preserves narration settings", async () => {
  installWx();
  const sheets = [];
  const calls = [];
  global.wx.showActionSheet = (options) => sheets.push(options);
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      prepareMemoirAudio: async (projectId, source, payload) => {
        calls.push({ projectId, source, payload });
        return sampleAudioPlan([]);
      },
    },
  });
  const context = pageContext(page, { projectId: "project-1", chapterId: "chapter-1", workspace: workspace(), chapterHasReadableDraft: true });
  context.pageVisible = true;
  await page.openChapterMoreActions.call(context);
  assert.deepEqual(sheets[0].itemList, ["修改章名", "AI 重新起名", "朗读整章", "分享设置", "不收入本书"]);
  assert.equal(context.data.operationKind, "");
  await page.playChapterAudio.call(context);
  assert.deepEqual(calls, [{ projectId: "project-1", source: { kind: "chapter", chapterId: "chapter-1", versionId: "version-1" }, payload: { voice: "linjiajiejie", speed_ratio: 1.5, start_index: 0, retry_failed: true } }]);
  assert.equal(context.data.audioPhase, "idle");
  context.setData({ chapterHasReadableDraft: false });
  context.pageVisible = true;
  await page.openChapterMoreActions.call(context);
  assert.deepEqual(sheets[1].itemList, ["修改章名", "AI 重新起名", "不收入本书"]);
});

test("chapter workspace renames manually or with AI through the existing APIs", async () => {
  installWx({ editableContent: "  院子里的童年  " });
  const calls = [];
  let currentTitle = "第一次离家";
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      updateMemoirBookChapterTitle: async (projectId, chapterId, data) => { calls.push({ projectId, chapterId, data }); currentTitle = data.title; },
      generateMemoirBookChapterTitle: async (projectId, chapterId, data) => { calls.push({ projectId, chapterId, data }); currentTitle = "外婆院中的日子"; },
      getMemoirChapterWorkspace: async () => workspace({ chapter_title: currentTitle }),
    },
  });
  const context = pageContext(page, { projectId: "project-1", chapterId: "chapter-1", workspace: workspace() });
  await page.editChapterTitle.call(context, false);
  assert.equal(context.data.workspace.chapter_title, "院子里的童年");
  await page.editChapterTitle.call(context, true);
  assert.equal(context.data.workspace.chapter_title, "外婆院中的日子");
  assert.deepEqual(calls, [
    { projectId: "project-1", chapterId: "chapter-1", data: { title: "院子里的童年" } },
    { projectId: "project-1", chapterId: "chapter-1", data: {} },
  ]);
  assert.equal(context.data.operationKind, "");
});

test("chapter workspace toggles book inclusion without deleting the chapter's draft", async () => {
  installWx();
  let includeInBook = true;
  const commands = [];
  const sheets = [];
  global.wx.showActionSheet = (options) => sheets.push(options);
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      getMemoirBookPlan: async () => ({ chapters: [{ id: "chapter-1", include_in_book: includeInBook }] }),
      applyMemoirBookPlanStructureCommand: async (_projectId, command) => { commands.push(command); includeInBook = command.include_in_book; },
    },
  });
  const context = pageContext(page, { projectId: "project-1", chapterId: "chapter-1", workspace: workspace() });
  await page.setChapterBookInclusion.call(context, false);
  assert.equal(context.data.workspace.draft_content, workspace().draft_content);
  context.pageVisible = true;
  await page.openChapterMoreActions.call(context);
  assert.ok(sheets[0].itemList.includes("恢复收入本书"));
  await page.setChapterBookInclusion.call(context, true);
  assert.equal(context.data.workspace.draft_content, workspace().draft_content);
  assert.deepEqual(commands, [
    { type: "set_chapter_book_inclusion", chapter_id: "chapter-1", include_in_book: false },
    { type: "set_chapter_book_inclusion", chapter_id: "chapter-1", include_in_book: true },
  ]);
  context.setData({ editingSectionId: "section-1" });
  await page.setChapterBookInclusion.call(context, false);
  await page.editChapterTitle.call(context, true);
  assert.equal(commands.length, 2);
});

test("chapter action sheet stays closed when its plan arrives after leaving the page", async () => {
  installWx();
  const sheets = [];
  let resolvePlan;
  global.wx.showActionSheet = (options) => sheets.push(options);
  const { page } = loadWorkspacePage({ voiceApiExports: { getMemoirBookPlan: () => new Promise((resolve) => { resolvePlan = resolve; }) } });
  const context = pageContext(page, { projectId: "project-1", chapterId: "chapter-1", workspace: workspace() });
  context.pageVisible = true;
  const opening = page.openChapterMoreActions.call(context);
  context.pageVisible = false;
  resolvePlan({ chapters: [{ id: "chapter-1", include_in_book: true }] });
  await opening;
  assert.equal(sheets.length, 0);
  assert.equal(context.data.operationKind, "");
});

test("leaving a workspace during audio preparation cancels polling and playback", async () => {
  const { flushAsyncWork } = require("./page-harness");
  installWx();
  let requests = 0;
  const { page } = loadWorkspacePage({ voiceApiExports: {
    prepareMemoirAudio: async () => {
      requests += 1;
      const plan = sampleAudioPlan();
      plan.poll_after_ms = 60000;
      plan.segments[0] = { ...plan.segments[0], status: "pending", audio_file_id: null };
      return plan;
    },
  } });
  const context = pageContext(page, { projectId: "project-1", chapterId: "chapter-1", workspace: workspace() });
  const playback = page.playDraftSection.call(context, { currentTarget: { dataset: { id: "section-1" } } });
  await flushAsyncWork();
  assert.equal(context.data.audioPhase, "preparing");
  assert.equal(context.bookTTSPollTimer.pending, true);
  page.onHide.call(context);
  await playback;
  assert.equal(context.bookTTSPollTimer.pending, false);
  assert.equal(context.data.audioPhase, "idle");
  assert.equal(requests, 1);
});


test("chapter sharing creates a chapter-only reader link and revokes the same chapter", async () => {
  installWx();
  const calls = [];
  const { page } = loadWorkspacePage({ voiceApiExports: {
    getMemoirBookShareStatus: async (...args) => { calls.push(["status", ...args]); return { active: false, current: false, stale: false }; },
    updateMemoirBookShare: async (...args) => { calls.push(["ensure", ...args]); return { token: "c1.chapter-token" }; },
    readMemoirBookShare: async () => ({ latest_version: null }),
    revokeMemoirBookShare: async (...args) => { calls.push(["revoke", ...args]); },
  } });
  const context = pageContext(page, { projectId: "project-1", chapterId: "chapter-1", workspace: workspace(), chapterHasReadableDraft: true });
  await context.refreshBookShareStatus();
  assert.equal(context.data.bookShareFeedbackVisible, false);
  assert.equal(context.onShareAppMessage().path, "/pages/home/index?from=share_home");
  await context.prepareReadingShare();
  assert.equal(context.data.bookShareFeedbackVisible, true);
  assert.deepEqual(context.onShareAppMessage(), { title: "第一次离家", path: "/pages/book-reader/index?shareToken=c1.chapter-token", imageUrl: "https://pawtale-api.spaceflows.cn/api/mobile/memoir/book-shares/c1.chapter-token/card.jpg?design=5" });
  await context.revokeBookShare();
  assert.equal(context.onShareAppMessage().path, "/pages/home/index?from=share_home");
  assert.deepEqual(calls, [["status", "project-1", "chapter-1", undefined], ["ensure", "project-1", "chapter-1", undefined], ["revoke", "project-1", "chapter-1", undefined]]);
});

test("a current chapter link reveals sharing information only after the share action", async () => {
  installWx();
  const { page } = loadWorkspacePage({ voiceApiExports: {
    getMemoirBookShareStatus: async () => ({ active: true, current: true, stale: false, token: "existing-chapter" }),
    readMemoirBookShare: async () => ({ latest_version: null }),
  } });
  const context = pageContext(page, { projectId: "p", chapterId: "c", workspace: workspace(), chapterHasReadableDraft: true });
  await context.refreshBookShareStatus();
  assert.equal(context.data.bookShareReady, true);
  assert.equal(context.data.bookShareFeedbackVisible, false);
  await context.prepareReadingShare();
  assert.equal(context.data.bookShareFeedbackVisible, true);
  assert.equal(context.onShareAppMessage().path, "/pages/book-reader/index?shareToken=existing-chapter");
});

test("chapter share ignores a status response from a different chapter in the same project", async () => {
  installWx();
  let resolveStatus;
  const { page } = loadWorkspacePage({ voiceApiExports: {
    getMemoirBookShareStatus: () => new Promise((resolve) => { resolveStatus = resolve; }),
  } });
  const context = pageContext(page, { projectId: "project-1", chapterId: "chapter-1", workspace: workspace(), chapterHasReadableDraft: true });
  const pending = context.refreshBookShareStatus();
  context.setData({ chapterId: "chapter-2" });
  resolveStatus({ active: true, current: true, stale: false, token: "old-chapter-token" });
  await pending;
  assert.equal(context.onShareAppMessage().path, "/pages/home/index?from=share_home");
  assert.equal(context.bookShareToken, "");
});


test("focused story shares and previews only its stable section", async () => {
  installWx();
  const calls = [];
  const urls = [];
  global.wx.navigateTo = ({ url }) => urls.push(url);
  const { page } = loadWorkspacePage({ voiceApiExports: {
    getMemoirBookShareStatus: async (...args) => { calls.push(["status", ...args]); return { active: false, current: false, stale: false }; },
    updateMemoirBookShare: async (...args) => { calls.push(["ensure", ...args]); return { token: "s1.story-token" }; },
    readMemoirBookShare: async () => ({ latest_version: null }),
    revokeMemoirBookShare: async (...args) => { calls.push(["stop", ...args]); },
  } });
  const context = pageContext(page, { projectId: "p", chapterId: "c", focusedSectionId: "s", focusedSectionTitle: "院子里的故事", focusedSection: { hasDraft: true } });
  await context.refreshBookShareStatus();
  context.openReadingSharePreview();
  assert.deepEqual(urls, ["/pages/book-reader/index?projectId=p&chapterId=c&sectionId=s&sharePreview=1"]);
  await context.prepareReadingShare();
  assert.equal(context.onShareAppMessage().path, "/pages/book-reader/index?shareToken=s1.story-token");
  assert.equal(context.onShareAppMessage().title, "院子里的故事");
  await context.revokeBookShare();
  assert.deepEqual(calls, [["status", "p", "c", "s"], ["ensure", "p", "c", "s"], ["stop", "p", "c", "s"]]);
});

test("a response for another story in the same chapter never readies the current share", async () => {
  installWx();
  let finish;
  const { page } = loadWorkspacePage({ voiceApiExports: {
    getMemoirBookShareStatus: () => new Promise(resolve => { finish = resolve; }),
  } });
  const context = pageContext(page, { projectId: "p", chapterId: "c", focusedSectionId: "first", focusedSection: { hasDraft: true } });
  const pending = context.refreshBookShareStatus();
  context.setData({ focusedSectionId: "second" });
  finish({ active: true, current: true, stale: false, token: "first-token" });
  await pending;
  assert.equal(context.data.bookShareReady, false);
  assert.equal(context.bookShareToken, "");
});


test("removing the focused story clears its in-flight sharing state before returning to the chapter", async () => {
  installWx();
  const { page } = loadWorkspacePage({ voiceApiExports: {
    getMemoirBookShareStatus: async () => ({ active: false, current: false, stale: false }),
  } });
  const context = pageContext(page, { projectId: "p", chapterId: "c", focusedSectionId: "removed", bookShareBusy: true, bookShareReady: true, bookShareActive: true });
  context.sectionFocusId = "removed";
  context.bookShareToken = "old-story";
  context.applyWorkspace(workspace());
  await flushAsyncWork();
  assert.equal(context.data.focusedSectionId, "");
  assert.equal(context.data.bookShareBusy, false);
  assert.equal(context.data.bookShareReady, false);
  assert.equal(context.bookShareToken, "");
});

for (const sectionId of [undefined, "s"]) {
  test(`legacy ${sectionId ? "story" : "chapter"} share confirms an unset excerpt choice before sharing`, async () => {
    installWx();
    let selection = sectionId ? undefined : null;
    let prompts = 0;
    let saves = 0;
    global.wx.showModal = ({ success }) => { prompts++; success({ confirm: true }); };
    const token = sectionId ? "s1.token" : "c1.token";
    const { page } = loadWorkspacePage({ voiceApiExports: {
      getMemoirBookShareStatus: async () => ({ active: true, current: true, stale: false, token, card_selection: selection }),
      updateMemoirBookShare: async () => ({ token }),
      readMemoirBookShare: async () => ({ latest_version: { id: "v1", content: "奶奶坐在门口。" } }),
      saveMemoirShareCardSelection: async (_projectId, { token: _, ...choice }, chapterId, storyId) => {
        assert.equal(chapterId, "c");
        assert.equal(storyId, sectionId);
        saves++;
        selection = choice;
        return choice;
      },
    } });
    const data = { projectId: "p", chapterId: "c", workspace: workspace(), chapterHasReadableDraft: true,
      ...(sectionId ? { focusedSectionId: sectionId, focusedSection: { hasDraft: true } } : {}) };
    const context = pageContext(page, data);
    await context.refreshBookShareStatus();
    assert.equal(prompts, 0);
    assert.equal(context.data.bookShareReady, false);
    assert.equal(context.onShareAppMessage().path, "/pages/home/index?from=share_home");
    await context.prepareReadingShare();
    assert.equal(prompts, 1);
    assert.equal(saves, 1);
    assert.equal(context.data.bookShareReady, true);
    assert.match(context.onShareAppMessage().imageUrl, /version_id=v1&excerpt_start=0&excerpt_length=7$/);
    const reopened = pageContext(page, data);
    await reopened.refreshBookShareStatus();
    assert.equal(reopened.data.bookShareReady, true);
    assert.equal(reopened.onShareAppMessage().imageUrl, context.onShareAppMessage().imageUrl);
    assert.equal(prompts, 1);
  });
}

for (const existingShare of [false, true]) {
  test(`chapter more menu can add and remove a confirmed excerpt for ${existingShare ? "existing" : "new"} shares`, async () => {
    installWx();
    let confirm = false;
    let preparations = 0;
    let savedSelection = existingShare ? { version_id: "v1", excerpt_start: 0, excerpt_length: 0 } : null;
    const prompts = [];
    const sheets = [];
    global.wx.showActionSheet = (options) => sheets.push(options);
    global.wx.showModal = ({ content, success }) => { prompts.push(content); success({ confirm }); };
    const { page } = loadWorkspacePage({ voiceApiExports: {
      getMemoirBookShareStatus: async () => ({ active: true, current: true, stale: false, token: "c1.chapter-token", card_selection: savedSelection }),
      saveMemoirShareCardSelection: async (_projectId, { token, ...selection }) => { savedSelection = selection; return selection; },
      updateMemoirBookShare: async () => { preparations += 1; return { token: "c1.chapter-token" }; },
      readMemoirBookShare: async () => ({ latest_version: { id: "v1", content: "# 标题\n\n奶奶坐在门口。另一句话。" } }),
    } });
    const context = pageContext(page, { projectId: "project-1", chapterId: "chapter-1", workspace: workspace(), chapterHasReadableDraft: true });
    context.pageVisible = true;
    if (existingShare) await context.refreshBookShareStatus();
    await context.prepareReadingShare();
    assert.equal(preparations, existingShare ? 0 : 1);
    assert.equal(prompts.length, existingShare ? 0 : 1);
    assert.equal(context.data.bookShareReady, true);
    assert.ok(!context.onShareAppMessage().imageUrl.includes("excerpt_length"));

    async function chooseExcerptSettings() {
      await context.openChapterMoreActions();
      const chapterMenu = sheets.pop();
      const shareIndex = chapterMenu.itemList.indexOf("分享设置");
      assert.ok(shareIndex >= 0, "visible chapter menu must expose share settings");
      chapterMenu.success({ tapIndex: shareIndex });
      const shareMenu = sheets.pop();
      assert.deepEqual(shareMenu.itemList, ["预览分享内容", "设置封面摘录", "停止分享"]);
      shareMenu.success({ tapIndex: shareMenu.itemList.indexOf("设置封面摘录") });
      await flushAsyncWork();
      assert.equal(context.data.bookShareReady, true);
      assert.equal(context.data.bookShareBusy, false);
    }

    confirm = true;
    await chooseExcerptSettings();
    assert.ok(prompts.at(-1).includes("奶奶坐在门口。"));
    assert.match(context.onShareAppMessage().imageUrl, /version_id=v1&excerpt_start=6&excerpt_length=7$/);
    const confirmedImage = context.onShareAppMessage().imageUrl;
    await context.refreshBookShareStatus();
    assert.equal(context.onShareAppMessage().imageUrl, confirmedImage);
    const reopened = pageContext(page, { projectId: "project-1", chapterId: "chapter-1", workspace: workspace(), chapterHasReadableDraft: true });
    await reopened.refreshBookShareStatus();
    assert.equal(reopened.onShareAppMessage().imageUrl, confirmedImage);
    confirm = false;
    await chooseExcerptSettings();
    await context.refreshBookShareStatus();
    await reopened.refreshBookShareStatus();
    assert.ok(!context.onShareAppMessage().imageUrl.includes("excerpt_length"));
    assert.ok(!reopened.onShareAppMessage().imageUrl.includes("excerpt_length"));
    assert.equal(savedSelection.excerpt_length, 0);
  });
}

test("a new reading version requires confirmation without silently applying the old excerpt", async () => {
  installWx();
  let prompts = 0;
  let saved = { version_id: "v1", excerpt_start: 0, excerpt_length: 7 };
  global.wx.showModal = ({ success }) => { prompts++; success({ confirm: true }); };
  const { page } = loadWorkspacePage({ voiceApiExports: {
    getMemoirBookShareStatus: async () => ({ active: true, current: true, stale: false, token: "c1.token", card_selection: saved }),
    updateMemoirBookShare: async () => ({ token: "c1.token" }),
    readMemoirBookShare: async () => ({ latest_version: { id: "v2", content: "这是新版正文。" } }),
    saveMemoirShareCardSelection: async (_projectId, { token, ...selection }) => { saved = selection; return selection; },
  } });
  const context = pageContext(page, { projectId: "p", chapterId: "c", workspace: workspace(), chapterHasReadableDraft: true });
  await context.refreshBookShareStatus();
  assert.equal(prompts, 0);
  assert.equal(context.data.bookShareReady, false);
  assert.ok(!context.data.bookShareImageUrl.includes("excerpt_length"));
  await context.prepareReadingShare();
  assert.equal(prompts, 1);
  assert.equal(saved.version_id, "v2");
  await context.refreshBookShareStatus();
  assert.equal(context.data.bookShareReady, true);
  assert.match(context.onShareAppMessage().imageUrl, /version_id=v2&excerpt_start=0&excerpt_length=7$/);
  assert.equal(prompts, 1);
});

test("failed excerpt persistence never reports a prepared share and can reload the server choice", async () => {
  installWx();
  const { page } = loadWorkspacePage({ voiceApiExports: {
    getMemoirBookShareStatus: async () => ({ active: true, current: true, stale: false, token: "c1.token", card_selection: { version_id: "v1", excerpt_start: 0, excerpt_length: 0 } }),
    readMemoirBookShare: async () => ({ latest_version: { id: "v1", content: "奶奶坐在门口。" } }),
    saveMemoirShareCardSelection: async () => { throw Error("保存失败"); },
  } });
  const context = pageContext(page, { projectId: "p", chapterId: "c", workspace: workspace(), chapterHasReadableDraft: true });
  await context.refreshBookShareStatus();
  await context.configureReadingShareCard();
  assert.equal(context.data.bookShareReady, false);
  assert.equal(context.data.bookShareBusy, false);
  assert.equal(context.data.error, "保存失败");
  await context.refreshBookShareStatus();
  assert.equal(context.data.bookShareReady, true);
  assert.ok(!context.onShareAppMessage().imageUrl.includes("excerpt_length"));
});

test("an excerpt confirmed after switching chapters cannot affect the new share", async () => {
  installWx();
  let finish;
  global.wx.showModal = ({ success }) => { finish = success; };
  const { page } = loadWorkspacePage({ voiceApiExports: {
    saveMemoirShareCardSelection: async () => { assert.fail("stale confirmation must not be persisted"); },
    updateMemoirBookShare: async () => ({ token: "c1.old" }),
    readMemoirBookShare: async () => ({ latest_version: { id: "v1", content: "奶奶坐在门口。" } }),
  } });
  const context = pageContext(page, { projectId: "p", chapterId: "c", workspace: workspace(), chapterHasReadableDraft: true });
  const pending = context.prepareReadingShare();
  await flushAsyncWork();
  context.setData({ chapterId: "new" });
  finish({ confirm: true });
  await pending;
  assert.equal(context.data.bookShareReady, false);
  assert.equal(context.data.bookShareImageUrl, "");
});

test("hiding a prepared share preserves its payload and ignores late workspace sharing refresh", async () => {
  installWx();
  let statusReads = 0;
  const { page } = loadWorkspacePage({ voiceApiExports: {
    getMemoirBookShareStatus: async () => { statusReads++; return { active: false, current: false, stale: false }; },
  } });
  const context = pageContext(page, { projectId: "p", chapterId: "c", workspace: workspace(),
    chapterHasReadableDraft: true, bookShareReady: true, bookShareImageUrl: "https://example.test/card.jpg" });
  context.bookShareToken = "c1.token";
  context.pageVisible = true;
  const payload = context.onShareAppMessage();
  context.onHide();
  context.applyWorkspace(workspace());
  await flushAsyncWork();
  assert.equal(statusReads, 0);
  assert.deepEqual(context.onShareAppMessage(), payload);
});

for (const exitPage of ["onHide", "onUnload"]) {
  for (const pendingStage of ["reading", "confirmation"]) {
    test(`${exitPage} invalidates pending excerpt ${pendingStage} even after returning to the same chapter`, async () => {
      installWx();
      let finishRead;
      let finishConfirmation;
      let prompts = 0;
      let saves = 0;
      let reads = 0;
      let resumed = false;
      const output = { latest_version: { id: "v1", content: "奶奶坐在门口。" } };
      global.wx.showModal = ({ success }) => {
        prompts++;
        if (pendingStage === "confirmation" && !resumed) finishConfirmation = success;
        else success({ confirm: true });
      };
      const { page } = loadWorkspacePage({ voiceApiExports: {
        updateMemoirBookShare: async () => ({ token: "c1.token" }),
        getMemoirBookShareStatus: async () => ({ active: true, current: true, stale: false, token: "c1.token" }),
        readMemoirBookShare: async () => ++reads === 1 && pendingStage === "reading"
          ? new Promise(resolve => { finishRead = resolve; }) : output,
        saveMemoirShareCardSelection: async (_projectId, selection) => { saves++; return selection; },
      } });
      const context = pageContext(page, { projectId: "p", chapterId: "c", workspace: workspace(), chapterHasReadableDraft: true });
      context.pageVisible = true;
      const pending = context.prepareReadingShare();
      await flushAsyncWork();
      context[exitPage]();
      // A quick return must not revive callbacks from before leaving.
      context.pageVisible = true;
      if (pendingStage === "reading") finishRead(output);
      else finishConfirmation({ confirm: true });
      await pending;
      assert.equal(prompts, pendingStage === "reading" ? 0 : 1);
      assert.equal(saves, 0);
      assert.equal(context.data.bookShareBusy, false);
      assert.equal(context.data.bookShareReady, false);
      resumed = true;
      await context.refreshBookShareStatus();
      await context.prepareReadingShare();
      assert.equal(saves, 1);
      assert.equal(context.data.bookShareReady, true);
    });
  }
}
