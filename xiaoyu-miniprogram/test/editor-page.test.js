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

function loadEditorPage(overrides = {}) {
  mockModule("../../xiaoyu-miniprogram/services/request.ts", {
    ApiError: TestApiError,
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
    ...overrides,
  });
  return loadPageModule("../../xiaoyu-miniprogram/pages/editor/index.ts");
}

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
