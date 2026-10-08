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
  flushAsyncWork,
  unmockedAsync,
} = require("./page-harness");

function video(id, utteranceId) {
  return {
    id,
    utterance_id: utteranceId,
    title: `视频 ${id}`,
    selected_text: "小时候在院子里看月亮。",
    duration_ms: 30000,
    video_style: id === "video-2" ? "uploaded" : "illustrated",
    created_at: "2026-09-24T08:00:00",
    completed_at: "2026-09-24T08:01:00",
  };
}

function loadLibraryPage(
  listMemoirShareVideos,
  getMemoirShareVideoJob = unmockedAsync("getMemoirShareVideoJob"),
  deleteMemoirShareVideoJob = unmockedAsync("deleteMemoirShareVideoJob"),
) {
  mockModule("../services/auth.ts", {
    getMemoirIdentity: unmockedAsync("getMemoirIdentity"),
  });
  mockMemoirApiModules({ listMemoirShareVideos, getMemoirShareVideoJob, deleteMemoirShareVideoJob });
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
      active_jobs: [],
      failed_jobs: [],
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
  assert.deepEqual(context.data.items.map((item) => item.styleLabel), ["自选画面", "回忆画卷"]);
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
  resolve({ items: [video("video-old", "utterance-old")], active_jobs: [], failed_jobs: [], total: 1, limit: 20, offset: 0 });
  await loading;
  assert.deepEqual(context.data.items, []);
});

test("video library route is registered", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  assert.ok(appJson.pages.includes("pages/share-videos/index"));
});

function job(status, patch = {}) {
  return {
    ...video("working", "utterance-working"),
    status,
    background_status: "generating",
    background_source: "ai",
    output: status === "succeeded" ? { download_url: "/completed.mp4", mime_type: "video/mp4", size_bytes: 1024 } : null,
    error_message: "",
    ...patch,
  };
}

function libraryWithPolling(page) {
  const { PollingTimer } = require("../services/polling-timer.ts");
  const context = pageContext(page);
  context.onLoad({ projectId: "project-1" });
  let callback = null;
  context.progressPoller = new PollingTimer(5000, {
    schedule(next) { callback = next; return 1; },
    cancel() { callback = null; },
  });
  return {
    context,
    hasPoll: () => Boolean(callback),
    async tick() {
      assert.ok(callback, "a visible active task should keep polling");
      const next = callback;
      callback = null;
      next();
      await flushAsyncWork();
    },
  };
}

test("video library restores queued and running tasks and opens their existing progress page", async () => {
  const navigations = [];
  const { page } = loadLibraryPage(async () => ({
    items: [], active_jobs: [job("pending"), job("running", { id: "rendering" })], failed_jobs: [],
    total: 0, limit: 20, offset: 0,
  }));
  global.wx = { navigateTo({ url }) { navigations.push(url); } };
  const { context, hasPoll } = libraryWithPolling(page);
  context.onShow();
  await flushAsyncWork();
  assert.deepEqual(context.data.taskItems.map((item) => item.status), ["pending", "running"]);
  assert.notEqual(context.data.taskItems[0].statusText, context.data.taskItems[1].statusText);
  assert.equal(hasPoll(), true);
  context.openVideo({ currentTarget: { dataset: { id: "working", utteranceId: "utterance-working" } } });
  assert.deepEqual(navigations, ["/pages/transcript-media/index?id=utterance-working&jobId=working"]);
  context.onUnload();
  assert.equal(hasPoll(), false);
});

test("video progress updates stages and moves completion into the loaded library without losing older pages", async () => {
  let polls = 0;
  const { page } = loadLibraryPage(async (_projectId, { limit, offset }) => ({
    items: [video(offset ? "video-1" : "video-2", "utterance-old")],
    active_jobs: [job("running")], failed_jobs: [], total: limit === 1 ? 3 : 2, limit, offset,
  }), async () => ++polls === 1
    ? job("running", { background_status: "ready" }) : job("succeeded"));
  const polling = libraryWithPolling(page);
  const { context } = polling;
  context.onShow();
  await flushAsyncWork();
  const initialStage = context.data.taskItems[0].statusText;
  await context.loadVideos(false);
  await polling.tick();
  assert.notEqual(context.data.taskItems[0].statusText, initialStage);
  await polling.tick();
  assert.deepEqual(context.data.items.map((item) => item.id), ["working", "video-2", "video-1"]);
  assert.equal(context.data.total, 3);
  assert.equal(context.data.hasMore, false);
  assert.deepEqual(context.data.taskItems, []);
  assert.equal(polling.hasPoll(), false);
  assert.equal(polls, 2);
});

test("video progress leaves a failed task visible with its real error and stops polling", async () => {
  const { page } = loadLibraryPage(async () => ({
    items: [], active_jobs: [job("running")], failed_jobs: [], total: 0, limit: 20, offset: 0,
  }), async () => job("failed", { error_message: "配图服务暂时不可用" }));
  const polling = libraryWithPolling(page);
  polling.context.onShow();
  await flushAsyncWork();
  await polling.tick();
  assert.equal(polling.context.data.taskItems[0].status, "failed");
  assert.equal(polling.context.data.taskItems[0].statusText, "配图服务暂时不可用");
  assert.equal(polling.hasPoll(), false);
});

