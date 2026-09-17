require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  loadPageModule,
  mockMemoirApiModules,
  mockModule,
  pageContext,
  unmockedAsync,
} = require("./page-harness");

function loadBookCompletionPage({ voiceApiExports = {}, navigationExports = {} } = {}) {
  mockMemoirApiModules({
    getMemoirBookCompletionOverview: unmockedAsync("getMemoirBookCompletionOverview"),
    enqueueMemoirBookCompletionJob: unmockedAsync("enqueueMemoirBookCompletionJob"),
    cancelMemoirBookCompletionJob: unmockedAsync("cancelMemoirBookCompletionJob"),
    enqueueMemoirBookReviewJob: unmockedAsync("enqueueMemoirBookReviewJob"),
    cancelMemoirBookReviewJob: unmockedAsync("cancelMemoirBookReviewJob"),
    enqueueMemoirBookRevisionJob: unmockedAsync("enqueueMemoirBookRevisionJob"),
    cancelMemoirBookRevisionJob: unmockedAsync("cancelMemoirBookRevisionJob"),
    enqueueMemoirChapterPreparationJob: unmockedAsync("enqueueMemoirChapterPreparationJob"),
    ...voiceApiExports,
  });
  mockModule("../services/navigation.ts", {
    ...navigationExports,
    returnToBook: () => wx.redirectTo({ url: "/pages/book/index" }),
    routeAuthError: () => false,
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

function confirmedOverview() {
  const overview = readyOverview();
  return {
    ...overview,
    completed_chapters: overview.total_chapters,
    chapters: overview.chapters.map((chapter) => ({
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
    })),
  };
}

function bulkJob(overrides = {}) {
  return {
    job_id: null,
    project_id: "project-1",
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
      editorial_actions: [],
    },
    error_message: "",
    ...overrides,
  };
}

function reviewJob(overrides = {}) {
  return {
    job_id: null,
    project_id: "project-1",
    status: "idle",
    stage: "",
    total_chapter_count: 0,
    processed_chapter_count: 0,
    current_chapter_title: "",
    requested_ai_model: null,
    resolved_ai_model: null,
    review_id: null,
    can_cancel: false,
    can_retry: false,
    error_message: "",
    ...overrides,
  };
}

function revisionJob(overrides = {}) {
  return {
    job_id: null,
    project_id: "project-1",
    status: "idle",
    stage: "",
    include_answers: false,
    answer_count: 0,
    unresolved_count: 0,
    action_ids: [],
    selected_action_count: 0,
    changed_chapter_count: 0,
    summary: "",
    proposal_id: null,
    proposal_status: null,
    requested_ai_model: null,
    resolved_ai_model: null,
    already_applied: false,
    can_cancel: false,
    can_retry: false,
    error_message: "",
    ...overrides,
  };
}

function completionSnapshot(overview, overrides = {}) {
  return {
    ...overview,
    completion_job: bulkJob(),
    book_review: null,
    book_review_job: reviewJob(),
    book_issues: [],
    book_output_available: false,
    revision_job: revisionJob(),
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
  assert.equal(patch.canReviewBook, false);
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

  assert.equal(active.title, "正在检查《参加工作》");
  assert.equal(active.detail, "已处理 2/4 章，每章完成后都会保存");
  assert.equal(active.progressMode, "determinate");
  assert.equal(active.progress, 50);
  assert.equal(active.canCancel, true);
  assert.equal(failed.title, "1 章检查未完成");
  assert.match(failed.detail, /已完成结果已保留/);
  assert.equal(failed.progressMode, "hidden");
});

test("book review progress uses persisted stages without inventing model percentages", () => {
  const { bookReviewJobView } = loadBookCompletionPage().mod;
  const preparing = bookReviewJobView(reviewJob({
    job_id: "review-job-1",
    status: "running",
    stage: "preparing_chapters",
    total_chapter_count: 4,
    processed_chapter_count: 2,
    current_chapter_title: "参加工作",
    can_cancel: true,
  }));
  const reviewing = bookReviewJobView(reviewJob({
    job_id: "review-job-1",
    status: "running",
    stage: "reviewing",
    total_chapter_count: 4,
    processed_chapter_count: 4,
    can_cancel: true,
  }));

  assert.equal(preparing.title, "正在准备《参加工作》");
  assert.equal(preparing.progress, 50);
  assert.equal(preparing.progressMode, "determinate");
  assert.equal(reviewing.title, "正在检查全书");
  assert.equal(reviewing.progressMode, "indeterminate");
  assert.equal(reviewing.progress, 0);
  assert.equal(bookReviewJobView(reviewJob({
    job_id: "review-job-1",
    status: "succeeded",
    stage: "completed",
    total_chapter_count: 4,
    processed_chapter_count: 4,
  })).visible, false);
});

test("book revision reports its real background stage", () => {
  const { revisionJobView } = loadBookCompletionPage().mod;
  const revising = revisionJobView(revisionJob({
    job_id: "revision-job-1",
    status: "running",
    stage: "revising",
    answer_count: 2,
    can_cancel: true,
  }));
  const failed = revisionJobView(revisionJob({
    job_id: "revision-job-1",
    status: "failed",
    stage: "failed",
    answer_count: 2,
    can_retry: true,
    error_message: "模型暂时不可用",
  }));

  assert.equal(revising.title, "正在根据所选内容制定修改思路");
  assert.equal(revising.progressMode, "indeterminate");
  assert.equal(revising.canCancel, true);
  assert.equal(failed.title, "修改思路未完成");
  assert.equal(failed.detail, "模型暂时不可用");
});

test("book completion returns to the book hub for generation and chapter management", () => {
  const navigations = [];
  global.wx = {
    redirectTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadBookCompletionPage();
  const context = pageContext(page);

  page.openBookHome.call(context);

  assert.deepEqual(navigations, ["/pages/book/index"]);
});

test("book completion submits answers and editorial actions as one revision", async () => {
  const requests = [];
  const navigations = [];
  const review = bookReview({
    review: {
      summary: "全书围绕家庭与成长展开。",
      strengths: ["人物主线清楚"],
      structure_guidance: [],
      editorial_actions: [{
        id: "action/1",
        action_type: "remove_repetition",
        title: "压缩两章重复的学校经历",
        reason: "两章都完整讲述了同一场比赛。",
        chapter_ids: ["chapter-1", "chapter-2"],
      }],
    },
  });
  let currentJob = revisionJob({
    answer_count: 2,
  });
  global.wx = {
    showToast() {},
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadBookCompletionPage({
    voiceApiExports: {
      getMemoirBookCompletionOverview: async () => completionSnapshot(
        confirmedOverview(),
        {
          book_review: review,
          book_output_available: true,
          revision_job: currentJob,
        },
      ),
      enqueueMemoirBookRevisionJob: async (
        projectId,
        includeAnswers,
        actionIds,
      ) => {
        requests.push([projectId, includeAnswers, actionIds]);
        currentJob = revisionJob({
          job_id: "revision-job-1",
          status: "succeeded",
          stage: "completed",
          include_answers: true,
          answer_count: 2,
          action_ids: ["action/1"],
          selected_action_count: 1,
          proposal_id: "proposal/1",
          proposal_status: "ready",
        });
        return currentJob;
      },
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    projectTitle: "我的故事",
  });

  await page.onShow.call(context);
  page.toggleBookReviewAnswers.call(context);
  page.toggleEditorialAction.call(context, {
    currentTarget: { dataset: { actionId: "action/1" } },
  });
  context.data.requiredBookIssues = [{ id: "issue-1" }];
  await page.generateBookRevision.call(context);
  assert.deepEqual(requests, []);
  context.data.requiredBookIssues = [];
  await page.generateBookRevision.call(context);
  page.openBookRevision.call(context);

  assert.deepEqual(requests, [["project/1", true, ["action/1"]]]);
  assert.equal(context.data.revisionJob.proposal_status, "ready");
  assert.deepEqual(navigations, [
    "/pages/revision-workbench/index?scope=book&projectId=project%2F1&proposalId=proposal%2F1",
  ]);
  page.onHide.call(context);
  delete global.wx;
});

test("book completion restores the selection of an active revision", async () => {
  const review = bookReview({
    review: {
      summary: "全书围绕家庭与成长展开。",
      strengths: [],
      structure_guidance: [],
      editorial_actions: [{
        id: "action/1",
        action_type: "improve_transition",
        title: "补充章节衔接",
        reason: "相邻章节切换较突然。",
        chapter_ids: ["chapter-1"],
      }],
    },
  });
  global.wx = { showToast() {} };
  const { page } = loadBookCompletionPage({
    voiceApiExports: {
      getMemoirBookCompletionOverview: async () => completionSnapshot(
        confirmedOverview(),
        {
          book_review: review,
          revision_job: revisionJob({
            job_id: "revision-job-1",
            status: "running",
            stage: "revising",
            include_answers: true,
            answer_count: 2,
            action_ids: ["action/1"],
            selected_action_count: 1,
          }),
        },
      ),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });

  await page.onShow.call(context);

  assert.equal(context.data.includeBookReviewAnswers, true);
  assert.deepEqual(context.data.selectedEditorialActionIds, ["action/1"]);
  assert.equal(context.data.editorialActionViews[0].selected, true);
  page.onHide.call(context);
  delete global.wx;
});

test("book completion submits the cross-chapter review to the background", async () => {
  const reviewRequests = [];
  const overview = confirmedOverview();
  let currentReview = null;
  let currentReviewJob = reviewJob();
  global.wx = { showToast() {} };
  const { page } = loadBookCompletionPage({
    voiceApiExports: {
      getMemoirBookCompletionOverview: async () => completionSnapshot(
        overview,
        {
          book_review: currentReview,
          book_review_job: currentReviewJob,
          book_output_available: true,
        },
      ),
      enqueueMemoirBookReviewJob: async (projectId, force) => {
        reviewRequests.push([projectId, force]);
        currentReviewJob = reviewJob({
          job_id: "review-job-1",
          status: "pending",
          stage: "queued",
          total_chapter_count: 2,
          can_cancel: true,
        });
        return currentReviewJob;
      },
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
  assert.equal(context.data.checkingBook, true);
  assert.equal(context.data.bookReviewProgress.title, "全书检查已提交");
  assert.equal(context.data.bookReview, null);

  currentReview = bookReview();
  currentReviewJob = reviewJob({
    job_id: "review-job-1",
    status: "succeeded",
    stage: "completed",
    total_chapter_count: 2,
    processed_chapter_count: 2,
    review_id: "review-1",
  });
  await page.loadOverview.call(context, true);

  assert.equal(context.data.checkingBook, false);
  assert.equal(context.data.bookReview.review.summary, "全书围绕家庭与成长展开。");
});

test("book completion refreshes all visible state with one snapshot request", async () => {
  let requestCount = 0;
  const overview = confirmedOverview();
  global.wx = { showToast() {} };
  const { page } = loadBookCompletionPage({
    voiceApiExports: {
      getMemoirBookCompletionOverview: async () => {
        requestCount += 1;
        return completionSnapshot(overview, {
          book_review: bookReview(),
          book_output_available: true,
        });
      },
    },
  });
  const context = pageContext(page, { projectId: "project-1" });

  await page.loadOverview.call(context);

  assert.equal(requestCount, 1);
  assert.equal(context.data.bookReview.id, "review-1");
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
      getMemoirBookCompletionOverview: async () => completionSnapshot(
        overview,
        {
          completion_job: bulkJob({
            job_id: "book-job-1",
            status: "running",
            total_chapter_count: 1,
            current_chapter_id: "chapter-1",
            current_chapter_title: "学校里的比赛",
            can_cancel: true,
          }),
        },
      ),
    },
  });
  const patch = mod.bookCompletionViewPatch(overview);
  assert.equal(patch.chapters[0].stateText, "待检查");
  assert.equal(patch.bulkCheckVisible, true);
  assert.equal(patch.bulkCheckActionText, "检查待检查章节");

  const context = pageContext(page, {
    projectId: "project-1",
    chapters: patch.chapters,
    canReviewBook: false,
    bulkCheckVisible: true,
  });
  await page.checkPendingChapters.call(context);

  assert.deepEqual(batchRequests, [["project-1", null]]);
  assert.equal(context.data.recheckingAll, true);
  assert.equal(context.data.bulkCheckProgress.progressMode, "determinate");
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
      getMemoirBookCompletionOverview: async () => completionSnapshot(
        readyOverview(),
        {
          completion_job: bulkJob({
            job_id: "old-book-job",
            status: "succeeded",
            total_chapter_count: 2,
            processed_chapter_count: 2,
            succeeded_chapter_count: 2,
          }),
        },
      ),
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

test("book completion restores and polls an active chapter batch after reopening", async () => {
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
        getMemoirBookCompletionOverview: async () => completionSnapshot(
          overview,
          {
            completion_job: bulkJob({
              job_id: "book-job-1",
              status: "running",
              total_chapter_count: 2,
              processed_chapter_count: 1,
              succeeded_chapter_count: 1,
              current_chapter_id: "chapter-1",
              current_chapter_title: "学校里的比赛",
              can_cancel: true,
            }),
          },
        ),
      },
    });
    const context = pageContext(page, { projectId: "project-1" });

    await page.onShow.call(context);

    assert.equal(context.data.recheckingAll, true);
    assert.equal(context.data.bulkCheckVisible, true);
    assert.equal(context.data.bulkCheckProgress.progress, 50);
    assert.equal(context.data.bulkCheckProgress.title, "正在检查《学校里的比赛》");
    assert.equal(scheduled.length, 1);
    assert.equal(scheduled[0][1], 1500);
    page.onHide.call(context);
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
    delete global.wx;
  }
});

test("book completion restores and can cancel an active full-book review for unconfirmed drafts after reopening", async () => {
  const scheduled = [];
  const cancellations = [];
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  global.setTimeout = (callback, delay) => {
    scheduled.push([callback, delay]);
    return 1;
  };
  global.clearTimeout = () => {};
  global.wx = { showToast() {} };
  const overview = confirmedOverview();
  overview.chapters = overview.chapters.map((chapter) => ({ ...chapter, state: "draft_ready" }));
  let currentReviewJob = reviewJob({
    job_id: "review-job-1",
    status: "running",
    stage: "reviewing",
    total_chapter_count: 2,
    processed_chapter_count: 2,
    can_cancel: true,
  });
  try {
    const { page } = loadBookCompletionPage({
      voiceApiExports: {
        getMemoirBookCompletionOverview: async () => completionSnapshot(
          overview,
          { book_review_job: currentReviewJob },
        ),
        cancelMemoirBookReviewJob: async (projectId, jobId) => {
          cancellations.push([projectId, jobId]);
          currentReviewJob = reviewJob({
            job_id: jobId,
            status: "cancelled",
            stage: "cancelled",
            total_chapter_count: 2,
            processed_chapter_count: 2,
            can_retry: true,
          });
          return currentReviewJob;
        },
      },
    });
    const context = pageContext(page, { projectId: "project-1" });

    await page.onShow.call(context);

    assert.equal(context.data.checkingBook, true);
    assert.equal(context.data.bookReviewProgress.title, "正在检查全书");
    assert.equal(context.data.bookReviewProgress.progressMode, "indeterminate");
    assert.equal(scheduled.length, 1);
    assert.equal(scheduled[0][1], 1500);

    await page.cancelBookReview.call(context);

    assert.deepEqual(cancellations, [["project-1", "review-job-1"]]);
    assert.equal(context.data.checkingBook, false);
    assert.equal(context.data.bookReviewProgress.title, "全书检查已停止");
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
      getMemoirBookCompletionOverview: async () => completionSnapshot(
        readyOverview(),
        { completion_job: currentJob },
      ),
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
  assert.equal(context.data.bulkCheckProgress.title, "1 章检查未完成");
  assert.match(context.data.bulkCheckProgress.detail, /已完成结果已保留/);

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
  assert.equal(context.data.bulkCheckProgress.title, "全书检查已停止");
  assert.match(context.data.bulkCheckProgress.detail, /已完成结果已保留/);
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
      getMemoirBookCompletionOverview: async () => completionSnapshot(overview),
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

test("unconfirmed readable chapters can review the book and stale manual decisions remain visible", () => {
  const { bookCompletionViewPatch, bookReviewViewPatch } = loadBookCompletionPage().mod;
  const overview = confirmedOverview();
  overview.chapters = overview.chapters.map((chapter) => ({ ...chapter, state: "draft_ready" }));
  const view = bookCompletionViewPatch(overview);
  assert.equal(view.canReviewBook, true);
  const issues = [{ id: "privacy", scope: "book", channel: "manual_review", status: "open", input_signature: "older" },
    { id: "optional", scope: "book", channel: "interview", status: "open", input_signature: "older" }];
  const review = bookReviewViewPatch(null, issues);
  assert.deepEqual(review.requiredBookIssues.map((issue) => issue.id), ["privacy"]);
  assert.deepEqual(review.optionalBookIssues, []);
});
