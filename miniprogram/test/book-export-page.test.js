require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

const {
  sampleBookManuscript: baseSampleBookManuscript,
  sampleBookOutput: baseSampleBookOutput,
  sampleBookOutputVersion: baseSampleBookOutputVersion,
  samplePlan: baseSamplePlan,
  sampleStructuredBookContent,
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
    sampleStructuredBookContent({ chapterCount: 1 }),
    createdAt,
    manuscript,
  );
}

function sampleBookOutput() {
  const latestVersion = sampleBookOutputVersion(2, "全书第二版正文");
  return {
    ...baseSampleBookOutput(2, "全书第二版正文", false, [], latestVersion.structured_content, [
      sampleBookOutputVersion(1, "全书第一版正文", "2026-06-16T00:00:00.000Z"),
      latestVersion,
    ], latestVersion.manuscript),
    latest_version: latestVersion,
  };
}

function sampleBookReadiness(overrides = {}) {
  return {
    project_id: "project/1",
    ready_for_formal: false,
    current_signature: "signature-1",
    output_status: "draft",
    output_version_id: "book-output-version-2",
    latest_audit_job: null,
    audit_is_current: false,
    groups: {
      must_handle: [
        {
          code: "book_consistency_audit_required",
          title: "需要运行全书一致性检查",
          detail: "",
        },
      ],
      suggested: [],
      accepted_incomplete: [],
    },
    ...overrides,
  };
}

function loadBookExportPage(voiceApiExports = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirBookPlan: async () => samplePlan(),
    getMemoirBookOutput: async () => sampleBookOutput(),
    getMemoirBookReadiness: async () => sampleBookReadiness(),
    enqueueMemoirBookConsistencyAudit: async () => ({ id: "audit-job-1", status: "pending" }),
    getMemoirBookConsistencyAudit: async () => ({ id: "audit-job-1", status: "succeeded" }),
    formalizeMemoirBookOutput: async () => ({ status: "accepted" }),
    downloadMemoirBookOutputExport: async () => "/tmp/book.pdf",
    suggestMemoirBookOutputTitles: async () => ({ suggestions: [] }),
    updateMemoirBookOutput: async () => sampleBookOutput(),
    assembleMemoirBookDraftBlocks: async () => ({ version: 1 }),
    enqueueMemoirBookOutputEditorialCopyGeneration: async () => ({ id: "event-1" }),
    ...voiceApiExports,
  });
  return loadPageModule("../pages/book-export/index.ts");
}

test("book export page is registered", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  const pageJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../pages/book-export/index.json"), "utf8"));

  assert.ok(appJson.pages.includes("pages/book-export/index"));
  assert.equal(pageJson.navigationBarTitleText, "预览与导出");
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
  assert.equal(context.data.bookManuscriptAvailable, true);
  assert.equal(context.data.bookManuscriptTemplateLabel, "素雅书籍版");
  assert.equal(context.data.bookManuscriptStructureItems.some((item) => item.title === "目录"), true);
  assert.equal(context.data.bookManuscriptChapterItems[0].intro, "这一章主要记录外婆小时候在院子里的生活。");
  assert.equal(context.data.bookManuscriptBackMatterItems.some((item) => item.title === "原始讲述索引"), true);
  assert.equal(context.data.bookConsistencyStatusText, "1 条必须处理");
});

