require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

const {
  sampleDraft,
  sampleDraftWithContent,
  samplePlan,
  sampleRef,
  sampleSourceCoverage,
} = require("./book-fixtures");

function loadBookChapterPage({
  plan = samplePlan("confirmed"),
  draft = sampleDraft(1, "confirmed"),
  coverage = sampleSourceCoverage(0),
  voiceApiExports = {},
} = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    applyMemoirBookPlanStructureCommand: async () => {
      throw new Error("applyMemoirBookPlanStructureCommand was not mocked");
    },
    confirmMemoirBookChapterDraft: async () => {
      throw new Error("confirmMemoirBookChapterDraft was not mocked");
    },
    enqueueMemoirBookChapterDraftGeneration: async () => {
      throw new Error("enqueueMemoirBookChapterDraftGeneration was not mocked");
    },
    generateMemoirBookChapterTitle: async () => {
      throw new Error("generateMemoirBookChapterTitle was not mocked");
    },
    getMemoirBookPlan: async () => plan,
    getMemoirBookChapterDraft: async () => draft,
    getMemoirBookSourceCoverage: async () => coverage,
    updateMemoirBookChapterDraft: async () => {
      throw new Error("updateMemoirBookChapterDraft was not mocked");
    },
    updateMemoirBookChapterTitle: async () => {
      throw new Error("updateMemoirBookChapterTitle was not mocked");
    },
    ...voiceApiExports,
  });
  return loadPageModule("../pages/book-chapter/index.ts");
}

test("book chapter page is registered", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));

  assert.ok(appJson.pages.includes("pages/book-chapter/index"));
});

test("book chapter page loads a chapter from route query", async () => {
  const { page } = loadBookChapterPage();
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: encodeURIComponent("project/1"),
    chapterId: "chapter-1",
    tab: "sources",
  });

  assert.equal(context.data.projectId, "project/1");
  assert.equal(context.data.chapterId, "chapter-1");
  assert.equal(context.data.activeTab, "sources");
  assert.equal(context.data.planTitle, "外婆回忆录章节规划");
  assert.equal(context.data.chapter.title, "外婆家的童年");
  assert.equal(context.data.chapterIndexText, "第 1 章");
  assert.equal(context.data.contentText, "我小时候常在外婆家的院子里晒太阳。");
  assert.equal(context.data.sourceCountText, "1 条原始讲述");
  assert.match(context.data.chapter.sourceViews[0].labelText, /语音记录 1/);
  assert.equal(context.data.chapter.mergeTargetOptions[0].value, "chapter-2");
});

test("book chapter page handles missing route query and tab changes", async () => {
  const { page } = loadBookChapterPage();
  const context = pageContext(page);

  await page.onLoad.call(context, {});
  assert.equal(context.data.error, "缺少章节信息");

  page.changeTab.call(context, { currentTarget: { dataset: { tab: "more" } } });
  assert.equal(context.data.activeTab, "more");

  page.changeTab.call(context, { currentTarget: { dataset: { tab: "unknown" } } });
  assert.equal(context.data.activeTab, "content");
});

