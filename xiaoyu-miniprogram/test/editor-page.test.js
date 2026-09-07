const assert = require("node:assert/strict");
const test = require("node:test");
const {
  loadPageModule,
  mockModule,
  pageContext,
} = require("../../miniprogram/test/page-harness");

class TestApiError extends Error {
  constructor(statusCode, message) {
    super(message);
    this.statusCode = statusCode;
  }
}

function loadEditorPage(overrides = {}, dictationRuntime = {}) {
  mockModule("../../xiaoyu-miniprogram/services/request.ts", {
    ApiError: TestApiError,
  });
  mockModule("../../xiaoyu-miniprogram/services/dictation.ts", {
    ArticleDictationClient: dictationRuntime.ArticleDictationClient || class {
      async connect() {}
      async start() {}
      async stop() {}
      async close() {}
    },
  });
  mockModule("../../xiaoyu-miniprogram/services/recorder-authorization.ts", {
    ensureRecorderAuthorization: dictationRuntime.ensureRecorderAuthorization || (async () => {}),
  });
  mockModule("../../xiaoyu-miniprogram/services/articles.ts", {
    getArticle: async () => ({
      id: "project-1",
      type: "article",
      title: "原来的标题",
      groups: [{
        id: "group-1",
        name: "正文",
        documents: [{
          id: "document-1",
          group_id: "group-1",
          name: "未命名文章",
          word_count: 2,
          content_revision: 4,
          updated_at: "2026-07-29T08:00:00Z",
        }],
      }],
    }),
    getArticleDocument: async () => ({
      id: "document-1",
      group_id: "group-1",
      name: "未命名文章",
      content: "原文",
      word_count: 2,
      content_revision: 4,
      updated_at: "2026-07-29T08:00:00Z",
    }),
    updateArticleTitle: async () => ({}),
    updateArticleContent: async () => ({
      content_revision: 5,
    }),
    continueArticle: async () => ({
      content: "原文\n\n续写",
      content_revision: 5,
      generated_chunk: "续写",
    }),
    reviseArticle: async () => ({
      revised_text: "修改后的正文",
      original_text: "原文",
      content_revision: 4,
    }),
    reviewArticle: async () => ({
      summary: "主题清楚。",
      suggestions: [],
      content_revision: 4,
    }),
    ...overrides,
  });
  return loadPageModule("../../xiaoyu-miniprogram/pages/editor/index.ts");
}

for (const lifecycle of ["onHide", "onUnload"]) {
  test(`editor ignores permission completion after ${lifecycle}`, async () => {
    let allow;
    const permission = new Promise((resolve) => { allow = resolve; });
    let starts = 0;
    const { page } = loadEditorPage({}, {
      ensureRecorderAuthorization: () => permission,
      ArticleDictationClient: class {
        async connect() { throw new Error("hidden page must not connect"); }
        async start() { starts += 1; }
        async close() {}
      },
    });
    const context = pageContext(page);
    await context.onLoad({ projectId: "project-1" });
    const starting = context.startDictation();
    context[lifecycle]();
    await context.finishPromise;
    allow();
    await starting;
    assert.equal(starts, 0);
    assert.equal(context.dictationClient, null);
    assert.equal(context.data.error, "");
  });
}

test("editor cancels a pending connection and can start again after showing", async () => {
  let connected;
  const connecting = new Promise((resolve) => { connected = resolve; });
  let clients = 0;
  let starts = 0;
  let closes = 0;
  const { page } = loadEditorPage({}, {
    ArticleDictationClient: class {
      constructor() { clients += 1; }
      async connect() { if (clients === 1) await connecting; }
      async start() { starts += 1; }
      async close() { closes += 1; }
    },
  });
  const context = pageContext(page);
  await context.onLoad({ projectId: "project-1" });
  const first = context.startDictation();
  await Promise.resolve();
  const duplicate = context.startDictation();
  context.onHide();
  await context.finishPromise;
  context.onShow();
  await context.startDictation();
  connected();
  await Promise.all([first, duplicate]);
  assert.equal(clients, 2);
  assert.equal(starts, 1);
  assert.equal(closes, 1);
  await context.releaseDictationClient();
});

