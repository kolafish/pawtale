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
    pending_completion_source_count: 0,
    pending_new_source_count: 0,
    draft_id: "draft-1",
    draft_version_id: "version-1",
    draft_version: 1,
    draft_status: "draft",
    draft_content: "十八岁那年，我第一次离开家。",
    ...overrides,
  };
}

function loadWorkspacePage({
  currentWorkspace = workspace(),
  voiceApiExports = {},
  consent = true,
} = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirChapterWorkspace: async () => currentWorkspace,
    prepareMemoirBookChapter: async () => {},
    rewriteMemoirBookChapter: async () => {},
    closeMemoirCompletionIssue: async () => {},
    createMemoirVoiceSession: async () => ({ session: { id: "session-1" } }),
    reviseMemoirBookChapterDraft: async () => ({}),
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
    showModal(options) {
      options.success?.({ confirm: true, cancel: false });
    },
  };
  return { navigations, toasts };
}

test("chapter workspace loads one whole chapter draft", async () => {
  installWx();
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
  assert.equal(context.data.canConfirmChapter, true);
  assert.doesNotMatch(
    JSON.stringify(context.data.workspace),
    /contract|nodes|draft_blocks|answer_updates/,
  );
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

test("chapter workspace edits the whole draft with optimistic version control", async () => {
  const { toasts } = installWx();
  const calls = [];
  const { page } = loadWorkspacePage({
    voiceApiExports: {
      reviseMemoirBookChapterDraft: async (...args) => calls.push(args),
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1", chapterId: "chapter-1" });

  page.startDraftEdit.call(context);
  page.changeDraftContent.call(context, { detail: { value: "修改后的整章正文" } });
  await page.saveDraftEdit.call(context);

  assert.deepEqual(calls, [[
    "project-1",
    "chapter-1",
    {
      expected_version_id: "version-1",
      content: "修改后的整章正文",
    },
  ]]);
  assert.equal(toasts[0].title, "正文已保存");
  delete global.wx;
});

test("chapter workspace confirms the current whole draft without an assembly step", async () => {
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
  const { toasts } = installWx();
  const calls = [];
  const staleWorkspace = workspace({
    draft_needs_rewrite: true,
    can_rewrite_draft: true,
    pending_completion_source_count: 2,
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
  page.rewriteChapter.call(context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [["project-1", "chapter-1"]]);
  assert.equal(toasts.at(-1).title, "本章正文已更新");
  delete global.wx;
});