test("book chapter page saves edited chapter title", async () => {
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const updatedPlan = samplePlan("confirmed");
  updatedPlan.chapters[0].title = "童年院子";
  const updates = [];
  const { page } = loadBookChapterPage({
    voiceApiExports: {
      updateMemoirBookChapterTitle: async (projectId, chapterId, data) => {
        updates.push({ projectId, chapterId, data });
        return updatedPlan;
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1" });

  page.changeChapterTitle.call(context, { detail: { value: "童年院子" } });
  assert.equal(context.data.chapter.titleChanged, true);
  await page.saveChapterTitle.call(context);

  assert.deepEqual(updates, [
    { projectId: "project/1", chapterId: "chapter-1", data: { title: "童年院子" } },
  ]);
  assert.equal(context.data.chapter.title, "童年院子");
  assert.equal(context.data.chapter.titleChanged, false);
  assert.deepEqual(toasts, ["章名已保存"]);
  delete global.wx;
});

test("book chapter page saves and confirms edited draft content", async () => {
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const updates = [];
  const confirms = [];
  const { page } = loadBookChapterPage({
    draft: sampleDraftWithContent("旧正文", "draft", 1),
    voiceApiExports: {
      updateMemoirBookChapterDraft: async (projectId, chapterId, data) => {
        updates.push({ projectId, chapterId, data });
        return sampleDraftWithContent(data.content, "draft", 2);
      },
      confirmMemoirBookChapterDraft: async (projectId, chapterId) => {
        confirms.push({ projectId, chapterId });
        return sampleDraftWithContent("新正文", "confirmed", 2);
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1" });

  page.changeDraftContent.call(context, { detail: { value: "新正文" } });
  assert.equal(context.data.chapter.draftChanged, true);
  await page.saveChapterDraft.call(context);

  assert.deepEqual(updates, [
    { projectId: "project/1", chapterId: "chapter-1", data: { content: "新正文" } },
  ]);
  assert.equal(context.data.chapter.draftChanged, false);
  assert.equal(context.data.chapter.draftVersionText, "历史版本 · 已修改 2 次");

  await page.confirmChapterDraft.call(context);

  assert.deepEqual(confirms, [{ projectId: "project/1", chapterId: "chapter-1" }]);
  assert.equal(context.data.chapter.draft.status, "confirmed");
  assert.deepEqual(toasts, ["章节稿已保存", "本章已确认"]);
  delete global.wx;
});

test("book chapter page generates a new draft version without overwriting unsaved edits", async () => {
  const toasts = [];
  const calls = [];
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const { page } = loadBookChapterPage({
    draft: sampleDraftWithContent("第一版正文", "draft", 1),
    voiceApiExports: {
      enqueueMemoirBookChapterDraftGeneration: async (projectId, chapterId, data) => {
        calls.push({ projectId, chapterId, data });
        return {};
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1" });

  await page.generateChapterDraft.call(context);

  assert.deepEqual(calls, [
    { projectId: "project/1", chapterId: "chapter-1", data: { force_regenerate: true } },
  ]);
  assert.equal(context.data.chapter.draftBusy, false);
  assert.deepEqual(toasts, ["已后台生成"]);

  page.changeDraftContent.call(context, { detail: { value: "未保存正文" } });
  await page.generateChapterDraft.call(context);

  assert.equal(calls.length, 1);
  assert.equal(context.data.chapter.draftError, "请先保存当前修改，再生成新版本");
  delete global.wx;
});

test("book chapter page moves and splits chapters through structure commands", async () => {
  const plan = samplePlan("confirmed");
  plan.chapters[0].source_refs.push(sampleRef("utterance-3", "屋檐下的夏天"));
  const commands = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const { page } = loadBookChapterPage({
    plan,
    voiceApiExports: {
      applyMemoirBookPlanStructureCommand: async (projectId, command) => {
        commands.push({ projectId, command });
        return plan;
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1", tab: "more" });

  await page.moveChapter.call(context, { currentTarget: { dataset: { direction: "down" } } });
  page.toggleSplitSource.call(context, { currentTarget: { dataset: { utteranceId: "utterance-1" } } });
  assert.equal(context.data.chapter.splitSelectedCount, 1);
  assert.equal(context.data.chapter.canSplitSelectedSources, true);
  await page.splitSelectedChapterSources.call(context);

  assert.deepEqual(commands, [
    {
      projectId: "project/1",
      command: { type: "move_chapter", chapter_id: "chapter-1", direction: "down" },
    },
    {
      projectId: "project/1",
      command: {
        type: "split_chapter",
        chapter_id: "chapter-1",
        title: "外婆家的童年（拆出）",
        utterance_ids: ["utterance-1"],
      },
    },
  ]);
  assert.deepEqual(toasts, ["章节顺序已调整", "章节已拆分"]);
  assert.deepEqual(context.data.splitSourceSelections, []);
  delete global.wx;
});

test("book chapter page confirms merge and delete structure commands", async () => {
  const commands = [];
  const navigations = [];
  const toasts = [];
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
    showToast(options) {
      toasts.push(options.title);
    },
    navigateBack() {
      navigations.push("back");
    },
  };
  const plan = samplePlan("confirmed");
  const { page } = loadBookChapterPage({
    plan,
    voiceApiExports: {
      applyMemoirBookPlanStructureCommand: async (projectId, command) => {
        commands.push({ projectId, command });
        return plan;
      },
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project%2F1", chapterId: "chapter-1", tab: "more" });

  await page.mergeChapterInto.call(context, { detail: { value: "0" } });
  await page.deleteChapter.call(context);

  assert.deepEqual(commands, [
    {
      projectId: "project/1",
      command: { type: "merge_chapters", chapter_id: "chapter-1", target_chapter_id: "chapter-2" },
    },
    {
      projectId: "project/1",
      command: { type: "delete_chapter", chapter_id: "chapter-1" },
    },
  ]);
  assert.deepEqual(toasts, ["章节已合并", "章节已删除"]);
  assert.deepEqual(navigations, ["back", "back"]);
  delete global.wx;
});
