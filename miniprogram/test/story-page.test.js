require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  loadPageModule,
  mockModule,
  pageContext,
} = require("./page-harness");

function loadStoryPage({ largeText = false } = {}) {
  mockModule("../services/accessibility-settings.ts", {
    accessibilityPageClass: (baseClass) => largeText ? `${baseClass} large-text` : baseClass,
  });
  return loadPageModule("../pages/story/index.ts");
}

test("story preview is registered without replacing the current tab bar", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));

  assert.ok(appJson.pages.includes("pages/story/index"));
  assert.ok(!appJson.tabBar.list.some((item) => item.pagePath === "pages/story/index"));
});

test("story preview starts with a representative typed life tree", () => {
  const { page } = loadStoryPage();

  assert.equal(page.data.memoir.title, "一路走来的故事");
  assert.equal(page.data.chapters.length, 5);
  assert.equal(page.data.chapters[0].expanded, true);
  assert.equal(page.data.chapters[0].sections[1].videoCount, 1);
  assert.equal(page.data.chapters[1].expanded, false);
});

test("story preview expands and collapses only the selected chapter", () => {
  const { page } = loadStoryPage();
  const context = pageContext(page);
  const beforeSecondChapter = context.data.chapters[1];

  page.toggleChapter.call(context, {
    currentTarget: { dataset: { id: "childhood" } },
  });

  assert.equal(context.data.chapters[0].expanded, false);
  assert.equal(context.data.chapters[1], beforeSecondChapter);
});

test("story preview follows the shared large text preference", () => {
  const { page } = loadStoryPage({ largeText: true });
  const context = pageContext(page);

  page.onShow.call(context);

  assert.match(context.data.pageClass, /large-text/);
});
