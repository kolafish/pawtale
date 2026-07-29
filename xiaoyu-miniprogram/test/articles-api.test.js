require("../../miniprogram/test/register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { mockModule } = require("../../miniprogram/test/page-harness");

test("article API keeps the article filter and revision in the shared backend contract", async () => {
  const calls = [];
  mockModule("../../xiaoyu-miniprogram/services/auth.ts", {
    requestWithBackendSession: async (path, options = {}) => {
      calls.push({ path, options });
      return {};
    },
  });
  delete require.cache[require.resolve("../services/articles.ts")];
  const api = require("../services/articles.ts");

  await api.listArticles();
  await api.createArticle();
  await api.updateArticleTitle("project-1", "新的标题");
  await api.updateArticleContent("project-1", "document-1", "新的正文", 7);
  await api.deleteArticle("project-1");

  assert.equal(calls[0].path, "/api/projects?type=article");
  assert.deepEqual(calls[1].options.data, {
    type: "article",
    title: "未命名文章",
  });
  assert.deepEqual(calls[2].options.data, { title: "新的标题" });
  assert.deepEqual(calls[3].options.data, {
    content: "新的正文",
    expected_content_revision: 7,
  });
  assert.equal(calls[4].options.method, "DELETE");
});
