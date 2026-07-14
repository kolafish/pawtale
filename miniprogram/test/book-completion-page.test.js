require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, mockModule } = require("./page-harness");

function loadBookCompletionPage({ voiceApiExports = {}, consent = true } = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirBookCompletionOverview: async () => {
      throw new Error("getMemoirBookCompletionOverview was not mocked");
    },
    prepareMemoirBookCompletionChapter: async () => {
      throw new Error("prepareMemoirBookCompletionChapter was not mocked");
    },
    createMemoirVoiceSession: async () => {
      throw new Error("createMemoirVoiceSession was not mocked");
    },
    ...voiceApiExports,
  });
  mockModule("../services/navigation.ts", {
    routeAuthError: () => false,
  });
  mockModule("../services/voice-authorization.ts", {
    readVoiceAuthorizationConsent: () => consent,
    syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
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
        headline: "还有一段重要经历值得补充",
        detail: "先讲完整经过，不需要逐个回答细节。",
        remaining_count: 1,
        focus_areas: ["请按顺序讲讲比赛的完整经过和结果。"],
        task_id: "task-1",
      },
      {
        chapter_id: "chapter-2",
        title: "参加工作",
        sort_order: 1,
        state: "complete",
        headline: "目前不需要再补",
        detail: "以后有新素材时会重新整理。",
        remaining_count: 0,
        focus_areas: [],
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
  assert.equal(patch.chapters[0].actionText, "开始讲这一段");
  assert.equal(patch.chapters[1].disabled, true);
  assert.doesNotMatch(JSON.stringify(patch), /Story|Claim|slot|Contract/);
});

test("book completion starts the existing realtime interview chain for a selected chapter", async () => {
  const created = [];
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const overview = readyOverview();
  const { page, mod } = loadBookCompletionPage({
    voiceApiExports: {
      createMemoirVoiceSession: async (input, options) => {
        created.push({ input, options });
        return { session: { id: "voice-1" } };
      },
    },
  });
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

  assert.equal(created[0].input.guidance_mode, "guided_interview");
  assert.equal(created[0].input.session_goal, "book_completion");
  assert.equal(created[0].input.current_followup_task_id, "task-1");
  assert.equal(created[0].options.projectId, "project-1");
  assert.match(navigations[0], /pages\/voice-chat\/index/);
  assert.match(navigations[0], /voiceSessionId=voice-1/);
  delete global.wx;
});
