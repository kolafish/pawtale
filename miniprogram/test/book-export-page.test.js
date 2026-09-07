require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { mockMemoirApiModules, loadPageModule, mockModule, pageContext } = require("./page-harness");

const {
  sampleAutomationEvent,
  sampleBookManuscript: baseSampleBookManuscript,
  sampleBookOutput: baseSampleBookOutput,
  sampleBookOutputVersion: baseSampleBookOutputVersion,
  samplePlan: baseSamplePlan,
} = require("./book-fixtures");

function samplePlan(status = "confirmed") {
  return baseSamplePlan(status, { chapterCount: 1 });
}

function sampleBookManuscript(title = "外婆回忆录", coverStyle = "classic_plain") {
  return baseSampleBookManuscript(title, coverStyle, { chapterCount: 1 });
}

function sampleBookOutputVersion(
  version,
  content,
  createdAt = "2026-06-17T00:00:00.000Z",
  manuscript = sampleBookManuscript(),
) {
  return baseSampleBookOutputVersion(
    version,
    content,
    createdAt,
    manuscript,
  );
}

function sampleBookOutput() {
  const latestVersion = sampleBookOutputVersion(2, "全书第二版正文");
  const historicalVersion = sampleBookOutputVersion(
    1,
    "全书第一版正文",
    "2026-06-16T00:00:00.000Z",
  );
  return {
    ...baseSampleBookOutput(2, "全书第二版正文", false, [], [
      historicalVersion,
      latestVersion,
    ].map(({ id, version, created_by, created_at }) => ({
      id,
      version,
      created_by,
      created_at,
    })), latestVersion.manuscript),
    latest_version: latestVersion,
  };
}

function loadBookExportPage(voiceApiExports = {}) {
  mockMemoirApiModules({
    getMemoirBookPlan: async () => samplePlan(),
    getMemoirBookOutput: async () => sampleBookOutput(),
    getMemoirBookOutputVersion: async () => sampleBookOutputVersion(1, "全书第一版正文"),
    downloadMemoirBookOutputExport: async () => "/tmp/book.pdf",
    suggestMemoirBookOutputTitles: async () => ({ suggestions: [] }),
    assembleMemoirBookOutput: async () => undefined,
    updateMemoirBookOutput: async () => sampleBookOutput(),
    enqueueMemoirBookOutputEditorialCopyGeneration: async () => sampleAutomationEvent({
      id: "event-1",
      event_type: "book_editorial_copy_generated",
      chapter_id: null,
      summary_text: "后记已提交后台重生成。",
      metadata_json: { target_label: "后记", targets: ["afterword"] },
    }),
    listMemoirAutomationEvents: async () => ({ items: [], total: 0, limit: 20 }),
    ...voiceApiExports,
  });
  return loadPageModule("../pages/book-export/index.ts");
}

test("book export page is registered", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  const pageJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../pages/book-export/index.json"), "utf8"));

  assert.ok(appJson.pages.includes("pages/book-export/index"));
  assert.equal(pageJson.navigationBarTitleText, "阅读与导出");
});