test("a failed attempt can be deleted from the library after confirming", async () => {
  const deleted = [];
  const { page } = loadLibraryPage(async () => ({
    items: [], active_jobs: [], failed_jobs: [job("failed", { id: "broken" })], total: 0, limit: 20, offset: 0,
  }), undefined, async (id) => { deleted.push(id); });
  const context = pageContext(page);
  context.onLoad({ projectId: "project-1" });
  await context.loadVideos(true);
  let answer = false;
  global.wx = { showModal: ({ success }) => success({ confirm: answer }) };
  try {
    await context.deleteFailedVideo({ currentTarget: { dataset: { id: "broken" } } });
    assert.deepEqual(deleted, []);
    answer = true;
    await context.deleteFailedVideo({ currentTarget: { dataset: { id: "broken" } } });
  } finally {
    delete global.wx;
  }
  assert.deepEqual(deleted, ["broken"]);
  assert.deepEqual(context.data.taskItems, []);
});

test("reopening the library after starting another video restores the earlier failure and polls only active tasks", async () => {
  const first = job("running", { id: "first-video", utterance_id: "first-story" });
  const second = job("pending", { id: "second-video", utterance_id: "second-story" });
  const failure = { ...first, status: "failed", error_message: "AI 额度已用尽，请添加微信联系管理员" };
  const response = { items: [], active_jobs: [first], failed_jobs: [], total: 0, limit: 20, offset: 0 };
  const polled = [];
  const navigations = [];
  const { page } = loadLibraryPage(async () => response, async (id) => {
    polled.push(id);
    return { ...second, status: "running" };
  });
  const initial = libraryWithPolling(page);
  initial.context.onShow();
  await flushAsyncWork();
  initial.context.onUnload();

  response.active_jobs = [second];
  response.failed_jobs = [failure];
  const reopened = libraryWithPolling(page);
  reopened.context.onShow();
  await flushAsyncWork();
  assert.deepEqual(reopened.context.data.taskItems.map(({ id, status }) => ({ id, status })), [
    { id: "second-video", status: "pending" }, { id: "first-video", status: "failed" },
  ]);
  assert.equal(reopened.context.data.taskItems[1].statusText, failure.error_message);
  await reopened.tick();
  assert.deepEqual(polled, ["second-video"]);
  assert.equal(reopened.context.data.taskItems[1].status, "failed");

  response.active_jobs = [];
  response.failed_jobs = [{ ...second, status: "failed", error_message: failure.error_message }, failure];
  reopened.context.onHide();
  reopened.context.onShow();
  await flushAsyncWork();
  assert.deepEqual(reopened.context.data.taskItems.map((item) => item.id), ["second-video", "first-video"]);
  assert.equal(reopened.hasPoll(), false);
  assert.equal(reopened.context.data.total, 0);
  global.wx = { navigateTo({ url }) { navigations.push(url); } };
  reopened.context.openVideo({ currentTarget: { dataset: { id: failure.id, utteranceId: failure.utterance_id } } });
  assert.deepEqual(navigations, ["/pages/transcript-media/index?id=first-story&jobId=first-video"]);
  reopened.context.onUnload();
});

test("pagination during completion keeps the task until its final status and does not duplicate videos or counts", async () => {
  let resolve;
  let polls = 0;
  const pending = new Promise((done) => { resolve = done; });
  const { page } = loadLibraryPage(async (_projectId, { limit, offset }) => ({
    items: limit === 1 ? [video("working", "utterance-working")]
      : offset ? [video("video-2", "utterance-old"), video("video-1", "utterance-old")]
        : [video("video-2", "utterance-old")],
    active_jobs: !offset && limit !== 1 ? [job("running")] : [],
    failed_jobs: [],
    total: offset || limit === 1 ? 3 : 2, limit, offset,
  }), () => ++polls === 1 ? pending : Promise.resolve(job("succeeded")));
  const polling = libraryWithPolling(page);
  const { context } = polling;
  context.onShow();
  await flushAsyncWork();
  const refreshing = context.refreshVideoProgress();
  await context.loadVideos(false);
  resolve(job("succeeded"));
  await refreshing;
  assert.deepEqual(context.data.items.map((item) => item.id), ["video-2", "video-1"]);
  assert.equal(context.data.taskItems[0].id, "working");
  await polling.tick();
  assert.deepEqual(context.data.items.map((item) => item.id), ["working", "video-2", "video-1"]);
  assert.equal(context.data.total, 3);
  assert.equal(context.data.hasMore, false);
  assert.deepEqual(context.data.taskItems, []);
  assert.equal(polling.hasPoll(), false);
});

test("video progress cancels polling and ignores completion after hiding, then recovers on return", async () => {
  let resolve;
  const pending = new Promise((done) => { resolve = done; });
  const { page } = loadLibraryPage(async () => ({
    items: [], active_jobs: [job("running")], failed_jobs: [], total: 0, limit: 20, offset: 0,
  }), () => pending);
  const polling = libraryWithPolling(page);
  const { context } = polling;
  context.onShow();
  await flushAsyncWork();
  const refreshing = context.refreshVideoProgress();
  context.onHide();
  resolve(job("succeeded"));
  await refreshing;
  assert.deepEqual(context.data.items, []);
  assert.equal(context.data.taskItems[0].status, "running");
  assert.equal(polling.hasPoll(), false);
  context.onShow();
  await flushAsyncWork();
  assert.equal(polling.hasPoll(), true);
  context.onUnload();
});

test("video progress exposes refresh failure and resumes only after an explicit reload", async () => {
  const { page } = loadLibraryPage(async () => ({
    items: [], active_jobs: [job("running")], failed_jobs: [], total: 0, limit: 20, offset: 0,
  }), async () => { throw new Error("连接暂时中断"); });
  const polling = libraryWithPolling(page);
  polling.context.onShow();
  await flushAsyncWork();
  await polling.tick();
  assert.equal(polling.context.data.error, "连接暂时中断");
  assert.equal(polling.context.data.taskItems[0].status, "running");
  assert.equal(polling.hasPoll(), false);
  await polling.context.loadVideos(true);
  assert.equal(polling.hasPoll(), true);
  polling.context.onUnload();
});
