require('./register-ts');
const assert = require('node:assert/strict');
const test = require('node:test');
const { componentContext, loadComponentModule, mockModule } = require('./page-harness');
const { ApiError } = require('../services/request');

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