test("editor autosave sends the loaded content revision", async () => {
  const updates = [];
  const { page } = loadEditorPage({
    updateArticleContent: async (...args) => {
      updates.push(args);
      return { content_revision: 5 };
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1" });

  page.onContentInput.call(context, { detail: { value: "原文继续写" } });
  await page.saveNow.call(context);

  assert.deepEqual(updates, [
    ["project-1", "document-1", "原文继续写", 4],
  ]);
  assert.equal(context.data.contentRevision, 5);
  assert.equal(context.data.saveState, "saved");
});

test("editor stops overwriting and asks for reload on revision conflict", async () => {
  const { page } = loadEditorPage({
    updateArticleContent: async () => {
      throw new TestApiError(409, "conflict");
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1" });

  page.onContentInput.call(context, { detail: { value: "本地的新内容" } });
  await page.saveNow.call(context);

  assert.equal(context.data.saveState, "conflict");
  assert.match(context.data.error, /其他位置修改/);
  assert.equal(context.data.content, "本地的新内容");
});

test("editor replaces live partial text and saves only the final dictation", async () => {
  let callbacks;
  const updates = [];
  class FakeDictationClient {
    constructor(options) {
      callbacks = options.callbacks;
    }
    async connect() {
      callbacks.onState("ready");
    }
    async start() {
      callbacks.onState("recording");
    }
    async stop() {
      callbacks.onState("stopping");
      callbacks.onTranscript("口述完成", true);
      callbacks.onState("ready");
    }
    async close() {}
  }
  const { page } = loadEditorPage({
    updateArticleContent: async (...args) => {
      updates.push(args);
      return { content_revision: 5 };
    },
  }, {
    ArticleDictationClient: FakeDictationClient,
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1" });
  context.setData({ cursorPosition: 1 });

  await page.startDictation.call(context);
  callbacks.onTranscript("口述", false);
  assert.equal(context.data.content, "原口述文");
  assert.equal(context.data.dictationActive, true);

  await page.stopDictation.call(context);
  await page.saveNow.call(context);

  assert.equal(context.data.content, "原口述完成文");
  assert.equal(context.data.dictationActive, false);
  assert.deepEqual(updates, [
    ["project-1", "document-1", "原口述完成文", 4],
  ]);
});

test("editor saves first and applies AI continuation returned by the backend", async () => {
  const calls = [];
  global.wx = { showToast() {} };
  const { page } = loadEditorPage({
    continueArticle: async (...args) => {
      calls.push(args);
      return {
        content: "原文\n\n接下来继续写。",
        content_revision: 5,
        generated_chunk: "接下来继续写。",
      };
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1" });
  context.setData({ aiInstruction: "顺着原来的语气" });

  await page.runAIAction.call(context, {
    currentTarget: { dataset: { action: "continue" } },
  });

  assert.deepEqual(calls, [
    ["project-1", "document-1", 4, "顺着原来的语气"],
  ]);
  assert.equal(context.data.content, "原文\n\n接下来继续写。");
  assert.equal(context.data.contentRevision, 5);
  assert.equal(context.data.saveState, "saved");
  delete global.wx;
});

test("editor previews an AI revision and applies it with revision protection", async () => {
  const updates = [];
  global.wx = { showToast() {} };
  const { page } = loadEditorPage({
    reviseArticle: async () => ({
      revised_text: "更自然的正文",
      original_text: "原文",
      content_revision: 4,
    }),
    updateArticleContent: async (...args) => {
      updates.push(args);
      return { content_revision: 5, word_count: 6 };
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1" });
  context.setData({ aiInstruction: "表达更自然" });

  await page.runAIAction.call(context, {
    currentTarget: { dataset: { action: "revise" } },
  });
  assert.equal(context.data.content, "原文");
  assert.equal(context.data.aiProposal, "更自然的正文");

  await page.applyAIRevision.call(context);

  assert.deepEqual(updates, [
    ["project-1", "document-1", "更自然的正文", 4],
  ]);
  assert.equal(context.data.content, "更自然的正文");
  assert.equal(context.data.aiResultKind, "none");
  delete global.wx;
});

test("editor presents article review suggestions without changing the article", async () => {
  const { page } = loadEditorPage({
    reviewArticle: async () => ({
      summary: "主题明确，可以精简开头。",
      suggestions: [{
        excerpt: "原文",
        issue: "开头略泛",
        suggestion: "直接从核心观点切入",
      }],
      content_revision: 4,
    }),
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1" });

  await page.runAIAction.call(context, {
    currentTarget: { dataset: { action: "review" } },
  });

  assert.equal(context.data.content, "原文");
  assert.equal(context.data.aiResultKind, "review");
  assert.equal(context.data.aiSummary, "主题明确，可以精简开头。");
  assert.equal(context.data.aiSuggestions[0].suggestion, "直接从核心观点切入");
});

test("editor does not reapply an AI revision after entering conflict state", async () => {
  let updates = 0;
  const { page } = loadEditorPage({
    updateArticleContent: async () => {
      updates += 1;
      return { content_revision: 5, word_count: 6 };
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1" });
  context.setData({
    saveState: "conflict",
    aiResultKind: "revision",
    aiProposal: "候选正文",
    aiResultRevision: 4,
  });

  await page.applyAIRevision.call(context);

  assert.equal(updates, 0);
  assert.equal(context.data.content, "原文");
});
