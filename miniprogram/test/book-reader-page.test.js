require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

const { sampleBookOutput, sampleStructuredBookContent } = require("./book-fixtures");

function sampleFinishedBook(content = "全书正文") {
  return sampleBookOutput(1, content, false, [], sampleStructuredBookContent());
}

function loadBookReaderPage(voiceApiExports = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    getMemoirBookOutput: async () => sampleFinishedBook(),
    ...voiceApiExports,
  });
  return loadPageModule("../pages/book-reader/index.ts");
}

test("book reader page is registered as the finished-book route", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  const pageJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../pages/book-reader/index.json"), "utf8"));
  const pageWxml = fs.readFileSync(path.join(__dirname, "../pages/book-reader/index.wxml"), "utf8");

  assert.ok(appJson.pages.includes("pages/book-reader/index"));
  assert.equal(pageJson.navigationBarTitleText, "成书阅读");
  assert.match(pageWxml, /成书阅读/);
  assert.doesNotMatch(pageWxml, /导出|重写|输入书名/);
});

test("book reader page loads the finished book by project id", async () => {
  const calls = [];
  const { page } = loadBookReaderPage({
    getMemoirBookOutput: async (projectId) => {
      calls.push(projectId);
      return sampleFinishedBook("外婆回忆录正文");
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project/1" });

  assert.deepEqual(calls, ["project/1"]);
  assert.equal(context.data.projectId, "project/1");
  assert.equal(context.data.readerTitle, "外婆回忆录");
  assert.equal(context.data.bookContent, "外婆回忆录正文");
  assert.equal(context.data.bookPreviewChapters.length, 2);
  assert.deepEqual(
    context.data.bookPreviewTocItems[0].sections.map((section) => section.title),
    ["院子里的日子", "外婆的身影"],
  );
  assert.deepEqual(
    context.data.bookPreviewChapters[0].sections.map((section) => section.content),
    ["我小时候常在院子里晒太阳。", "外婆总在屋里屋外忙碌。"],
  );
  assert.equal(context.data.error, "");
});

test("book reader page shares the same finished-book route", async () => {
  const shareMenuCalls = [];
  global.wx = {
    showShareMenu(options) {
      shareMenuCalls.push(options);
    },
  };
  try {
    const { page } = loadBookReaderPage({
      getMemoirBookOutput: async () => sampleFinishedBook("外婆回忆录正文"),
    });
    const context = pageContext(page);

    await page.onLoad.call(context, { projectId: "project/1" });
    page.onShow.call(context);

    assert.deepEqual(shareMenuCalls, [{
      menus: ["shareAppMessage", "shareTimeline"],
    }]);
    assert.deepEqual(page.onShareAppMessage.call(context), {
      title: "送你一本《外婆回忆录》",
      path: "/pages/book-reader/index?projectId=project%2F1&from=share_book",
    });
    assert.deepEqual(page.onShareTimeline.call(context), {
      title: "送你一本《外婆回忆录》",
      query: "projectId=project%2F1&from=share_book",
    });
  } finally {
    delete global.wx;
  }
});

test("book reader jumps from the table of contents to a chapter and back to the top", () => {
  const scrolls = [];
  global.wx = {
    pageScrollTo(options) {
      scrolls.push(options);
    },
  };
  const { page } = loadBookReaderPage();
  const context = pageContext(page);

  page.openReaderChapter.call(context, {
    currentTarget: { dataset: { index: "1" } },
  });
  page.scrollReaderToTop.call(context);

  assert.deepEqual(scrolls, [
    { selector: "#reader-chapter-1", duration: 280 },
    { scrollTop: 0, duration: 280 },
  ]);
  delete global.wx;
});

test("book reader keeps flat chapter content as a historical-version fallback", async () => {
  const structured = sampleStructuredBookContent({ chapterCount: 1 });
  structured.table_of_contents[0].sections = [];
  structured.chapters[0].sections = [];
  const { page } = loadBookReaderPage({
    getMemoirBookOutput: async () => sampleBookOutput(
      1,
      "旧版扁平正文",
      false,
      [],
      structured,
    ),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project/1" });

  assert.equal(context.data.bookPreviewChapters[0].sections.length, 0);
  assert.equal(
    context.data.bookPreviewChapters[0].content,
    "我小时候常在院子里晒太阳。",
  );
});

test("book reader opens the version selected by the export flow", async () => {
  const latest = sampleFinishedBook("最新版正文");
  const historical = {
    ...latest.latest_version,
    id: "book-output-version-history",
    version: 0,
    content: "历史版本正文",
  };
  const { page } = loadBookReaderPage({
    getMemoirBookOutput: async () => ({
      ...latest,
      versions: [historical, ...(latest.versions || [])],
    }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project/1",
    versionId: "book-output-version-history",
  });

  assert.equal(context.data.versionId, "book-output-version-history");
  assert.equal(context.data.bookContent, "历史版本正文");
});
