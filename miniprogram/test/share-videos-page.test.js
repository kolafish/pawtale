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

function video(id, utteranceId) {
  return {
    id,
    utterance_id: utteranceId,
    title: `视频 ${id}`,
    selected_text: "小时候在院子里看月亮。",
    duration_ms: 30000,
    created_at: "2026-09-24T08:00:00",
    completed_at: "2026-09-24T08:01:00",
  };
}

function loadLibraryPage(listMemoirShareVideos) {
  mockModule("../services/auth.ts", {
    getMemoirIdentity: unmockedAsync("getMemoirIdentity"),
  });
  mockMemoirApiModules({ listMemoirShareVideos });
  return loadPageModule("../pages/share-videos/index.ts");
}

test("video library lists metadata in pages and opens only the chosen video", async () => {
  const calls = [];
  const navigations = [];
  const { page } = loadLibraryPage(async (...args) => {
    calls.push(args);
    const offset = args[1].offset;
    return {
      items: offset ? [video("video-1", "utterance-1")] : [video("video-2", "utterance-2")],
      total: 2,
      limit: 20,
      offset,
    };
  });
  global.wx = { navigateTo({ url }) { navigations.push(url); } };
  const context = pageContext(page);
  context.onLoad({ projectId: "project-1" });

  await context.loadVideos(true);
  assert.deepEqual(context.data.items.map((item) => item.id), ["video-2"]);
  assert.equal(context.data.hasMore, true);
  await context.loadVideos(false);
  assert.deepEqual(context.data.items.map((item) => item.id), ["video-2", "video-1"]);
  assert.equal(context.data.hasMore, false);
  assert.deepEqual(calls, [
    ["project-1", { limit: 20, offset: 0 }],
    ["project-1", { limit: 20, offset: 1 }],
  ]);

  context.openVideo({ currentTarget: { dataset: { id: "video-1", utteranceId: "utterance-1" } } });
  assert.deepEqual(navigations, ["/pages/transcript-media/index?id=utterance-1&jobId=video-1"]);
});

test("video library ignores a response after leaving the page", async () => {
  let resolve;
  const pending = new Promise((done) => { resolve = done; });
  const { page } = loadLibraryPage(() => pending);
  const context = pageContext(page);
  context.onLoad({ projectId: "project-1" });

  const loading = context.loadVideos(true);
  context.onHide();
  resolve({ items: [video("video-old", "utterance-old")], total: 1, limit: 20, offset: 0 });
  await loading;
  assert.deepEqual(context.data.items, []);
});

test("video library route is registered", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  assert.ok(appJson.pages.includes("pages/share-videos/index"));
});
