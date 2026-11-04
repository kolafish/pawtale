require('./register-ts');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { componentContext, flushAsyncWork, loadComponentModule, mockModule } = require('./page-harness');
const { ApiError } = require('../services/request');

test('shared reading and video keep an unsent response mounted while content reloads', () => {
  for (const page of ['book-reader', 'share-video-player']) {
    const wxml = fs.readFileSync(path.join(__dirname, `../pages/${page}/index.wxml`), 'utf8');
    const host = /<view\s+wx:if="([^"]+)"\s+hidden="([^"]+)">\s*<share-response-form\b/.exec(wxml);
    assert.ok(host, `${page} keeps the response form in a stable host`);
    assert.match(wxml.slice(0, host.index), /<\/block>\s*$/, `${page} places the host outside the content branch`);
    assert.doesNotMatch(host[1], /loading|error|video\b/, `${page} does not unmount a draft during reload`);
    assert.match(host[2], /loading/);
    assert.match(host[2], /error/);
    assert.match(wxml.slice(host.index), /<share-response-form\b[^>]*active="\{\{[^\"]*loading[^\"]*error[^\"]*\}\}"/);
  }
  const form = fs.readFileSync(path.join(__dirname, '../components/share-response-form/index.wxml'), 'utf8');
  assert.match(form, /<voice-text-editor\b[^>]*disabled="\{\{!active\}\}"/);
});

test('hiding a response cancels a pending voice ticket without losing its draft', async () => {
  let finishTicket;
  mockModule('../services/share-responses.ts', {
    startShareResponseVoice: () => new Promise(resolve => { finishTicket = resolve; }),
  });
  const { component } = loadComponentModule('../components/share-response-form/index.ts');
  const ctx = componentContext(component, { readingToken: 'story-token' });
  ctx.openForm();
  ctx.changeText({ detail: { value: '我记得院子里的树。' } });
  const pending = ctx.requestVoice();
  ctx.properties.active = false;
  component.observers.active.call(ctx, false);
  finishTicket({ gatewayUrl: 'wss://example.test/voice' });
  await pending;
  assert.equal(ctx.data.content, '我记得院子里的树。');
  assert.equal(ctx.data.open, true);
  assert.equal(ctx.data.busy, false);
  assert.equal(ctx.data.gatewayUrl, '');
  assert.equal(ctx.data.voiceStartRequest, 0);
  ctx.properties.active = true;
  component.observers.active.call(ctx, true);
  const retry = ctx.requestVoice();
  finishTicket({ gatewayUrl: 'wss://example.test/voice' });
  await retry;
  assert.equal(ctx.data.voiceStartRequest, 1);
  ctx.voiceSource({ detail: { captureId: '11111111-1111-4111-8111-111111111111' } });
  ctx.properties.active = false;
  component.observers.active.call(ctx, false);
  assert.equal(ctx.data.content, '我记得院子里的树。');
  assert.equal(ctx.data.voiceCaptureId, '11111111-1111-4111-8111-111111111111');
});

test('hiding a response preserves the idempotency key for a retry', async () => {
  const calls = [];
  let failSubmit;
  mockModule('../services/share-responses.ts', {
    submitShareResponse: (...args) => {
      calls.push(args);
      if (calls.length === 1) return new Promise((_, reject) => { failSubmit = reject; });
      return Promise.resolve({ id: 'response' });
    },
  });
  const { component } = loadComponentModule('../components/share-response-form/index.ts');
  const ctx = componentContext(component, { videoCode: 'video-code' });
  ctx.openForm();
  ctx.changeText({ detail: { value: '我记得这段往事。' } });
  const pending = ctx.submit();
  component.observers['readingToken,videoCode'].call(ctx);
  ctx.properties.active = false;
  component.observers.active.call(ctx, false);
  assert.equal(ctx.data.busy, true);
  failSubmit(new Error('网络中断'));
  await pending;
  assert.equal(ctx.data.busy, false);
  assert.equal(ctx.data.content, '我记得这段往事。');
  ctx.properties.active = true;
  component.observers.active.call(ctx, true);
  await ctx.submit();
  assert.equal(calls[0][2], calls[1][2]);
  assert.equal(ctx.data.sent, true);
});

