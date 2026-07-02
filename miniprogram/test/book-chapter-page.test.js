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

function samplePlan() {
  return {
    id: "plan-1",
    project_id: "project/1",
    title: "外婆回忆录章节规划",
    status: "confirmed",
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
      {
        id: "chapter-2",
        plan_id: "plan-1",
        project_id: "project/1",
        title: "第一次离家",
        sort_order: 1,
        source_refs: [sampleRef("utterance-2", "第一次离家")],
        metadata_json: {},
        pending_source_count: 0,
        removed_source_count: 0,
        created_at: "2026-06-17T00:00:00.000Z",
        updated_at: "2026-06-17T00:00:00.000Z",
      },
    ],
  };
}

function sampleDraft() {
  return {
    id: "draft-1",
    project_id: "project/1",
    chapter_id: "chapter-1",
    status: "confirmed",
    metadata_json: {},
    pending_source_count: 0,
    removed_source_count: 0,
    latest_version: {
      id: "draft-version-1",
      draft_id: "draft-1",
      project_id: "project/1",
      chapter_id: "chapter-1",
      version: 1,
      content: "我小时候常在外婆家的院子里晒太阳。",
      created_by: "ai",
      model: "fake",
      prompt_snapshot: "prompt",
      feedback: null,
      created_at: "2026-06-17T00:00:00.000Z",
    },
    versions: [],
    created_at: "2026-06-17T00:00:00.000Z",
    updated_at: "2026-06-17T00:00:00.000Z",
  };
}

function sampleSourceCoverage() {
  return {
    project_id: "project/1",
    plan_id: "plan-1",
    total_source_count: 2,
    covered_source_count: 2,
    uncovered_source_count: 0,
    sources: [
      {
        source_index: 1,
        voice_session_id: "voice-1",
        message_id: "message-utterance-1",
        utterance_id: "utterance-1",
        transcript_version_id: "version-utterance-1",
        audio_file_id: "audio-utterance-1",
        start_ms: 0,
        end_ms: 8000,
        text: "外婆家的院子",
        created_at: "2026-06-17T00:00:00.000Z",
        is_included: true,
        included_in_chapters: [{ chapter_id: "chapter-1", title: "外婆家的童年", sort_order: 0 }],
      },
      {
        source_index: 2,
        voice_session_id: "voice-1",
        message_id: "message-utterance-2",
        utterance_id: "utterance-2",
        transcript_version_id: "version-utterance-2",
        audio_file_id: "audio-utterance-2",
        start_ms: 0,
        end_ms: 8000,
        text: "第一次离家",
        created_at: "2026-06-17T00:00:00.000Z",
        is_included: true,
        included_in_chapters: [{ chapter_id: "chapter-2", title: "第一次离家", sort_order: 1 }],
      },
    ],
    uncovered_sources: [],
  };
}

function loadBookChapterPage({
  plan = samplePlan(),
  draft = sampleDraft(),
  coverage = sampleSourceCoverage(),
  voiceApiExports = {},
} = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirBookPlan: async () => plan,
    getMemoirBookChapterDraft: async () => draft,
    getMemoirBookSourceCoverage: async () => coverage,
    ...voiceApiExports,
  });
  return loadPageModule("../pages/book-chapter/index.ts");
}

test("book chapter page is registered and renders the detail tabs", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/book-chapter/index.wxml"), "utf8");
  const wxss = fs.readFileSync(path.join(__dirname, "../pages/book-chapter/index.wxss"), "utf8");

  assert.ok(appJson.pages.includes("pages/book-chapter/index"));
  assert.match(wxml, /章节详情/);
  assert.match(wxml, /data-tab="content"/);
  assert.match(wxml, /data-tab="sources"/);
  assert.match(wxml, /data-tab="more"/);
  assert.match(wxss, /\.tab-row/);
  assert.match(wxss, /\.source-row/);
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
  assert.equal(context.data.moreActions[0].title, "历史版本");
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
