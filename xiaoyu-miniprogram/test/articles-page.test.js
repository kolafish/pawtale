require("../../miniprogram/test/register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  loadPageModule,
  mockModule,
  pageContext,
} = require("../../miniprogram/test/page-harness");

function article(id, title) {
  return {
    id,
    type: "article",
    title,
    total_word_count: 12,
    document_count: 1,
    updated_at: "2026-07-29T08:00:00Z",
  };
}

function loadArticlesPage(overrides = {}) {
  mockModule("../../xiaoyu-miniprogram/services/articles.ts", {
    listArticles: async () => [article("article-1", "第一篇文章")],
    createArticle: async () => article("article-2", "未命名文章"),
    deleteArticle: async () => undefined,
    ...overrides,
  });
  return loadPageModule("../../xiaoyu-miniprogram/pages/articles/index.ts");
}

test("article list loads, creates, opens and deletes articles", async () => {
  const navigations = [];
  const deleted = [];
  global.wx = {
    navigateTo({ url }) {
      navigations.push(url);
    },
    showModal(options) {
      options.success({ confirm: true });
    },
    showToast() {},
  };
  const { page } = loadArticlesPage({
    deleteArticle: async (id) => {
      deleted.push(id);
    },
  });
  const context = pageContext(page);

  await page.loadArticles.call(context);
  assert.equal(context.data.articles[0].wordCountText, "12 字");

  await page.createArticle.call(context);
  page.openArticle.call(context, {
    currentTarget: { dataset: { id: "article-1" } },
  });
  await page.deleteArticle.call(context, {
    currentTarget: { dataset: { id: "article-1" } },
  });

  assert.deepEqual(navigations, [
    "/pages/editor/index?projectId=article-2",
    "/pages/editor/index?projectId=article-1",
  ]);
  assert.deepEqual(deleted, ["article-1"]);
  assert.deepEqual(context.data.articles, []);
  delete global.wx;
});