test('hiding a response keeps an in-flight submission until its success is shown', async () => {
  const calls = [];
  let finishSubmit;
  mockModule('../services/share-responses.ts', {
    submitShareResponse: (...args) => {
      calls.push(args);
      return new Promise(resolve => { finishSubmit = resolve; });
    },
  });
  const { component } = loadComponentModule('../components/share-response-form/index.ts');
  const ctx = componentContext(component, { videoCode: 'video-code' });
  ctx.openForm();
  ctx.changeText({ detail: { value: '我记得这段往事。' } });
  const pending = ctx.submit();
  component.observers['readingToken,videoCode'].call(ctx);
  ctx.properties.active = false;
  component.observers.active.call(ctx, false);
  assert.equal(ctx.data.busy, true);
  assert.equal(ctx.data.content, '我记得这段往事。');
  finishSubmit({ id: 'saved' });
  await pending;
  assert.equal(ctx.data.busy, false);
  assert.equal(ctx.data.sent, true);
  assert.equal(ctx.data.content, '');
  ctx.properties.active = true;
  component.observers.active.call(ctx, true);
  await ctx.submit();
  assert.equal(calls.length, 1);
});

test('response preserves text through login and network retry with the same submission id', async () => {
  const calls = []; let mode = 'login';
  mockModule('../services/share-responses.ts', { submitShareResponse: async (...args) => {
    calls.push(args); if (mode === 'login') throw new ApiError(401, '请登录', {});
    if (mode === 'network') throw new Error('网络中断');
    return { id: 'response' };
  } });
  const navigations = []; global.wx = { navigateTo: ({url}) => navigations.push(url) };
  const { component } = loadComponentModule('../components/share-response-form/index.ts');
  const ctx = componentContext(component, { readingToken: 'story-token' });
  ctx.changeText({ detail: { value: '我记得这棵树。' } });
  await ctx.submit();
  assert.equal(ctx.data.content, '我记得这棵树。');
  assert.deepEqual(navigations, ['/pages/login/index?from=response']);
  mode = 'network'; await ctx.submit();
  assert.equal(ctx.data.content, '我记得这棵树。');
  mode = 'ok'; await ctx.submit();
  assert.equal(new Set(calls.map(x => x[2])).size, 1);
  assert.deepEqual(calls[0][0], {reading_token:'story-token'});
  assert.equal(ctx.data.sent, true); assert.equal(ctx.data.content, '');
});

test('response late submit cannot replace another share form', async () => {
  let finish;
  mockModule('../services/share-responses.ts', { submitShareResponse: () => new Promise(resolve => finish = resolve) });
  const { component } = loadComponentModule('../components/share-response-form/index.ts');
  const ctx = componentContext(component, {videoCode:'video-a'});
  ctx.changeText({detail:{value:'旧视频的回应'}});
  const pending = ctx.submit();
  ctx.properties.videoCode = 'video-b';
  component.observers['readingToken,videoCode'].call(ctx);
  ctx.changeText({detail:{value:'新故事的回应'}});
  finish({id:'old'}); await pending;
  assert.equal(ctx.data.content, '新故事的回应'); assert.equal(ctx.data.sent, false);
});

test('private inbox discards a request after author/project changes', async () => {
  let finish;
  mockModule('../services/share-responses.ts', { listShareResponses: () => new Promise(resolve => finish = resolve) });
  const { component } = loadComponentModule('../components/share-response-inbox/index.ts');
  const ctx = componentContext(component, { projectId:'p', userId:'owner' });
  const pending = ctx.load();
  ctx.properties.projectId = ''; ctx.properties.userId = '';
  component.observers['projectId,userId'].call(ctx);
  finish({items:[{id:'private',content:'private'}],unread_count:1,next_before_id:null}); await pending;
  assert.deepEqual(ctx.data.items, []); assert.equal(ctx.data.selected, null);
});

