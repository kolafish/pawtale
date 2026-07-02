require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

function sampleRef(id = "utterance-1", quote = "外婆家的院子") {
  return {
    voice_session_id: "voice-1",
    message_id: `message-${id}`,
    utterance_id: id,
    transcript_version_id: `version-${id}`,
    audio_file_id: `audio-${id}`,
    start_ms: 0,
    end_ms: 8000,
    created_at: "2026-06-17T00:00:00.000Z",
    quote,
  };
}

function samplePlan(status = "confirmed") {
  return {
    id: "plan-1",
    project_id: "project/1",
    title: "外婆回忆录章节规划",
    status,
    created_by: "ai",
    model: "fake",
    metadata_json: {},
    created_at: "2026-06-17T00:00:00.000Z",
    updated_at: "2026-06-17T00:00:00.000Z",
    chapters: [
      {
        id: "chapter-1",
        plan_id: "plan-1",
        project_id: "project/1",
        title: "外婆家的童年",
        sort_order: 0,
        source_refs: [sampleRef("utterance-1", "外婆家的院子")],
        metadata_json: {},
        pending_source_count: 0,
        removed_source_count: 0,
        created_at: "2026-06-17T00:00:00.000Z",
        updated_at: "2026-06-17T00:00:00.000Z",
      },
    ],
  };
}

function sampleStructuredBookContent() {
  return {
    title: "外婆回忆录",
    chapter_count: 1,
    char_count: 14,
    table_of_contents: [
      {
        chapter_id: "chapter-1",
        index: 1,
        title: "外婆家的童年",
        char_count: 14,
        source_count: 1,
      },
    ],
    chapters: [
      {
        chapter_id: "chapter-1",
        index: 1,
        title: "外婆家的童年",
        content: "我小时候常在院子里晒太阳。",
        char_count: 14,
        source_count: 1,
        draft_version_id: "draft-version-1",
        draft_version: 1,
      },
    ],
  };
}

function sampleBookOutputVersion(version, content, createdAt = "2026-06-17T00:00:00.000Z") {
  return {
    id: `book-output-version-${version}`,
    output_id: "book-output-1",
    project_id: "project/1",
    plan_id: "plan-1",
    version,
    content,
    chapter_snapshot_json: [],
    structured_content: sampleStructuredBookContent(),
    manuscript: null,
    created_by: "system",
    created_at: createdAt,
  };
}

function sampleBookOutput() {
  const latestVersion = sampleBookOutputVersion(2, "全书第二版正文");
  return {
    id: "book-output-1",
    project_id: "project/1",
    plan_id: "plan-1",
    title: "外婆回忆录",
    status: "draft",
    metadata_json: {},
    is_stale: false,
    stale_chapter_titles: [],
    latest_version: latestVersion,
    versions: [
      sampleBookOutputVersion(1, "全书第一版正文", "2026-06-16T00:00:00.000Z"),
      latestVersion,
    ],
    created_at: "2026-06-17T00:00:00.000Z",
    updated_at: "2026-06-17T00:00:00.000Z",
  };
}

function loadBookExportPage(voiceApiExports = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirBookPlan: async () => samplePlan(),
    getMemoirBookOutput: async () => sampleBookOutput(),
    downloadMemoirBookOutputExport: async () => "/tmp/book.pdf",
    ...voiceApiExports,
  });
  return loadPageModule("../pages/book-export/index.ts");
}

test("book export page is registered and exposes preview/export controls", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/book-export/index.wxml"), "utf8");
  const wxss = fs.readFileSync(path.join(__dirname, "../pages/book-export/index.wxss"), "utf8");

  assert.ok(appJson.pages.includes("pages/book-export/index"));
  assert.match(wxml, /全书导出/);
  assert.match(wxml, /阅读版 PDF/);
  assert.match(wxml, /打印版 PDF/);
  assert.match(wxml, /导出文本/);
  assert.match(wxml, /bindchange="changeBookExportLayout"/);
  assert.match(wxml, /bindchange="toggleBookExportSourceAppendix"/);
  assert.match(wxml, /历史版本/);
  assert.match(wxss, /\.export-actions/);
  assert.match(wxss, /\.preview-card/);
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
  assert.equal(context.data.bookContent, "全书第二版正文");
  assert.equal(context.data.bookPreviewChapters.length, 1);
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
  assert.equal(context.data.bookExportBusyTarget, "");
  delete global.wx;
});
