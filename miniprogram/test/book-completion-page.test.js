require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

function loadBookCompletionPage({ voiceApiExports = {}, consent = true } = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirBookCompletionOverview: async () => {
      throw new Error("getMemoirBookCompletionOverview was not mocked");
    },
    prepareMemoirBookChapter: async () => {
      throw new Error("prepareMemoirBookChapter was not mocked");
    },
    createMemoirVoiceSession: async () => {
      throw new Error("createMemoirVoiceSession was not mocked");
    },
    closeMemoirCompletionIssue: async () => {
      throw new Error("closeMemoirCompletionIssue was not mocked");
    },
    ...voiceApiExports,
  });
  mockModule("../services/navigation.ts", { routeAuthError: () => false });
  mockModule("../services/audit-tracker.ts", { trackAuditAction() {} });
  mockModule("../services/voice-authorization.ts", {
    readVoiceAuthorizationConsent: () => consent,
    syncVoiceAuthorizationConsent: async () => {},
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
  });
  return loadPageModule("../pages/book-completion/index.ts");
}

function completionChapter(overrides = {}) {
  return {
    chapter_id: "chapter-1",
    title: "学校里的比赛",
    sort_order: 0,
    state: "needs_input",
    next_action: {
      kind: "start_interview",
      title: "这段回忆为什么重要？",
      detail: "讲完后会自动继续整理。",
      action_text: "开始讲",
      disabled: false,
      issue_id: "issue-1",
    },
    active_issue: {
      id: "issue-1",
      question: "这段回忆为什么重要？",
      status: "pending",
      active_session_id: null,
    },
    headline: "还需要讲一件事",
    detail: "讲完后会自动继续整理。",
    ...overrides,
  };
}

function readyOverview() {
  return {
    project_id: "project-1",
    headline: "逐章整理这本书",
    detail: "每章只显示一个下一步。",
    total_chapters: 2,
    completed_chapters: 1,
    chapters: [
      completionChapter(),
      completionChapter({
        chapter_id: "chapter-2",
        title: "参加工作",
        sort_order: 1,
        state: "confirmed",
        next_action: {
          kind: "complete",
          title: "本章已确认",
          detail: "正文已经收入书稿。",
          action_text: "",
          disabled: true,
          issue_id: null,
        },
        active_issue: null,
        headline: "本章已确认",
        detail: "正文已经收入书稿。",
      }),
    ],
  };
}

test("book completion overview page is registered", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  assert.ok(appJson.pages.includes("pages/book-completion/index"));
  const pageJson = JSON.parse(
    fs.readFileSync(
      path.join(__dirname, "../pages/book-completion/index.json"),
      "utf8",
    ),
  );
  assert.equal(pageJson.navigationBarTitleText, "整理这本书");
});

test("book completion overview exposes confirmed chapter progress and one action per chapter", () => {
  const { bookCompletionViewPatch } = loadBookCompletionPage().mod;
  const patch = bookCompletionViewPatch(readyOverview());

  assert.equal(patch.progressText, "1 / 2 章正文已确认");
  assert.equal(patch.chapters[0].actionText, "开始讲");
  assert.equal(patch.chapters[1].actionText, "查看正文");
  assert.equal(patch.allChaptersConfirmed, false);
  assert.doesNotMatch(JSON.stringify(patch), /Story|Claim|slot|Contract|visible_tasks/);
});

