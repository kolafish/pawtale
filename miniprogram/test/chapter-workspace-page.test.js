require("./register-ts");

const assert = require("node:assert/strict");
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
      item_id: null,
    },
    active_item: null,
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
    ...overrides,
  };
}

function draftBlock(overrides = {}) {
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
  currentWorkspace = workspace(),
  blocks = [draftBlock()],
  voiceApiExports = {},
  consent = true,
} = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirChapterWorkspace: async () => currentWorkspace,
    listMemoirDraftBlocks: async () => ({ chapter_id: currentWorkspace.chapter_id, blocks }),
    prepareMemoirBookChapter: async () => {},
    resolveMemoirCompletionItem: async () => {},
    createMemoirVoiceSession: async () => ({ session: { id: "session-1" } }),
    manuallyReviseMemoirDraftBlock: async () => draftBlock(),
    unlockMemoirDraftBlock: async () => draftBlock(),
    assembleMemoirChapterDraftBlocks: async () => ({ version: 1 }),
    confirmMemoirBookChapterDraft: async () => ({}),
    ...voiceApiExports,
  });
  mockModule("../services/navigation.ts", { routeAuthError: () => false });
  mockModule("../services/audit-tracker.ts", { trackAuditAction() {} });
  mockModule("../services/voice-authorization.ts", {
    readVoiceAuthorizationConsent: () => consent,
    syncVoiceAuthorizationConsent: async () => {},
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
  });
  return loadPageModule("../pages/chapter-workspace/index.ts");
}

function installWx() {
  const navigations = [];
  const toasts = [];
  global.wx = {
    navigateTo(options) { navigations.push(options.url); },
    navigateBack() {},
    pageScrollTo() {},
    stopPullDownRefresh() {},
    showToast(options) { toasts.push(options); },
  };
  return { navigations, toasts };
}

test("chapter workspace exposes one status and only the fields needed by the page", async () => {
  installWx();
  const { page } = loadWorkspacePage();
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  assert.equal(context.data.readinessTitle, "正文已经整理好");
  assert.equal(context.data.nextAction.kind, "review_draft");
  assert.equal(context.data.draftBlocks.length, 1);
  assert.equal(context.data.canConfirmChapter, true);
  assert.doesNotMatch(JSON.stringify(context.data.workspace), /contract|stories|gaps|answer_updates/);
  delete global.wx;
});

test("chapter workspace confirms the current item and resumes automatic preparation", async () => {
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
      item_id: "item-1",
    },
    active_item: {
      id: "item-1",
      question: "离家年龄有两个说法",
      status: "pending",
    },
    headline: "还需要确认一件事",
  });
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({ completion_summary: confirmationSummary }),
    blocks: [],
    voiceApiExports: {
      resolveMemoirCompletionItem: async (...args) => calls.push(["confirm", ...args]),
      prepareMemoirBookChapter: async (...args) => calls.push(["prepare", ...args]),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });
  await page.confirmCurrentItem.call(context);

  assert.deepEqual(calls, [
    [
      "confirm",
      "project-1",
      "item-1",
      { note: "用户已确认按当前建议处理" },
    ],
    ["prepare", "project-1", "chapter-1"],
  ]);
  delete global.wx;
});

test("chapter workspace starts its single completion through the existing voice chat", async () => {
  const { navigations } = installWx();
  const interviewSummary = summary({
    state: "needs_input",
    next_action: {
      kind: "start_interview",
      title: "离家后的第一晚是什么感受？",
      detail: "讲完后会自动继续整理。",
      action_text: "开始讲",
      disabled: false,
      item_id: "item-1",
    },
    active_item: {
      id: "item-1",
      question: "离家后的第一晚是什么感受？",
      status: "pending",
      active_session_id: null,
    },
  });
  const { page } = loadWorkspacePage({
    currentWorkspace: workspace({ completion_summary: interviewSummary }),
    blocks: [],
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  await page.runNextAction.call(context);

  assert.match(navigations[0], /pages\/voice-chat\/index/);
  assert.match(navigations[0], /voiceSessionId=session-1/);
  delete global.wx;
});

test("chapter workspace allows direct paragraph edits and legacy unlock", async () => {
  installWx();
  const calls = [];
  const lockedBlock = draftBlock({ locked: true });
  const { page } = loadWorkspacePage({
    blocks: [lockedBlock],
    voiceApiExports: {
      unlockMemoirDraftBlock: async (...args) => {
        calls.push(["unlock", ...args]);
        return draftBlock();
      },
      manuallyReviseMemoirDraftBlock: async (...args) => {
        calls.push(["edit", ...args]);
        return draftBlock();
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  await page.unlockLegacyBlock.call(context, {
    currentTarget: { dataset: { blockId: "block-1" } },
  });
  context.data.draftBlocks = [draftBlock()];
  page.startDraftBlockEdit.call(context, {
    currentTarget: { dataset: { blockId: "block-1" } },
  });
  page.changeDraftBlockContent.call(context, { detail: { value: "修改后的正文" } });
  await page.saveDraftBlockEdit.call(context, {
    currentTarget: { dataset: { blockId: "block-1" } },
  });

  assert.equal(calls[0][0], "unlock");
  assert.equal(calls[1][0], "edit");
  assert.equal(calls[1][3].content, "修改后的正文");
  delete global.wx;
});

test("chapter workspace confirms the whole current chapter without per-paragraph decisions", async () => {
  const { toasts } = installWx();
  const calls = [];
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      assembleMemoirChapterDraftBlocks: async (...args) => calls.push(["assemble", ...args]),
      confirmMemoirBookChapterDraft: async (...args) => calls.push(["confirm", ...args]),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  await page.confirmChapter.call(context);

  assert.deepEqual(calls, [
    ["assemble", "project-1", "chapter-1"],
    ["confirm", "project-1", "chapter-1"],
  ]);
  assert.equal(toasts[0].title, "本章正文已确认");
  delete global.wx;
});

test("stale paragraphs keep chapter confirmation disabled", async () => {
  installWx();
  const { page } = loadWorkspacePage({
    blocks: [draftBlock({ freshness_status: "stale" })],
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  assert.equal(context.data.canConfirmChapter, false);
  delete global.wx;
});

test("open completion work keeps an older current draft from being confirmed", async () => {
  installWx();
  const interviewSummary = summary({
    state: "needs_input",
    next_action: {
      kind: "start_interview",
      title: "离家后的第一晚是什么感受？",
      detail: "讲完后会自动继续整理。",
      action_text: "开始讲",
      disabled: false,
      item_id: "item-1",
    },
    active_item: {
      id: "item-1",
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

  assert.equal(context.data.draftBlocks[0].freshness_status, "current");
  assert.equal(context.data.canConfirmChapter, false);
  delete global.wx;
});