test("book export page runs consistency audit in background and then enables formalization", async () => {
  const calls = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const ready = sampleBookReadiness({
    ready_for_formal: true,
    audit_is_current: true,
    latest_audit_job: { id: "audit-job-1", status: "succeeded" },
    groups: { must_handle: [], suggested: [], accepted_incomplete: [] },
  });
  const { page } = loadBookExportPage({
    enqueueMemoirBookConsistencyAudit: async (projectId, payload) => {
      calls.push({ kind: "enqueue", projectId, payload });
      return { id: "audit-job-1", status: "pending" };
    },
    getMemoirBookConsistencyAudit: async (projectId, jobId) => {
      calls.push({ kind: "poll", projectId, jobId });
      return { id: jobId, status: "succeeded", error_message: "" };
    },
    getMemoirBookReadiness: async () => ready,
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());
  page.applyBookReadiness.call(context, sampleBookReadiness(), sampleBookOutput());

  await page.startBookConsistencyAudit.call(context);
  page.clearBookConsistencyPoll.call(context);
  assert.equal(context.data.bookConsistencyAuditRunning, true);
  assert.equal(context.data.bookConsistencyStatusText, "检查中");
  await page.pollBookConsistencyAudit.call(context);

  assert.deepEqual(calls, [
    {
      kind: "enqueue",
      projectId: "project/1",
      payload: { include_semantic_audit: true },
    },
    { kind: "poll", projectId: "project/1", jobId: "audit-job-1" },
  ]);
  assert.equal(context.data.bookConsistencyAuditRunning, false);
  assert.equal(context.data.bookConsistencyCanFormalize, true);
  assert.equal(toasts[0].title, "全书审校已完成");
  delete global.wx;
});

test("book export page resumes polling an unfinished consistency audit after reopening", async () => {
  const pending = sampleBookReadiness({
    latest_audit_job: { id: "audit-job-pending", status: "running" },
  });
  const { page } = loadBookExportPage({
    getMemoirBookReadiness: async () => pending,
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project/1" });
  page.clearBookConsistencyPoll.call(context);

  assert.equal(context.data.bookConsistencyAuditRunning, true);
  assert.equal(context.data.bookConsistencyJobId, "audit-job-pending");
  assert.equal(context.data.bookConsistencyStatusText, "检查中");
});

test("book consistency failure stays scoped and leaves the export page usable", async () => {
  const { page } = loadBookExportPage({
    enqueueMemoirBookConsistencyAudit: async () => ({
      id: "audit-job-failed",
      status: "pending",
    }),
    getMemoirBookConsistencyAudit: async () => ({
      id: "audit-job-failed",
      status: "failed",
      error_message: "语义审校服务暂时不可用",
    }),
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());
  page.applyBookReadiness.call(context, sampleBookReadiness(), sampleBookOutput());

  await page.startBookConsistencyAudit.call(context);
  page.clearBookConsistencyPoll.call(context);
  await page.pollBookConsistencyAudit.call(context);

  assert.equal(context.data.error, "");
  assert.equal(context.data.bookConsistencyError, "语义审校服务暂时不可用");
  assert.equal(context.data.bookConsistencyAuditRunning, false);
  assert.equal(context.data.bookContent, "全书第二版正文");
});

test("book export page formalizes only the audited current output version", async () => {
  const requests = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const ready = sampleBookReadiness({
    ready_for_formal: true,
    audit_is_current: true,
    latest_audit_job: { id: "audit-job-1", status: "succeeded" },
    groups: { must_handle: [], suggested: [], accepted_incomplete: [] },
  });
  const acceptedOutput = { ...sampleBookOutput(), status: "accepted" };
  const { page } = loadBookExportPage({
    getMemoirBookReadiness: async () => ready,
    formalizeMemoirBookOutput: async (projectId, payload) => {
      requests.push({ projectId, payload });
      return { status: "accepted" };
    },
    getMemoirBookOutput: async () => acceptedOutput,
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());
  page.applyBookReadiness.call(context, ready, sampleBookOutput());

  await page.formalizeBookOutput.call(context);

  assert.deepEqual(requests, [{
    projectId: "project/1",
    payload: {
      audit_job_id: "audit-job-1",
      output_version_id: "book-output-version-2",
    },
  }]);
  assert.equal(context.data.bookOutput.status, "accepted");
  assert.equal(context.data.bookConsistencyStatusText, "正式书稿");
  assert.equal(toasts[0].title, "已标记为正式书稿");
  delete global.wx;
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
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadBookExportPage({
    suggestMemoirBookOutputTitles: async (projectId, payload) => {
      titleRequests.push({ projectId, payload });
      return { suggestions: [" 外婆的月光 ", "庭院往事"] };
    },
    enqueueMemoirBookOutputEditorialCopyGeneration: async (projectId, payload) => {
      editorialRequests.push({ projectId, payload });
      return { id: "event-1" };
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
  assert.equal(toasts[0].title, "已后台生成");
  assert.equal(context.data.bookEditorialCopyBusyTarget, "");
  delete global.wx;
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
  page.changeBookOutputVersion.call(context, { currentTarget: { dataset: {} }, detail: { value: "1" } });
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
  delete global.wx;
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
    bookExportLayout: "large_print",
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
  delete global.wx;
});

test("book export page assembles a new version from confirmed chapter blocks", async () => {
  const requests = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadBookExportPage({
    assembleMemoirBookDraftBlocks: async (projectId) => {
      requests.push({ projectId });
      return { version: 3 };
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  await page.generateBook.call(context);

  assert.deepEqual(requests[0], { projectId: "project/1" });
  assert.equal(context.data.generatingBook, false);
  assert.equal(context.data.exportNotice, "已按确认的结构化章节组装最新版，历史版本保持不变。");
  assert.equal(toasts[0].title, "全书已组装");
  delete global.wx;
});
