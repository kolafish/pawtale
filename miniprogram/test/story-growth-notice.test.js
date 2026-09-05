require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { mockModule, clearModule, flushAsyncWork } = require("./page-harness");
const { PollingTimer } = require("../services/polling-timer.ts");

function event(id, patch = {}) {
  return { id, event_type: "chapter_draft_generated", status: "completed", summary_text: `正文 ${id} 已更新`, metadata_json: {}, created_at: "2026-09-05T08:51:31", ...patch };
}
function setup(loadEvents) {
  mockModule("../services/memoir-voice-session-api.ts", { listMemoirAutomationEvents: loadEvents });
  clearModule("../services/story-growth-notice.ts");
  const exports = require("../services/story-growth-notice.ts");
  let next = null;
  let id = 0;
  const timer = new PollingTimer(1, {
    schedule(callback) { next = callback; return ++id; },
    cancel() { next = null; },
  });
  const watcher = new exports.StoryProgressWatcher(timer);
  return { ...exports, watcher, timer, async tick() { const callback = next; next = null; callback?.(); await flushAsyncWork(); } };
}

test("significant progress includes chapter and book drafts and excludes acknowledged or unfinished work", () => {
  const { storyProgressEvents, syncStoryTabRedDot } = setup(async () => ({ items: [] }));
  const events = [event("seen", { metadata_json: { acknowledged_at: "2026-09-05T08:50:49Z" } }),
    event("pending", { status: "pending" }), event("failed", { status: "failed" }),
    event("draft"), event("book", { event_type: "book_output_generated", created_at: "2026-09-05T08:52:00" })];
  assert.deepEqual(storyProgressEvents(events).map((item) => item.id), ["book", "draft"]);
  let redDot = false;
  global.wx = { showTabBarRedDot() { redDot = true; }, hideTabBarRedDot() { redDot = false; } };
  syncStoryTabRedDot(events);
  assert.equal(redDot, true);
  syncStoryTabRedDot([events[0]]);
  assert.equal(redDot, false);
});

test("watcher receives later progress after an empty poll without finishing narration", async () => {
  let items = [];
  const seen = [];
  const { watcher, timer, tick } = setup(async () => ({ items }));
  watcher.start("project-1", (events) => seen.push(events.map((item) => item.id)));
  await flushAsyncWork();
  assert.deepEqual(seen, [[]]);
  assert.equal(timer.pending, true);
  items = [event("new-chapter")];
  await tick();
  assert.deepEqual(seen, [[], ["new-chapter"]]);
  assert.equal(timer.pending, true);
  watcher.stop();
  assert.equal(timer.pending, false);
});

test("watcher ignores hidden-page and previous-project responses and starts fresh on return", async () => {
  const requests = [];
  const seen = [];
  const { watcher, timer } = setup((projectId) => new Promise((resolve) => requests.push({ projectId, resolve })));
  watcher.start("project-1", (events) => seen.push(events[0]?.id));
  watcher.stop();
  requests[0].resolve({ items: [event("hidden")] });
  await flushAsyncWork();
  assert.deepEqual(seen, []);
  assert.equal(timer.pending, false);
  watcher.start("project-1", (events) => seen.push(events[0]?.id));
  watcher.start("project-2", (events) => seen.push(events[0]?.id));
  requests[1].resolve({ items: [event("stale")] });
  requests[2].resolve({ items: [event("current")] });
  await flushAsyncWork();
  assert.deepEqual(seen, ["current"]);
  assert.equal(watcher.projectId, "project-2");
  watcher.stop();
});