test("book completion offers one whole-book recheck after all chapters are confirmed", async () => {
  const prepared = [];
  const toasts = [];
  const overview = readyOverview();
  overview.completed_chapters = 2;
  overview.chapters[0] = completionChapter({
    state: "confirmed",
    next_action: {
      kind: "complete",
      title: "本章已确认",
      detail: "正文已经收入书稿。",
      action_text: "",
      disabled: true,
    },
    active_issue: null,
    headline: "本章已确认",
    detail: "正文已经收入书稿。",
  });
  global.wx = {
    navigateTo() {},
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const { page, mod } = loadBookCompletionPage({
    voiceApiExports: {
      prepareMemoirBookChapter: async (projectId, chapterId) => {
        prepared.push([projectId, chapterId]);
      },
      getMemoirBookCompletionOverview: async () => overview,
    },
  });
  const patch = mod.bookCompletionViewPatch(overview);
  assert.equal(patch.allChaptersConfirmed, true);
  assert.equal(
    patch.chapters.every((chapter) => !Object.hasOwn(chapter, "canRecheck")),
    true,
  );

  const context = pageContext(page, {
    projectId: "project-1",
    chapters: patch.chapters,
    allChaptersConfirmed: true,
    bulkCheckVisible: true,
  });
  await page.recheckAllChapters.call(context);

  assert.deepEqual(prepared, [
    ["project-1", "chapter-1"],
    ["project-1", "chapter-2"],
  ]);
  assert.ok(toasts.every((title) => title === "暂未发现待补内容"));
  delete global.wx;
});

test("book completion can check only chapters that have not been checked", async () => {
  const prepared = [];
  const overview = readyOverview();
  overview.completed_chapters = 1;
  overview.chapters[0] = completionChapter({
    state: "not_started",
    next_action: {
      kind: "prepare",
      title: "检查本章是否需要补充",
      detail: "系统会根据当前素材判断下一步。",
      action_text: "检查本章",
      disabled: false,
    },
    active_issue: null,
    headline: "尚未检查是否需要补充",
    detail: "检查后只会给出一个明确的下一步。",
  });
  global.wx = { showToast() {} };
  const { page, mod } = loadBookCompletionPage({
    voiceApiExports: {
      prepareMemoirBookChapter: async (projectId, chapterId) => {
        prepared.push([projectId, chapterId]);
      },
      getMemoirBookCompletionOverview: async () => overview,
    },
  });
  const patch = mod.bookCompletionViewPatch(overview);
  assert.equal(patch.chapters[0].stateText, "待检查");
  assert.equal(patch.bulkCheckVisible, true);
  assert.equal(patch.bulkCheckActionText, "检查待检查章节");

  const context = pageContext(page, {
    projectId: "project-1",
    chapters: patch.chapters,
    allChaptersConfirmed: false,
    bulkCheckVisible: true,
  });
  await page.recheckAllChapters.call(context);

  assert.deepEqual(prepared, [["project-1", "chapter-1"]]);
  delete global.wx;
});

test("book completion resumes polling after a transient recheck refresh failure", async () => {
  const scheduled = [];
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  global.setTimeout = (callback, delay) => {
    scheduled.push([callback, delay]);
    return 1;
  };
  global.clearTimeout = () => {};
  global.wx = {};
  try {
    const { page } = loadBookCompletionPage({
      voiceApiExports: {
        getMemoirBookCompletionOverview: async () => {
          throw new Error("network unavailable");
        },
      },
    });
    const context = pageContext(page, {
      projectId: "project-1",
      recheckingAll: true,
    });
    context.pageVisible = true;

    await page.loadOverview.call(context, true);

    assert.equal(context.data.error, "network unavailable");
    assert.equal(scheduled.length, 1);
    assert.equal(scheduled[0][1], 1500);
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
    delete global.wx;
  }
});

test("book completion starts the selected completion directly", async () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const overview = readyOverview();
  const { page, mod } = loadBookCompletionPage({
    voiceApiExports: {
      createMemoirVoiceSession: async () => ({ session: { id: "session-1" } }),
    },
  });
  const context = pageContext(page, {
    projectId: "project-1",
    projectTitle: "爷爷的故事",
    chapters: mod.bookCompletionViewPatch(overview).chapters,
  });

  await page.tapChapter.call(context, {
    currentTarget: { dataset: { chapterId: "chapter-1" } },
  });

  assert.match(navigations[0], /pages\/voice-chat\/index/);
  assert.match(navigations[0], /voiceSessionId=session-1/);
  delete global.wx;
});

test("book completion prepares an untouched chapter in place", async () => {
  const prepared = [];
  global.wx = { navigateTo() {} };
  const overview = readyOverview();
  overview.chapters[0] = completionChapter({
    state: "not_started",
    next_action: {
      kind: "prepare",
      title: "检查本章是否需要补充",
      detail: "系统会先检查现有讲述。",
      action_text: "检查本章",
      disabled: false,
    },
    active_issue: null,
  });
  const { page, mod } = loadBookCompletionPage({
    voiceApiExports: {
      prepareMemoirBookChapter: async (projectId, chapterId) => {
        prepared.push([projectId, chapterId]);
      },
      getMemoirBookCompletionOverview: async () => overview,
    },
  });
  const context = pageContext(page, {
    projectId: "project-1",
    chapters: mod.bookCompletionViewPatch(overview).chapters,
  });

  await page.tapChapter.call(context, {
    currentTarget: { dataset: { chapterId: "chapter-1" } },
  });

  assert.deepEqual(prepared, [["project-1", "chapter-1"]]);
  delete global.wx;
});