test("book export page loads plan and output from query project", async () => {
  const calls = [];
  const { page } = loadBookExportPage({
    getMemoirBookPlan: async (projectId) => {
      calls.push(["plan", projectId]);
      return samplePlan();
    },
    getMemoirBookOutput: async (projectId) => {
      calls.push(["output", projectId]);
      return sampleBookOutput();
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project/1", action: "print_pdf" });

  assert.deepEqual(calls, [
    ["plan", "project/1"],
    ["output", "project/1"],
  ]);
  assert.equal(context.data.projectId, "project/1");
  assert.equal(context.data.requestedAction, "print_pdf");
  assert.equal(context.data.exportNotice, "打印版字号更大，适合装订保存。");
  assert.equal(context.data.exportActionHintText, "适合拿去打印装订，页边距更宽。");
  assert.equal(context.data.bookContent, "全书第二版正文");
  assert.equal(context.data.bookPreviewChapters.length, 1);
  assert.equal(context.data.bookPreviewChapters[0].sections.length, 2);
  assert.equal(
    context.data.bookPreviewChapters[0].sections[0].excerpt,
    "我小时候常在院子里晒太阳。",
  );
  assert.equal(context.data.bookManuscriptAvailable, true);
  assert.equal(context.data.bookManuscriptTemplateLabel, "素雅书籍版");
  assert.equal(context.data.bookManuscriptStructureItems.some((item) => item.title === "目录"), true);
  assert.equal(context.data.bookManuscriptChapterItems[0].intro, "这一章主要记录外婆小时候在院子里的生活。");
  assert.equal(context.data.bookManuscriptBackMatterItems.some((item) => item.title === "原始讲述索引"), true);
  assert.equal("bookOutput" in context.data, false);
  assert.equal("plan" in context.data, false);
  assert.equal("chapters" in context.data, false);
});

test("book export page keeps a production-sized book within the page-data budget", () => {
  const output = sampleBookOutput();
  const chapterBody = "那年院子里的桂花开了，我们围在桌边听外婆慢慢讲从前。".repeat(200);
  const chapters = Array.from({ length: 8 }, (_, index) => ({
    ...output.latest_version.manuscript.chapters[0],
    chapter_id: `chapter-${index + 1}`,
    index: index + 1,
    title: `第 ${index + 1} 段往事`,
    content: chapterBody,
    sections: [],
  }));
  output.latest_version.content = chapters
    .map((chapter) => `第 ${chapter.index} 章 ${chapter.title}\n\n${chapter.content}`)
    .join("\n\n");
  assert.ok(output.latest_version.content.length > 40_000);
  output.latest_version.manuscript.chapters = chapters;
  output.latest_version.manuscript.table_of_contents = chapters.map((chapter) => ({
    chapter_id: chapter.chapter_id,
    index: chapter.index,
    title: chapter.title,
    char_count: chapter.content.length,
    source_count: 1,
    sections: [],
  }));
  output.versions = Array.from({ length: 9 }, (_, index) => ({
    id: `book-output-version-${index + 1}`,
    version: index + 1,
    created_by: "system",
    created_at: `2026-06-${String(index + 1).padStart(2, "0")}T00:00:00.000Z`,
  }));
  const { page } = loadBookExportPage();
  const context = pageContext(page);

  page.applyBookExportData.call(context, samplePlan(), output);

  const pageDataBytes = Buffer.byteLength(JSON.stringify(context.data), "utf8");
  assert.ok(pageDataBytes < 512 * 1024, `Page.data is ${pageDataBytes} bytes`);
  assert.equal("bookOutput" in context.data, false);
  assert.equal("plan" in context.data, false);
  assert.equal("chapters" in context.data, false);
});

test("book export opens book settings when linked from whole-book review", async () => {
  const completedEvent = sampleAutomationEvent({
    event_type: "book_editorial_copy_generated",
    status: "completed",
    chapter_id: null,
    summary_text: "前言已重生成，可以预览或导出。",
    metadata_json: { target_label: "前言", targets: ["preface"] },
  });
  let outputLoads = 0;
  const { page } = loadBookExportPage({
    getMemoirBookPlan: async () => samplePlan(),
    getMemoirBookOutput: async () => {
      outputLoads += 1;
      return sampleBookOutput();
    },
    listMemoirAutomationEvents: async () => ({
      items: [completedEvent],
      total: 1,
      limit: 20,
    }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    action: "preview",
    settings: "book",
  });

  assert.equal(context.data.showBookSettings, true);
  assert.equal(context.data.bookEditorialCopyEventItem.statusText, "已完成");
  assert.equal(outputLoads, 1);
});

test("book export refreshes when an initial completed event is newer than the loaded output", async () => {
  const output = sampleBookOutput();
  output.updated_at = "2026-06-17T00:00:00.000Z";
  const completedEvent = sampleAutomationEvent({
    event_type: "book_editorial_copy_generated",
    status: "completed",
    chapter_id: null,
    updated_at: "2026-06-17T00:01:00.000Z",
  });
  let outputLoads = 0;
  const { page } = loadBookExportPage({
    getMemoirBookPlan: async () => samplePlan(),
    getMemoirBookOutput: async () => {
      outputLoads += 1;
      return output;
    },
    listMemoirAutomationEvents: async () => ({
      items: [completedEvent],
      total: 1,
      limit: 20,
    }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    settings: "book",
  });

  assert.equal(outputLoads, 2);
});

test("book export page counts only chapters included in the final book", () => {
  const { page } = loadBookExportPage();
  const context = pageContext(page);
  const plan = baseSamplePlan("confirmed", { chapterCount: 2 });
  plan.chapters[1].include_in_book = false;

  page.applyBookExportData.call(context, plan, sampleBookOutput());

  assert.equal(context.data.totalChapterCount, 1);
});

test("book export page explains the selected export action", () => {
  const { page } = loadBookExportPage();
  const context = pageContext(page);

  page.previewExportActionHint.call(context, {
    currentTarget: { dataset: { action: "text" } },
  });
  assert.equal(context.data.exportActionHintText, "导出纯文字，方便复制和继续编辑。");

  page.previewExportActionHint.call(context, {
    currentTarget: { dataset: { action: "reading_pdf" } },
  });
  assert.equal(context.data.exportActionHintText, "适合微信分享和手机阅读。");
});

test("book export keeps advanced settings collapsed and opens immersive reading", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadBookExportPage();
  const context = pageContext(page, {
    projectId: "project/1",
    bookContent: "全书正文",
    bookSelectedVersionId: "book-output-version-2",
  });

  assert.equal(context.data.showBookSettings, false);
  page.toggleBookSettings.call(context);
  assert.equal(context.data.showBookSettings, true);

  page.openBookReader.call(context);
  assert.deepEqual(navigations, [
    "/pages/book-reader/index?projectId=project%2F1&versionId=book-output-version-2",
  ]);
});

test("book export does not save settings while viewing a historical version", async () => {
  let updateCalls = 0;
  const { page } = loadBookExportPage({
    updateMemoirBookOutput: async () => {
      updateCalls += 1;
      return sampleBookOutput();
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    bookViewingHistoricalVersion: true,
  });

  await page.saveBookDesignSettings.call(context);

  assert.equal(updateCalls, 0);
});

test("book export page saves full-book metadata and cover style", async () => {
  const updates = [];
  const { page } = loadBookExportPage({
    updateMemoirBookOutput: async (projectId, payload) => {
      updates.push({ projectId, payload });
      return sampleBookOutput();
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  assert.deepEqual(
    context.data.bookCoverStyleOptions.map((option) => option.value),
    ["classic_plain", "warm_gray", "dark_album", "moonlit_courtyard", "navy_gilt"],
  );

  page.changeBookTitle.call(context, {
    currentTarget: { dataset: {} },
    detail: { value: "外婆的月光" },
  });
  page.changeBookMetadataField.call(context, {
    currentTarget: { dataset: { key: "subtitle" } },
    detail: { value: "根据口述访谈整理" },
  });
  page.changeBookMetadataField.call(context, {
    currentTarget: { dataset: { key: "narrator_name" } },
    detail: { value: "外婆" },
  });
  page.changeBookMetadataField.call(context, {
    currentTarget: { dataset: { key: "preface" } },
    detail: { value: "写给家人的开篇。" },
  });
  page.changeBookCoverStyle.call(context, {
    currentTarget: { dataset: {} },
    detail: { value: "3" },
  });

  await page.saveBookDesignSettings.call(context);

  assert.deepEqual(updates[0], {
    projectId: "project/1",
    payload: {
      title: "外婆的月光",
      subtitle: "根据口述访谈整理",
      narrator_name: "外婆",
      prepared_by: "家人整理",
      preface: "写给家人的开篇。",
      afterword: "这些故事还可以继续补充。",
      back_cover_quote: "我小时候常在院子里晒太阳。",
      cover_style: "moonlit_courtyard",
    },
  });
  assert.equal(context.data.bookTitleChanged, false);
  assert.equal(context.data.bookMetadataChanged, false);
  assert.equal(context.data.bookCoverStyleChanged, false);
  assert.equal(context.data.bookDesignSaveStatusText, "已自动保存");
});

test("book export page suggests titles and queues book-level editorial copy", async () => {
  const titleRequests = [];
  const editorialRequests = [];
  const navigations = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
    redirectTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadBookExportPage({
    suggestMemoirBookOutputTitles: async (projectId, payload) => {
      titleRequests.push({ projectId, payload });
      return { suggestions: [" 外婆的月光 ", "庭院往事"] };
    },
    enqueueMemoirBookOutputEditorialCopyGeneration: async (projectId, payload) => {
      editorialRequests.push({ projectId, payload });
      return sampleAutomationEvent({
        id: "event-1",
        event_type: "book_editorial_copy_generated",
        chapter_id: null,
        summary_text: "后记已提交后台重生成。",
        metadata_json: { target_label: "后记", targets: ["afterword"] },
      });
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  await page.suggestBookTitles.call(context);
  page.chooseBookTitleSuggestion.call(context, {
    currentTarget: { dataset: { title: "外婆的月光" } },
  });
  assert.deepEqual(context.data.bookTitleSuggestions, ["外婆的月光", "庭院往事"]);
  assert.equal(context.data.bookTitleInput, "外婆的月光");

  page.clearBookDesignAutosaveTimer.call(context);
  await page.regenerateBookEditorialCopy.call(context, {
    currentTarget: { dataset: { target: "afterword" } },
  });

  assert.deepEqual(titleRequests[0], { projectId: "project/1", payload: { count: 6 } });
  assert.equal(context.data.bookTitleInput, "外婆的月光");
  assert.deepEqual(editorialRequests[0], {
    projectId: "project/1",
    payload: { targets: ["afterword"] },
  });
  assert.equal(toasts[0].title, "已开始后台生成");
  assert.equal(context.data.bookEditorialCopyBusyTarget, "");
  assert.equal(context.data.bookEditorialCopyEventItem.titleText, "文案生成中");
  assert.equal(context.data.bookEditorialCopyEventItem.statusText, "处理中");
  assert.equal(context.data.bookEditorialCopyEventItem.summaryText, "后记已提交后台重生成。");
  page.openBookHome.call(context);
  assert.deepEqual(navigations, ["/pages/book/index"]);
  const { takeBookWorkspaceNavigationTarget } = require("../services/book-workspace-navigation.ts");
  assert.equal(takeBookWorkspaceNavigationTarget(), "tools");
  page.clearBookEditorialCopyPoll.call(context);
});

test("book export page submits only one editorial copy task at a time", async () => {
  const editorialRequests = [];
  let resolveEnqueue;
  global.wx = {
    showToast() {},
  };
  const { page } = loadBookExportPage({
    enqueueMemoirBookOutputEditorialCopyGeneration: async (projectId, payload) => {
      editorialRequests.push({ projectId, payload });
      await new Promise((resolve) => {
        resolveEnqueue = resolve;
      });
      return sampleAutomationEvent({
        id: "event-1",
        event_type: "book_editorial_copy_generated",
        chapter_id: null,
        summary_text: "前言已提交后台重生成。",
        metadata_json: { target_label: "前言", targets: ["preface"] },
      });
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  const firstSubmission = page.regenerateBookEditorialCopy.call(context, {
    currentTarget: { dataset: { target: "preface" } },
  });
  await new Promise((resolve) => setImmediate(resolve));
  await page.regenerateBookEditorialCopy.call(context, {
    currentTarget: { dataset: { target: "afterword" } },
  });

  assert.deepEqual(editorialRequests, [{
    projectId: "project/1",
    payload: { targets: ["preface"] },
  }]);
  assert.equal(context.data.bookEditorialCopyBusyTarget, "preface");

  resolveEnqueue();
  await firstSubmission;
  assert.equal(context.data.bookEditorialCopyBusyTarget, "");
  page.clearBookEditorialCopyPoll.call(context);
});

test("book export page refreshes rewritten copy when the background task completes", async () => {
  const updatedOutput = sampleBookOutput();
  updatedOutput.latest_version.manuscript.back_matter.afterword.content = "新的后记。";
  const pendingEvent = sampleAutomationEvent({
    id: "event-1",
    event_type: "book_editorial_copy_generated",
    chapter_id: null,
    summary_text: "后记已提交后台重生成。",
    metadata_json: { target_label: "后记", targets: ["afterword"] },
  });
  const completedEvent = {
    ...pendingEvent,
    status: "completed",
    summary_text: "后记已重生成，可以预览或导出。",
    updated_at: "2026-06-17T00:01:00.000Z",
  };
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadBookExportPage({
    listMemoirAutomationEvents: async () => ({
      items: [completedEvent],
      total: 1,
      limit: 20,
    }),
    getMemoirBookOutput: async () => updatedOutput,
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());
  page.applyBookEditorialCopyEvent.call(context, pendingEvent);

  await page.refreshBookEditorialCopyProgress.call(context);

  assert.equal(context.data.bookEditorialCopyEventItem.statusText, "已完成");
  assert.equal(context.data.bookAfterwordInput, "新的后记。");
  assert.equal(toasts[0].title, "后记已更新");
  page.clearBookEditorialCopyPoll.call(context);
});

test("book export page downloads selected PDF layout and source appendix setting", async () => {
  const exportCalls = [];
  const opened = [];
  const toasts = [];
  global.wx = {
    openDocument(options) {
      opened.push(options);
      options.success();
    },
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadBookExportPage({
    downloadMemoirBookOutputExport: async (projectId, format, versionId, layout, showSourceAppendix) => {
      exportCalls.push({ projectId, format, versionId, layout, showSourceAppendix });
      return "/tmp/book.pdf";
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());
  await page.changeBookOutputVersion.call(
    context,
    { currentTarget: { dataset: {} }, detail: { value: "1" } },
  );
  page.toggleBookExportSourceAppendix.call(context, {
    currentTarget: { dataset: {} },
    detail: { value: false },
  });

  await page.downloadBookOutputExport.call(context, {
    currentTarget: { dataset: { format: "pdf", layout: "large_print" } },
  });

  assert.deepEqual(exportCalls[0], {
    projectId: "project/1",
    format: "pdf",
    versionId: "book-output-version-1",
    layout: "large_print",
    showSourceAppendix: false,
  });
  assert.equal(opened[0].filePath, "/tmp/book.pdf");
  assert.equal(opened[0].fileType, "pdf");
  assert.equal(toasts[0].title, "PDF 已打开");
  assert.equal(context.data.exportActionHintText, "适合拿去打印装订，页边距更宽。");
  assert.equal(context.data.bookExportBusyTarget, "");
});

test("book export page downloads reading PDF with the standard layout", async () => {
  const exportCalls = [];
  const opened = [];
  const toasts = [];
  global.wx = {
    openDocument(options) {
      opened.push(options);
      options.success();
    },
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadBookExportPage({
    downloadMemoirBookOutputExport: async (projectId, format, versionId, layout, showSourceAppendix) => {
      exportCalls.push({ projectId, format, versionId, layout, showSourceAppendix });
      return "/tmp/reading.pdf";
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
  });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  await page.downloadBookOutputExport.call(context, {
    currentTarget: { dataset: { format: "pdf", layout: "standard" } },
  });

  assert.equal(exportCalls[0].layout, "standard");
  assert.equal(exportCalls[0].showSourceAppendix, true);
  assert.equal(opened[0].filePath, "/tmp/reading.pdf");
  assert.equal(toasts[0].title, "PDF 已打开");
  assert.equal(context.data.exportActionHintText, "适合微信分享和手机阅读。");
});

test("book export page assembles confirmed chapter drafts as a new full-book version", async () => {
  const requests = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadBookExportPage({
    assembleMemoirBookOutput: async (projectId) => {
      requests.push({ kind: "assemble", projectId });
    },
    updateMemoirBookOutput: async (projectId, payload) => {
      requests.push({ kind: "update", projectId, payload });
      return sampleBookOutput();
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  await page.generateBook.call(context);

  assert.deepEqual(requests[0], { kind: "assemble", projectId: "project/1" });
  assert.deepEqual(requests[1], {
    kind: "update",
    projectId: "project/1",
    payload: {
      title: "外婆回忆录",
      subtitle: "根据口述访谈整理",
      narrator_name: "外婆",
      prepared_by: "家人整理",
      preface: "这些故事来自家里的讲述。",
      afterword: "这些故事还可以继续补充。",
      back_cover_quote: "我小时候常在院子里晒太阳。",
      cover_style: "classic_plain",
    },
  });
  assert.equal(context.data.generatingBook, false);
  assert.equal(context.data.exportNotice, "已根据各章确认正文更新全书稿。");
  assert.equal(toasts[0].title, "书稿已更新");
});


test("plain text output without a manuscript does not invent book structure", async () => {
  const output = sampleBookOutput();
  output.latest_version.content = "已有的纯文字正文";
  output.latest_version.manuscript = null;
  const { bookOutputData } = require("../pages/book/book-view-model.ts");
  const data = bookOutputData(output);
  assert.equal(data.bookContent, "已有的纯文字正文");
  assert.equal(data.bookManuscriptAvailable, false);
  assert.deepEqual(data.bookManuscriptStructureItems, []);
  assert.deepEqual(data.bookManuscriptChapterItems, []);
  assert.deepEqual(data.bookPreviewChapters, []);
});
