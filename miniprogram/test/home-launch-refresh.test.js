const test = require('node:test');
const assert = require('node:assert/strict');
require('./register-ts');
const { loadPageModule, mockModule, pageContext } = require('./page-harness');

function fixture(cached, userState) {
  const snapshot = {
    server_revision: 1,
    identity: { id: 'review-user', email: 'review@example.invalid', display_name: '作者',
      active_project_id: 'project-1', active_project: { id: 'project-1', title: '回忆录' } },
    latest_transcript: null, chapters: [], growth_events: [], progress: null,
    automation_events: { items: [], total: 0, limit: 8 },
    book_readable: false, book_char_count: 0, unassigned_image_count: 0, unassigned_video_count: 0,
  };
  const storage = new Map();
  if (userState) storage.set('pawtale_home_launch_state', { version: 1, users: { 'review-user': userState } });
  global.wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    pageScrollTo() {},
    createSelectorQuery: () => ({ select: () => ({ boundingClientRect: cb => ({ exec: () => cb({ bottom: 600 }) }) }) }),
  };
  mockModule('../stores/memoir-cache.ts', { writeMemoirIdentitySnapshot() {} });
  mockModule('../stores/memoir-read-cache.ts', {
    readCachedMemoirHome: () => ({ status: 'miss' }),
    readCachedMemoirIdentity: () => cached ? snapshot.identity : null,
    writeCachedMemoirHome() {},
  });
  mockModule('../services/memoir-snapshot-api.ts', { getMemoirStorySnapshot: async () => snapshot });
  mockModule('../services/voice-authorization.ts', { readVoiceAuthorizationConsent: () => true });
  mockModule('../services/accessibility-settings.ts', { readLargeTextEnabled: () => false });
  mockModule('../services/navigation.ts', { consumeHomeStoryTarget: () => null, consumeHomeVoiceTarget: () => null, isAuthError: () => false });
  const context = pageContext(loadPageModule('../pages/home/index.ts').page);
  context.snapshot = snapshot;
  return context;
}

test('first-visit introduction remains until user acts, including refresh', async () => {
  const context = fixture(false);
  try {
    await context.onShow();
    assert.equal(context.data.showLaunchPanel, true);
    await context.loadHome(true); // Same callback as the five-second refresh timer.
    assert.equal(context.data.showLaunchPanel, true);
  } finally { context.onHide(); }
});

test('identity cache does not consume first-visit introduction before network result', async () => {
  const context = fixture(true);
  try {
    await context.onShow();
    assert.equal(context.data.showLaunchPanel, true);
  } finally { context.onHide(); }
});

test('dismissal survives refresh and a different account gets its own introduction', async () => {
  const context = fixture(true);
  try {
    await context.onShow();
    context.dismissLaunchPanel();
    await context.loadHome(true);
    assert.equal(context.data.showLaunchPanel, false);
    context.snapshot.identity.id = 'another-user';
    await context.loadHome(true);
    assert.equal(context.data.launchVariant, 'first_visit');
    assert.equal(context.data.showLaunchPanel, true);
  } finally { context.onHide(); }
});

test('soft return stays visible through cached and network refresh until dismissed', async () => {
  const context = fixture(true, { first_seen_at: '2020-01-01T00:00:00Z', last_opened_at: '2020-01-01T00:00:00Z' });
  try {
    await context.onShow();
    assert.equal(context.data.launchVariant, 'returning_soft');
    await context.loadHome(true);
    assert.equal(context.data.showReturnPrompt, true);
    context.dismissReturnPrompt();
    await context.loadHome(true);
    assert.equal(context.data.showReturnPrompt, false);
  } finally { context.onHide(); }
});

test('first visit survives reconciliation of a cached project with the active server project', async () => {
  const context = fixture(true);
  try {
    await context.onShow();
    context.snapshot.identity.active_project_id = 'new-active-project';
    context.snapshot.identity.active_project = { id: 'new-active-project', title: '给家人看的故事' };
    await context.loadHome(true);
    assert.equal(context.data.projectId, 'new-active-project');
    assert.equal(context.data.launchVariant, 'first_visit');
    assert.equal(context.data.showLaunchPanel, true);
  } finally { context.onHide(); }
});
