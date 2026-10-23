const test = require("node:test");
const assert = require("node:assert/strict");
require("./register-ts");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

function fixture(cached, userState) {
  const snapshot = {
    server_revision: 1,
    identity: { id: "review-user", email: "review@example.invalid", display_name: "作者",
      active_project_id: "project-1", active_project: { id: "project-1", title: "回忆录" } },
    latest_transcript: null, chapters: [], growth_events: [], progress: null,
    automation_events: { items: [], total: 0, limit: 8 }, book_issues: { review_count: 0, supplement_count: 0 },
    book_readable: false, book_char_count: 0, book_chapter_count: 0, book_manual_edit_preserved: false,
    unassigned_image_count: 0, unassigned_video_count: 0,
  };
  const storage = new Map();
  if (userState) storage.set("pawtale_home_launch_state", { version: 1, users: { "review-user": userState } });
  global.wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    pageScrollTo() {},
    createSelectorQuery: () => ({ select: () => ({ boundingClientRect: cb => ({ exec: () => cb({ bottom: 600 }) }) }) }),
  };
  mockModule("../stores/memoir-cache.ts", { writeMemoirIdentitySnapshot() {} });
  mockModule("../stores/memoir-read-cache.ts", {
    readCachedMemoirHome: () => ({ status: "miss" }),
    readCachedMemoirIdentity: () => cached ? snapshot.identity : null,
    writeCachedMemoirHome() {},
  });
  mockModule("../services/memoir-snapshot-api.ts", { getMemoirStorySnapshot: async () => snapshot });
  mockModule("../services/voice-authorization.ts", { readVoiceAuthorizationConsent: () => true });
  mockModule("../services/accessibility-settings.ts", { readLargeTextEnabled: () => false });
  mockModule("../services/navigation.ts", { consumeHomeStoryTarget: () => null, consumeHomeVoiceTarget: () => null, consumeNarrationRequest: () => false, isAuthError: () => false });
  const context = pageContext(loadPageModule("../pages/home/index.ts").page);
  context.snapshot = snapshot;
  return context;
}

test("first-visit launch context remains until the user acts, including refresh", async () => {
  const context = fixture(false);
  try {
    await context.onShow();
    assert.equal(context.data.launchVariant, "first_visit");
    await context.loadHome(true);
    assert.equal(context.data.launchVariant, "first_visit");
  } finally { context.onHide(); }
});

test("identity cache does not consume first-visit context before the network result", async () => {
  const context = fixture(true);
  try {
    await context.onShow();
    assert.equal(context.data.launchVariant, "first_visit");
  } finally { context.onHide(); }
});

test("dismissal survives refresh and another account gets independent context", async () => {
  const context = fixture(true);
  try {
    await context.onShow();
    context.dismissLaunchExperience();
    await context.loadHome(true);
    assert.equal(context.data.launchVariant, "none");
    context.snapshot.identity.id = "another-user";
    await context.loadHome(true);
    assert.equal(context.data.launchVariant, "first_visit");
  } finally { context.onHide(); }
});

test("soft-return context persists through refresh until dismissed", async () => {
  const context = fixture(true, { first_seen_at: "2020-01-01T00:00:00Z", last_opened_at: "2020-01-01T00:00:00Z" });
  try {
    await context.onShow();
    assert.equal(context.data.launchVariant, "returning_soft");
    await context.loadHome(true);
    assert.equal(context.data.launchVariant, "returning_soft");
    context.dismissLaunchExperience();
    await context.loadHome(true);
    assert.equal(context.data.launchVariant, "none");
  } finally { context.onHide(); }
});

test("first visit survives cached-project reconciliation with the active server project", async () => {
  const context = fixture(true);
  try {
    await context.onShow();
    context.snapshot.identity.active_project_id = "new-active-project";
    context.snapshot.identity.active_project = { id: "new-active-project", title: "给家人看的故事" };
    await context.loadHome(true);
    assert.equal(context.data.projectId, "new-active-project");
    assert.equal(context.data.launchVariant, "first_visit");
  } finally { context.onHide(); }
});
