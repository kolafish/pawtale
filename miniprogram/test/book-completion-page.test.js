require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  loadPageModule,
  mockModule,
  pageContext,
  unmockedAsync,
} = require("./page-harness");

function loadBookCompletionPage({ voiceApiExports = {} } = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirBookCompletionJob: async () => bulkJob(),
    getMemoirBookCompletionOverview: unmockedAsync("getMemoirBookCompletionOverview"),
    enqueueMemoirBookCompletionJob: unmockedAsync("enqueueMemoirBookCompletionJob"),
    cancelMemoirBookCompletionJob: unmockedAsync("cancelMemoirBookCompletionJob"),
    generateMemoirBookReview: unmockedAsync("generateMemoirBookReview"),
    getMemoirBookReview: async () => null,
    getMemoirBookOutput: async () => null,
    listMemoirCompletionIssues: async () => ({
      project_id: "project-1",
      current_issue: null,
      issues: [],
    }),
    enqueueMemoirChapterPreparationJob: unmockedAsync("enqueueMemoirChapterPreparationJob"),
    ...voiceApiExports,
  });
  mockModule("../services/navigation.ts", { routeAuthError: () => false });
  return loadPageModule("../pages/book-completion/index.ts");
}

function completionChapter(overrides = {}) {
  return {
    chapter_id: "chapter-1",
    title: "学校里的比赛",
    sort_order: 0,
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

function bulkJob(overrides = {}) {
  return {
    job_id: null,
    project_id: "project-1",
    scope: "pending",
    status: "idle",
    total_chapter_count: 0,
    processed_chapter_count: 0,
    succeeded_chapter_count: 0,
    failed_chapter_count: 0,
    cancelled_chapter_count: 0,
    current_chapter_id: null,
    current_chapter_title: "",
    requested_ai_model: null,
    chapters: [],
    can_cancel: false,
    can_retry: false,
    error_message: "",
    ...overrides,
  };
}

function bookReview(overrides = {}) {
  return {
    id: "review-1",
    project_id: "project-1",
    input_signature: "book-signature-1",
    status: "ready",
    review: {
      summary: "全书围绕家庭与成长展开。",
      strengths: ["人物主线清楚"],
      structure_guidance: ["合并两章重复的学校经历"],
    },
    error_message: "",
    ...overrides,
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
  assert.equal(
    pageJson.usingComponents["ai-task-progress"],
    "/components/ai-task-progress/index",
  );
  assert.equal(
    pageJson.usingComponents["completion-issue-list"],
    "/components/completion-issue-list/index",
  );
});

test("book completion overview exposes confirmed chapter progress and one action per chapter", () => {
  const { bookCompletionViewPatch } = loadBookCompletionPage().mod;
  const patch = bookCompletionViewPatch(readyOverview());

  assert.equal(patch.progressText, "1 / 2 章正文已确认");
  assert.equal(patch.chapters[0].actionText, "确认");
  assert.equal(patch.chapters[1].actionText, "查看正文");
  assert.equal(patch.allChaptersConfirmed, false);
  assert.doesNotMatch(JSON.stringify(patch), /Story|Claim|slot|Contract|visible_tasks/);
});

test("book completion uses only persisted chapter counts for whole-book progress", () => {
  const { bookCompletionJobView } = loadBookCompletionPage().mod;
  const active = bookCompletionJobView(bulkJob({
    job_id: "book-job-1",
    status: "running",
    total_chapter_count: 4,
    processed_chapter_count: 2,
    succeeded_chapter_count: 2,
    current_chapter_id: "chapter-3",
    current_chapter_title: "参加工作",
    can_cancel: true,
  }));
  const failed = bookCompletionJobView(bulkJob({
    job_id: "book-job-1",
    status: "failed",
    total_chapter_count: 4,
    processed_chapter_count: 4,
    succeeded_chapter_count: 3,
    failed_chapter_count: 1,
    can_retry: true,
  }));

  assert.equal(active.bulkCheckProgressTitle, "正在检查《参加工作》");
  assert.equal(active.bulkCheckProgressDetail, "已处理 2/4 章，每章完成后都会保存");
  assert.equal(active.bulkCheckProgressMode, "determinate");
  assert.equal(active.bulkCheckProgressPercent, 50);
  assert.equal(active.bulkCheckCanCancel, true);
  assert.equal(failed.bulkCheckProgressTitle, "1 章检查未完成");
  assert.match(failed.bulkCheckProgressDetail, /已完成结果已保留/);
  assert.equal(failed.bulkCheckProgressMode, "hidden");
});

test("book completion returns to the book hub for generation and chapter management", () => {
  const tabNavigations = [];
  global.wx = {
    switchTab(options) {
      tabNavigations.push(options.url);
    },
  };
  const { page } = loadBookCompletionPage();
  const context = pageContext(page);

  page.openBookHome.call(context);

  assert.deepEqual(tabNavigations, ["/pages/book/index"]);
});

test("book completion opens book title settings directly", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadBookCompletionPage();
  const context = pageContext(page, { projectId: "project/1" });

  page.openBookSettings.call(context);

  assert.deepEqual(navigations, [
    "/pages/book-export/index?projectId=project%2F1&action=preview&settings=book",
  ]);
});

test("book completion runs the cross-chapter review after chapters are confirmed", async () => {
  const reviewRequests = [];
  const overview = readyOverview();
  overview.completed_chapters = 2;
  overview.chapters = overview.chapters.map((chapter) => ({
    ...chapter,
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
  }));
  let currentReview = null;
  global.wx = { showToast() {} };
  const { page } = loadBookCompletionPage({
    voiceApiExports: {
      getMemoirBookCompletionOverview: async () => overview,
      generateMemoirBookReview: async (projectId, force) => {
        reviewRequests.push([projectId, force]);
        currentReview = bookReview();
        return currentReview;
      },
      getMemoirBookReview: async () => currentReview,
      getMemoirBookOutput: async () => ({ id: "book-output-1" }),
    },
  });
  const context = pageContext(page, {
    projectId: "project-1",
  });

  await page.loadOverview.call(context);
  assert.equal(context.data.bulkCheckVisible, false);
  assert.equal(context.data.bookReview, null);
  await page.reviewBook.call(context);

  assert.deepEqual(reviewRequests, [["project-1", false]]);
  assert.equal(context.data.bookReview.review.summary, "全书围绕家庭与成长展开。");
  assert.equal(context.data.bookOutputAvailable, true);
});

test("book completion can check only chapters that have not been checked", async () => {
  const batchRequests = [];
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
      enqueueMemoirBookCompletionJob: async (projectId, retryJobId) => {
        batchRequests.push([projectId, retryJobId]);
        return bulkJob({
          job_id: "book-job-1",
          status: "running",
          total_chapter_count: 1,
          current_chapter_id: "chapter-1",
          current_chapter_title: "学校里的比赛",
          can_cancel: true,
        });
      },
      getMemoirBookCompletionJob: async () => bulkJob({
        job_id: "book-job-1",
        status: "running",
        total_chapter_count: 1,
        current_chapter_id: "chapter-1",
        current_chapter_title: "学校里的比赛",
        can_cancel: true,
      }),
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
  await page.checkPendingChapters.call(context);

  assert.deepEqual(batchRequests, [["project-1", null]]);
  assert.equal(context.data.recheckingAll, true);
  assert.equal(context.data.bulkCheckProgressMode, "determinate");
});

test("book completion keeps a submit error when only an older finished job exists", async () => {
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const { page, mod } = loadBookCompletionPage({
    voiceApiExports: {
      enqueueMemoirBookCompletionJob: async () => {
        throw new Error("network unavailable");
      },
      getMemoirBookCompletionJob: async () => bulkJob({
        job_id: "old-book-job",
        status: "succeeded",
        total_chapter_count: 2,
        processed_chapter_count: 2,
        succeeded_chapter_count: 2,
      }),
      getMemoirBookCompletionOverview: async () => readyOverview(),
    },
  });
  const patch = mod.bookCompletionViewPatch(readyOverview());
  const context = pageContext(page, {
    projectId: "project-1",
    chapters: patch.chapters,
    bulkCheckVisible: true,
  });

  await page.checkPendingChapters.call(context);

  assert.equal(context.data.recheckingAll, false);
  assert.equal(context.data.error, "network unavailable");
  assert.deepEqual(toasts, []);
});

test("book completion restores and polls an active whole-book check after reopening", async () => {
  const scheduled = [];
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  global.setTimeout = (callback, delay) => {
    scheduled.push([callback, delay]);
    return 1;
  };
  global.clearTimeout = () => {};
  global.wx = { showToast() {} };
  const overview = readyOverview();
  overview.chapters[0] = completionChapter({
    state: "preparing",
    next_action: {
      kind: "wait",
      title: "正在处理本章",
      detail: "可以进入本章查看逐节进度。",
      action_text: "查看进度",
      disabled: false,
    },
    active_issue: null,
  });
  try {
    const { page } = loadBookCompletionPage({
      voiceApiExports: {
        getMemoirBookCompletionJob: async () => bulkJob({
          job_id: "book-job-1",
          status: "running",
          total_chapter_count: 2,
          processed_chapter_count: 1,
          succeeded_chapter_count: 1,
          current_chapter_id: "chapter-1",
          current_chapter_title: "学校里的比赛",
          can_cancel: true,
        }),
        getMemoirBookCompletionOverview: async () => overview,
      },
    });
    const context = pageContext(page, { projectId: "project-1" });

    await page.onShow.call(context);

    assert.equal(context.data.recheckingAll, true);
    assert.equal(context.data.bulkCheckVisible, true);
    assert.equal(context.data.bulkCheckProgressPercent, 50);
    assert.equal(context.data.bulkCheckProgressTitle, "正在检查《学校里的比赛》");
    assert.equal(scheduled.length, 1);
    assert.equal(scheduled[0][1], 1500);
    page.onHide.call(context);
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
    delete global.wx;
  }
});

test("book completion preserves partial results and can cancel remaining checks", async () => {
  const cancellations = [];
  const failedJob = bulkJob({
    job_id: "book-job-1",
    status: "failed",
    total_chapter_count: 2,
    processed_chapter_count: 2,
    succeeded_chapter_count: 1,
    failed_chapter_count: 1,
    can_retry: true,
    error_message: "第二章检查失败",
  });
  const cancelledJob = bulkJob({
    job_id: "book-job-2",
    status: "cancelled",
    total_chapter_count: 2,
    processed_chapter_count: 2,
    succeeded_chapter_count: 1,
    cancelled_chapter_count: 1,
    can_retry: true,
  });
  let currentJob = failedJob;
  global.wx = { showToast() {} };
  const { page } = loadBookCompletionPage({
    voiceApiExports: {
      getMemoirBookCompletionJob: async () => currentJob,
      getMemoirBookCompletionOverview: async () => readyOverview(),
      cancelMemoirBookCompletionJob: async (projectId, jobId) => {
        cancellations.push([projectId, jobId]);
        currentJob = cancelledJob;
        return currentJob;
      },
    },
  });
  const context = pageContext(page, { projectId: "project-1" });

  await page.loadOverview.call(context);
  assert.equal(context.data.recheckingAll, false);
  assert.equal(context.data.bulkCheckProgressTitle, "1 章检查未完成");
  assert.match(context.data.bulkCheckProgressDetail, /已完成结果已保留/);

  currentJob = bulkJob({
    job_id: "book-job-2",
    status: "running",
    total_chapter_count: 2,
    processed_chapter_count: 1,
    succeeded_chapter_count: 1,
    current_chapter_id: "chapter-2",
    current_chapter_title: "参加工作",
    can_cancel: true,
  });
  await page.loadOverview.call(context, true);
  await page.cancelBulkCheck.call(context);

  assert.deepEqual(cancellations, [["project-1", "book-job-2"]]);
  assert.equal(context.data.bulkCheckProgressTitle, "全书检查已停止");
  assert.match(context.data.bulkCheckProgressDetail, /已完成结果已保留/);
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
      enqueueMemoirChapterPreparationJob: async (projectId, chapterId, operation) => {
        prepared.push([projectId, chapterId, operation]);
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

  assert.deepEqual(prepared, [["project-1", "chapter-1", "prepare"]]);
});