test('opening one response twice before either read returns decrements unread only once', async () => {
  const pending = [];
  mockModule('../services/share-responses.ts', {
    readShareResponse: () => new Promise(resolve => pending.push(resolve)),
  });
  const { component } = loadComponentModule('../components/share-response-inbox/index.ts');
  const ctx = componentContext(component, { projectId: 'p', userId: 'owner' }, {
    items: [{ id: 'a', read_at: null }, { id: 'b', read_at: null }], unread: 2,
  });
  const first = ctx.select({ currentTarget: { dataset: { id: 'a' } } });
  ctx.back();
  const second = ctx.select({ currentTarget: { dataset: { id: 'a' } } });
  pending[0]({ id: 'a', read_at: '2026-10-25T08:00:00Z' });
  await first;
  pending[1]({ id: 'a', read_at: '2026-10-25T08:00:00Z' });
  await second;
  assert.equal(ctx.data.unread, 1);
  assert.equal(ctx.data.items.find(item => item.id === 'b').read_at, null);
});

test('voice response keeps confirmed text and binds only its finalized source', async () => {
  const submits = [];
  mockModule('../services/share-responses.ts', {
    startShareResponseVoice: async () => ({ gatewayUrl: 'wss://example.test/voice' }),
    submitShareResponse: async (...args) => { submits.push(args); return { id: 'saved' }; },
  });
  const { component } = loadComponentModule('../components/share-response-form/index.ts');
  const ctx = componentContext(component, { readingToken: 'story-token' });
  ctx.openForm();
  ctx.changeText({ detail: { value: '我还记得那棵树。' } });
  await ctx.requestVoice();
  await flushAsyncWork();
  assert.equal(ctx.data.gatewayUrl, 'wss://example.test/voice');
  assert.equal(ctx.data.voiceStartRequest, 1);
  assert.equal(ctx.data.voiceCaptureId, '');
  ctx.voiceSource({ detail: { captureId: '11111111-1111-4111-8111-111111111111' } });
  await ctx.submit();
  assert.equal(submits[0][1], '我还记得那棵树。');
  assert.equal(submits[0][3], '11111111-1111-4111-8111-111111111111');
});

test('voice start waits for gateway URL to reach the child component', async () => {
  mockModule('../services/share-responses.ts', {
    startShareResponseVoice: async () => ({ gatewayUrl: 'wss://example.test/voice' }),
  });
  const { component } = loadComponentModule('../components/share-response-form/index.ts');
  const ctx = componentContext(component, { videoCode: 'video' });
  ctx.openForm();
  const immediateSetData = ctx.setData;
  let gatewayBound;
  ctx.setData = function (patch, callback) {
    immediateSetData.call(this, patch);
    if (patch.gatewayUrl) gatewayBound = callback;
    else callback?.();
  };
  await ctx.requestVoice();
  assert.equal(ctx.data.gatewayUrl, 'wss://example.test/voice');
  assert.equal(ctx.data.voiceStartRequest, 0);
  gatewayBound();
  assert.equal(ctx.data.voiceStartRequest, 1);
});

test('closing and reopening a response form does not replay an old voice ticket', async () => {
  mockModule('../services/share-responses.ts', {
    startShareResponseVoice: async () => ({ gatewayUrl: 'wss://example.test/voice' }),
  });
  const { component } = loadComponentModule('../components/share-response-form/index.ts');
  const ctx = componentContext(component, { videoCode: 'video' });
  ctx.openForm();
  await ctx.requestVoice();
  assert.equal(ctx.data.voiceStartRequest, 1);
  ctx.closeForm();
  ctx.openForm();
  assert.equal(ctx.data.voiceStartRequest, 0);
  assert.equal(ctx.data.gatewayUrl, '');

  const immediateSetData = ctx.setData;
  let bindGateway;
  ctx.setData = function (patch, callback) {
    immediateSetData.call(this, patch);
    if (patch.gatewayUrl) bindGateway = callback;
    else callback?.();
  };
  await ctx.requestVoice();
  ctx.closeForm();
  bindGateway();
  assert.equal(ctx.data.voiceStartRequest, 0);
});

