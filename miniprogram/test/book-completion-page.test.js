require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, mockModule } = require("./page-harness");

function loadBookCompletionPage({ voiceApiExports = {} } = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirBookCompletionOverview: async () => {
      throw new Error("getMemoirBookCompletionOverview was not mocked");
    },
    enqueueMemoirChapterCompletionRefresh: async () => {
      throw new Error("enqueueMemoirChapterCompletionRefresh was not mocked");
    },
    ...voiceApiExports,
  });
  mockModule("../services/navigation.ts", {
    routeAuthError: () => false,
  });
  return loadPageModule("../pages/book-completion/index.ts");
}

function readyOverview() {
  return {
    project_id: "project-1",
    headline: "先看全书，再选择要补的章节",
    detail: "全书共 2 章，其中 1 章目前不需要再补。",
    total_chapters: 2,
    completed_chapters: 1,
    chapters: [
      {
        chapter_id: "chapter-1",
        title: "学校里的比赛",
        sort_order: 0,
        state: "ready",
        refresh_status: "succeeded",
        refresh_is_current: true,
        next_action: {
          kind: "start_interview",
          eyebrow: "选择一个方向继续讲",
          title: "这段回忆为什么适合作为本章开头？",
          detail: "围绕这个方向讲您记得的内容，不必逐项作答。",
          action_text: "从这个方向开始",
          disabled: false,
          task_id: "task-1",
        },
        must_handle_count: 0,
        suggested_count: 1,
        accepted_incomplete_count: 0,
        active_task: {
          id: "task-1",
          gap_id: "gap-1",
          question: "这段回忆为什么适合作为本章开头？",
          purpose: "补充开头。",
          known_context_summary: "",
          priority: 2000,
          status: "pending",
          active_session_id: null,
        },
        visible_tasks: [{
          id: "task-1",
          gap_id: "gap-1",
          question: "这段回忆为什么适合作为本章开头？",
          purpose: "补充开头。",
          known_context_summary: "",
          priority: 2000,
          status: "pending",
          active_session_id: null,
        }],
        editorial_gaps: [],
        review_gaps: [],
        draft_readiness: "blocked",
        headline: "还有一个重要方向值得补充",
        detail: "围绕这个方向讲您记得的内容，不必逐项作答。",
      },
      {
        chapter_id: "chapter-2",
        title: "参加工作",
        sort_order: 1,
        state: "complete",
        refresh_status: "succeeded",
        refresh_is_current: true,
        next_action: {
          kind: "complete",
          eyebrow: "本章已整理",
          title: "这一章目前没有必须处理的内容",
          detail: "以后增加新素材时，系统会重新给出下一步。",
          action_text: "",
          disabled: true,
          task_id: null,
        },
        must_handle_count: 0,
        suggested_count: 0,
        accepted_incomplete_count: 0,
        active_task: null,
        visible_tasks: [],
        editorial_gaps: [],
        review_gaps: [],
        draft_readiness: "ready",
        headline: "目前不需要再补",
        detail: "以后有新素材时会重新整理。",
      },
    ],
  };
}

test("book completion overview page is registered", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  assert.ok(appJson.pages.includes("pages/book-completion/index"));
});

test("book completion overview exposes chapter-level progress without internal model terms", () => {
  const { bookCompletionViewPatch } = loadBookCompletionPage().mod;
  const patch = bookCompletionViewPatch(readyOverview());

  assert.equal(patch.progressText, "1 / 2 章目前不需要再补");
  assert.equal(patch.chapters[0].actionText, "从这个方向开始");
  assert.deepEqual(patch.chapters[0].focusAreas, ["这段回忆为什么适合作为本章开头？"]);
  assert.equal(patch.chapters[1].actionText, "查看本章情况");
  assert.equal(patch.chapters[1].disabled, false);
  assert.doesNotMatch(JSON.stringify(patch), /Story|Claim|slot|Contract/);
});

test("book completion opens the chapter overview before starting an interview", async () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const overview = readyOverview();
  const { page, mod } = loadBookCompletionPage();
  const context = {
    ...page,
    data: {
      ...page.data,
      projectId: "project-1",
      projectTitle: "爷爷的故事",
      chapters: mod.bookCompletionViewPatch(overview).chapters,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.tapChapter.call(context, {
    currentTarget: { dataset: { chapterId: "chapter-1" } },
  });

  assert.match(navigations[0], /pages\/chapter-workspace\/index/);
  assert.match(navigations[0], /chapterId=chapter-1/);
  delete global.wx;
});