test('expired original audio can be replaced without losing response text', async () => {
  const submits = [];
  mockModule('../services/share-responses.ts', {
    submitShareResponse: async (...args) => {
      submits.push(args);
      if (args[3]) throw new ApiError(410, '原音已过期', {});
      return { id: 'text-only' };
    },
  });
  const { component } = loadComponentModule('../components/share-response-form/index.ts');
  const ctx = componentContext(component, { readingToken: 'story-token' });
  ctx.changeText({ detail: { value: '我仍记得这段往事。' } });
  ctx.voiceSource({ detail: { captureId: '11111111-1111-4111-8111-111111111111' } });
  await ctx.submit();
  assert.equal(ctx.data.content, '我仍记得这段往事。');
  assert.equal(ctx.data.voiceCaptureId, '');
  assert.match(ctx.data.error, /重新录音/);
  await ctx.submit();
  assert.equal(submits[1][3], '');
  assert.equal(ctx.data.sent, true);
  ctx.changeText({ detail: { value: '下一条' } });
  ctx.voiceSource({ detail: { captureId: '22222222-2222-4222-8222-222222222222' } });
  ctx.replaceVoice();
  assert.equal(ctx.data.voiceCaptureId, '');
  assert.equal(ctx.data.content, '下一条');
});

test('old private audio download cannot change newly selected response playback state', async () => {
  let finishA; let finishB;
  class Playback {
    constructor() { this.token = 0; }
    stop() { this.token += 1; }
    destroy() { this.stop(); }
    setDownloadAudioFile(download) { this.stop(); this.download = download; }
    nextToken() { return ++this.token; }
    isCurrentToken(token) { return this.token === token; }
    async playFile(id, token, options) {
      if (id === 'a') await new Promise(resolve => { finishA = resolve; });
      if (!this.isCurrentToken(token)) return;
      options.onPlay();
      if (id === 'b') await new Promise(resolve => { finishB = resolve; });
    }
  }
  mockModule('../services/audio-playback.ts', { AudioPlaybackController: Playback });
  mockModule('../services/share-responses.ts', {
    readShareResponse: async id => ({ id, voice_capture_id: 'voice' }),
    downloadShareResponseAudio: async id => id,
  });
  const { component } = loadComponentModule('../components/share-response-inbox/index.ts');
  const ctx = componentContext(component, { projectId: 'p', userId: 'owner' }, {
    items: [{ id: 'a', voice_capture_id: 'voice' }, { id: 'b', voice_capture_id: 'voice' }],
  });
  await ctx.select({ currentTarget: { dataset: { id: 'a' } } });
  const old = ctx.playOriginal();
  await ctx.select({ currentTarget: { dataset: { id: 'b' } } });
  const latest = ctx.playOriginal();
  await flushAsyncWork();
  assert.equal(ctx.data.selected.id, 'b');
  assert.equal(ctx.data.audioPlaying, true);
  finishA();
  await old;
  assert.equal(ctx.data.selected.id, 'b');
  assert.equal(ctx.data.audioPlaying, true);
  finishB();
  await latest;
  assert.equal(ctx.data.audioPlaying, false);
});

test("the author can feature an excerpt response in its own chapter and unfeature it", async () => {
  const row = { id: "r", can_feature: true, featured_chapter_id: null };
  const changes = [];
  mockModule("../services/share-responses.ts", { featureShareResponse: async (id, chapter, featured) => {
    changes.push([id, featured]);
    return { ...row, featured_at: featured ? "now" : null, featured_chapter_id: featured ? (chapter || "chapter") : null };
  } });
  const { component } = loadComponentModule("../components/share-response-inbox/index.ts");
  const ctx = componentContext(component, { projectId: "p", userId: "owner" });
  ctx.setData({ items: [row], selected: row });
  await ctx.toggleFeatured();
  assert.equal(ctx.data.selected.featured_chapter_id, "chapter");
  await ctx.toggleFeatured();
  assert.deepEqual(changes, [["r", true], ["r", false]]);
});
