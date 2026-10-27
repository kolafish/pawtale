require("./register-ts");
const { memoirAudioPlanLoader } = require("../services/memoir-book-api.ts");
const { sampleAudioPlan } = require("./book-fixtures");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  clearModule,
  mockMemoirApiModules,
  loadPageModule,
  mockModule,
  pageContext,
  flushAsyncWork,
  unmockedAsync,
} = require("./page-harness");

const {
  sampleBookManuscript,
  sampleBookOutput,
} = require("./book-fixtures");
const { readingParagraphs } = require("../pages/book/book-view-model.ts");
const { ApiError } = require("../services/request.ts");

function sampleFinishedBook(content = "全书正文") {
  return sampleBookOutput(1, content, false, [], [], sampleBookManuscript());
}

function loadBookReaderPage(voiceApiExports = {}, ttsExports = {}) {
  global.wx ||= { getStorageSync: () => "", setStorageSync() {} };
  mockMemoirApiModules({
    memoirAudioPlanLoader: (...args) => memoirAudioPlanLoader(...args, voiceApiExports.prepareMemoirAudio || unmockedAsync("prepareMemoirAudio")),
    getMemoirChapterWorkspace: unmockedAsync("getMemoirChapterWorkspace"),
    getMemoirBookOutput: async () => sampleFinishedBook(),
    getMemoirBookOutputVersion: unmockedAsync("getMemoirBookOutputVersion"),
    readMemoirBookShare: unmockedAsync("readMemoirBookShare"),
    ...voiceApiExports,
  });
  mockModule("../services/tts-settings.ts", {
    readBookTTSRequest: () => ({}),
    ...ttsExports,
  });
  clearModule("../services/share-likes.ts");
  clearModule("../services/reading-excerpt-share.ts");
  return loadPageModule("../pages/book-reader/index.ts");
}

test("book reader page is registered as the finished-book route", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  const pageJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../pages/book-reader/index.json"), "utf8"));

  assert.ok(appJson.pages.includes("pages/book-reader/index"));
  assert.equal(pageJson.navigationBarTitleText, "成书阅读");
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
  assert.equal(context.data.readerSubtitle, "根据口述访谈整理而成");
  assert.equal(context.data.readerMetaText, "2章 · 7字");
  assert.equal(context.readerText, "外婆回忆录正文");
  assert.equal(context.data.bookContent, "");
  assert.equal(context.data.bookPreviewChapters.length, 1);
  assert.equal(context.data.readerChapterTotal, 2);
  assert.equal(context.data.bookPreviewTocItems[0].expanded, false);
  assert.equal(context.data.bookPreviewTocItems[0].storyCountText, "2小节");
  assert.deepEqual(
    context.data.bookPreviewTocItems[0].sections.map((section) => section.title),
    ["院子里的日子", "外婆的身影"],
  );
  assert.deepEqual(
    context.data.bookPreviewChapters[0].sections.map((section) => section.content),
    ["", ""],
  );
  assert.deepEqual(
    context.data.bookPreviewChapters[0].sections[0].paragraphs,
    ["我小时候常在院子里晒太阳。"],
  );
  assert.equal(context.data.error, "");
});

test("shared reader opens without owner access and hides owner-only narration", async () => {
  const calls = [];
  const { page } = loadBookReaderPage({
    getMemoirBookOutput: unmockedAsync("owner book API"),
    readMemoirBookShare: async (token) => {
      calls.push(token);
      return sampleFinishedBook("只读分享正文");
    },
    // Family like what the link shows, whether a whole book or one chapter.
    readMemoirShareLikes: async (target) => {
      calls.push(target);
      return { count: 0, likers: [], liked: false, can_like: true };
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { shareToken: "family-token" });

  assert.deepEqual(calls, ["family-token", { reading_token: "family-token" }]);
  assert.equal(context.data.shareLikeVisible, true);
  assert.equal(context.data.readerShared, true);
  assert.equal(context.data.projectId, "");
  assert.equal(context.readerText, "只读分享正文");
  assert.equal(context.data.bookPreviewChapters.length, 1);
  assert.equal(context.data.error, "");
  await page.playReaderAudio.call(context);
  assert.equal(context.data.ttsBusyTarget, "");
});

test("returning to a shared reader replaces removed content with the current share result", async () => {
  let state = "first";
  const { page } = loadBookReaderPage({
    readMemoirBookShare: async () => {
      if (state === "removed") throw new ApiError(409, "作者正在更新分享书稿", {});
      return sampleFinishedBook(state === "first" ? "旧版正文" : "新版正文");
    },
    readMemoirShareLikes: async () => ({ count: 0, likers: [], liked: false, can_like: true }),
  });
  const context = pageContext(page);
  await context.onLoad({ shareToken: "family-token" });
  context.onShow();
  assert.equal(context.readerText, "旧版正文");
  context.onHide();
  state = "second";
  context.onShow();
  await flushAsyncWork();
  assert.equal(context.readerText, "新版正文");
  context.onHide();
  state = "removed";
  context.onShow();
  await flushAsyncWork();
  assert.equal(context.readerText, "");
  assert.match(context.data.error, /正在更新/);
});

test("demo reader uses the public book and disables account-only audio", async () => {
  const destinations = [];
  global.wx = { getStorageSync: () => "", navigateTo({url}) { destinations.push(url); } };
  const { page } = loadBookReaderPage({
    getMemoirBookOutput: unmockedAsync("owner book API"),
    readMemoirDemoBook: async () => sampleFinishedBook("家里的人完整章节"),
  });
  const context = pageContext(page);
  await context.onLoad({ demo: "1" });
  assert.equal(context.data.readerShared, true);
  assert.equal(context.data.shareLikeVisible, false);
  assert.equal(context.readerText, "家里的人完整章节");
  assert.equal(context.data.error, "");
  await context.playReaderAudio();
  assert.equal(context.data.ttsBusyTarget, "");
  assert.equal(context.data.readerDemo, true);
  context.startMyStory();
  assert.deepEqual(destinations, ["/pages/login/index"]);
  assert.equal(require("../services/navigation.ts").consumeNarrationRequest(), "after_login");
  delete global.wx;
});

test("a signed-in sample reader returns to the existing home to start narration", () => {
  const destinations = [];
  global.wx = {
    getStorageSync: () => "test-session",
    navigateTo: ({ url }) => destinations.push(url),
    navigateBack: ({ delta }) => destinations.push({ delta }),
  };
  global.getCurrentPages = () => [{ route: "pages/home/index" }, { route: "pages/book-reader/index" }];
  const { page } = loadBookReaderPage();
  const context = pageContext(page, { readerDemo: true });

  context.startMyStory();

  assert.deepEqual(destinations, [{ delta: 1 }]);
  const { consumeNarrationRequest } = require("../services/navigation.ts");
  assert.equal(consumeNarrationRequest(), "start");
  assert.equal(consumeNarrationRequest(), null);
});

test("reader paragraphs only honor line breaks saved by the language model", () => {
  const unbrokenContent = [
    "十九年前，我们因为一次偶然的点赞认识。",
    "后来虽然不常见面，但每次联系都觉得很亲切。",
    "那些年各自忙着生活，偶尔会在朋友圈里知道对方的近况。",
    "直到这个春天，我们终于约着一起去西山看山桃花。",
    "一路上聊起从前，才发现很多记忆并没有因为时间过去而消失。",
    "回到家以后，我仍然记得山路上的风和大家说笑的声音。",
  ].join("");

  assert.deepEqual(readingParagraphs(unbrokenContent), [unbrokenContent]);
  assert.deepEqual(
    readingParagraphs(`${unbrokenContent}\n\n第二段由模型明确换行。`),
    [unbrokenContent, "第二段由模型明确换行。"],
  );
});

test("book reader previews and highlights one current chapter", async () => {
  const paragraphs = [
    "十九年前，我们因为一次偶然的点赞认识。后来虽然不常见面，但每次联系都觉得很亲切。",
    "那些年各自忙着生活，直到这个春天，我们终于约着一起去西山看山桃花。",
  ];
  const longContent = paragraphs.join("\n\n");
  const secondContent = "山路上很安静。后来他主动伸手拉了我一把。";
  const { page } = loadBookReaderPage({
    getMemoirChapterWorkspace: async () => ({
      project_id: "project-1",
      chapter_id: "chapter-1",
      chapter_title: "山桃花开的心动",
      completion_summary: {},
      draft_needs_rewrite: false,
      can_rewrite_draft: false,
      pending_assignment_material_count: 0,
      pending_completion_source_count: 0,
      pending_new_source_count: 0,
      sections: [],
      draft_version_id: "version-1",
      draft_version: 2,
      draft_content: longContent,
      draft_sections: [{
        section_id: "section-1",
        title: "十九年点赞之交的淡交过往",
        content: longContent,
        used_material_ids: ["utterance-1"],
      }, {
        section_id: "section-2",
        title: "爬山途中的细碎关照",
        content: secondContent,
        used_material_ids: ["utterance-2"],
      }],
    }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    chapterId: "chapter-1",
  });

  assert.equal(context.data.readerMode, "chapter");
  assert.equal(context.data.readerTitle, "山桃花开的心动");
  assert.equal(context.data.bookPreviewChapters[0].sections[0].title, "十九年点赞之交的淡交过往");
  assert.deepEqual(
    context.data.bookPreviewChapters[0].sections[0].paragraphs,
    paragraphs,
  );
  assert.equal(
    context.readerText,
    `十九年点赞之交的淡交过往\n\n${longContent}\n\n爬山途中的细碎关照\n\n${secondContent}`,
  );
  assert.equal(context.data.bookContent, "");

  const activeText = Array.from(context.readerText);
  const currentTextStart = context.readerText.indexOf("主动伸手");
  page.onBookTTSProgressPatch.call(context, {
    ttsSpokenText: activeText.slice(0, currentTextStart).join(""),
    ttsCurrentText: activeText[currentTextStart],
    ttsRemainingText: activeText.slice(currentTextStart + 1).join(""),
    ttsProgressPercent: 20,
  });

  assert.equal(context.data.readerHighlightSectionId, "section-2");
  assert.equal(context.data.readerHighlightParagraphIndex, 0);
  assert.equal(
    context.data.readerHighlightCurrent,
    "后来他主动伸手拉了我一把。",
  );
});

test("book reader hides private content from system sharing", async () => {
  const shareMenuCalls = [];
  global.wx = {
    hideShareMenu() {
      shareMenuCalls.push("hidden");
    },
  };
  try {
    const { page } = loadBookReaderPage({
      getMemoirBookOutput: async () => sampleFinishedBook("外婆回忆录正文"),
    });
    const context = pageContext(page);

    await page.onLoad.call(context, { projectId: "project/1" });
    page.onShow.call(context);

    assert.deepEqual(shareMenuCalls, ["hidden"]);
    assert.equal(context.onShareAppMessage().path, "/pages/home/index?from=share_home");
    assert.equal(page.onShareTimeline, undefined);
  } finally {
    delete global.wx;
  }
});

test("book reader expands stories and jumps from the table of contents to the text", async () => {
  const scrolls = [];
  global.wx = {
    pageScrollTo(options) {
      scrolls.push(options);
    },
  };
  const { page } = loadBookReaderPage();
  const context = pageContext(page, {
    bookPreviewTocItems: [{
      id: "chapter-1:1",
      chapterId: "chapter-1",
      index: 1,
      indexText: "1",
      title: "外婆家的童年",
      metaText: "",
      storyCountText: "1小节",
      expanded: false,
      sections: [{ id: "section-1" }],
    }, {
      id: "chapter-2:2",
      chapterId: "chapter-2",
      index: 2,
      indexText: "2",
      title: "第一次离家",
      metaText: "",
      storyCountText: "整章正文",
      expanded: false,
      sections: [],
    }],
  });
  context.bookChapters = [
    { chapterId: "chapter-1", sections: [{ id: "section-1" }] },
    { chapterId: "chapter-2", sections: [] },
  ];
  context.data.readerChapterTotal = 2;

  page.toggleTocChapter.call(context, {
    currentTarget: { dataset: { chapterIndex: "0" } },
  });
  assert.equal(context.data.bookPreviewTocItems[0].expanded, true);

  page.openReaderSection.call(context, {
    currentTarget: { dataset: { chapterId: "chapter-1", sectionId: "section-1" } },
  });
  page.toggleTocChapter.call(context, {
    currentTarget: { dataset: { chapterIndex: "1" } },
  });
  page.scrollReaderToTop.call(context);

  assert.deepEqual(scrolls, [
    { selector: "#reader-section-0", duration: 280 },
    { selector: "#reader-body", duration: 280 },
    { scrollTop: 0, duration: 280 },
  ]);
  assert.equal(context.data.readerChapterIndex, 1);
});

test("book reader identifies the saved version when chapters have newer changes", async () => {
  const { page } = loadBookReaderPage({
    getMemoirBookOutput: async () => sampleBookOutput(
      3,
      "更新后的正文",
      true,
      ["工作与创业经历", "家庭生活"],
      [],
      sampleBookManuscript(),
    ),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project-1" });

  assert.equal(context.readerAudioVersionId, "book-output-version-3");
  assert.match(context.data.readerUpdateNotice, /已保存书稿.*尚未包含.*工作与创业经历/);
  assert.equal(context.data.readerMetaText, "2章 · 6字");
});

test("book reader template keeps editing language out of the reading view", () => {
  const template = fs.readFileSync(
    path.join(__dirname, "../pages/book-reader/index.wxml"),
    "utf8",
  );

  [
    "bookVersionText",
    "bookStaleNotice",
    "chapter.metaText",
    "已修改",
    "重新整理一版",
    "原始讲述",
    "小节 ",
    "回到目录与封面",
  ].forEach((editingCopy) => assert.equal(template.includes(editingCopy), false));
  assert.match(template, /readerUpdateNotice/);
  assert.match(template, /回到目录/);
});

test("book reader keeps flat chapter content as a historical-version fallback", async () => {
  const manuscript = sampleBookManuscript(
    "外婆回忆录",
    "classic_plain",
    { chapterCount: 1 },
  );
  manuscript.table_of_contents[0].sections = [];
  manuscript.chapters[0].sections = [];
  const { page } = loadBookReaderPage({
    getMemoirBookOutput: async () => sampleBookOutput(
      1,
      "旧版扁平正文",
      false,
      [],
      [],
      manuscript,
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
      versions: [
        {
          id: historical.id,
          version: historical.version,
          created_by: historical.created_by,
          created_at: historical.created_at,
        },
        ...(latest.versions || []),
      ],
    }),
    getMemoirBookOutputVersion: async () => historical,
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project/1",
    versionId: "book-output-version-history",
  });

  assert.equal(context.data.versionId, "book-output-version-history");
  assert.equal(context.readerText, "历史版本正文");
  assert.equal(context.data.bookContent, "");
  assert.equal(context.data.bookViewingHistoricalVersion, true);
  assert.match(context.data.bookVersionNotice, /切回最新版/);
});

test("long books send only the current chapter to the view and keep later chapters reachable", async () => {
  const manuscript = sampleBookManuscript();
  const chapterText = "外婆在院子里讲故事。".repeat(1000);
  manuscript.chapters = Array.from({ length: 10 }, (_, index) => ({
    ...manuscript.chapters[0],
    chapter_id: `chapter-${index + 1}`,
    index: index + 1,
    title: `第${index + 1}章`,
    content: chapterText,
    sections: [{
      ...manuscript.chapters[0].sections[0],
      section_id: `section-${index + 1}`,
      content: chapterText,
    }],
  }));
  manuscript.table_of_contents = manuscript.chapters.map((chapter) => ({
    chapter_id: chapter.chapter_id,
    index: chapter.index,
    title: chapter.title,
    char_count: chapterText.length,
    source_count: 1,
    sections: [{ section_id: chapter.sections[0].section_id, index: 1, title: "故事", char_count: chapterText.length, source_count: 1 }],
  }));
  const { page } = loadBookReaderPage({
    getMemoirBookOutput: async () => sampleBookOutput(1, chapterText.repeat(10), false, [], [], manuscript),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project/1" });

  assert.equal(context.data.readerChapterTotal, 10);
  assert.equal(context.readerText.length, chapterText.length * 10);
  assert.ok(Buffer.byteLength(JSON.stringify(context.data)) < 100_000);
  global.wx = { pageScrollTo() {} };
  page.showReaderChapter.call(context, 9);
  delete global.wx;
  assert.equal(context.data.readerChapterIndex, 9);
  assert.equal(context.data.bookPreviewChapters[0].sections[0].paragraphs[0], chapterText);
});

test("flat books paginate without losing Unicode text", async () => {
  const content = "回忆🌿".repeat(15_000);
  const { page } = loadBookReaderPage({
    getMemoirBookOutput: async () => sampleBookOutput(1, content),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project/1" });

  assert.ok(context.data.readerChapterTotal > 1);
  assert.ok(Array.from(context.data.bookContent).length <= 6000);
  global.wx = { pageScrollTo() {} };
  page.showReaderChapter.call(context, context.data.readerChapterTotal - 1);
  delete global.wx;
  assert.equal(context.flatPages.join(""), content);
  assert.equal(context.data.bookContent, context.flatPages.at(-1));
});

for (const mode of ["book", "chapter"]) test(`${mode} reader uses the shared plan and saved settings`, async () => {
  const requests = [], played = [];
  const { page } = loadBookReaderPage({
    prepareMemoirAudio: async (...args) => { requests.push(args); return sampleAudioPlan(); },
  }, { readBookTTSRequest: () => ({ voice: "voice-1", speed_ratio: 1.25 }) });
  const context = pageContext(page, { readerMode: mode, projectId: "project-1", chapterId: "chapter-1" });
  context.readerText = "正文内容";
  context.readerAudioVersionId = "version-1";
  context.audioPlayback.playFile = async (id) => { played.push([id, context.data.playingTarget]); };
  await page.playReaderAudio.call(context);
  assert.deepEqual(requests, [["project-1", mode === "book"
    ? { kind: "book", versionId: "version-1" }
    : { kind: "chapter", chapterId: "chapter-1", versionId: "version-1" },
    { voice: "voice-1", speed_ratio: 1.25, start_index: 0, retry_failed: true }]]);
  assert.deepEqual(played, [["audio-1", mode]]);
  assert.equal(context.data.bookTTSProgressPercent, 100);
  assert.equal(context.data.playbackError, "");
});

test("book reader opens the full text without starting narration", async () => {
  const { page } = loadBookReaderPage();
  const context = pageContext(page);
  let starts = 0;
  context.playReaderAudio = async () => { starts += 1; };

  await page.onLoad.call(context, { projectId: "project/1" });

  assert.equal(context.data.loading, false);
  assert.equal(context.readerText, "全书正文");
  assert.equal(starts, 0);
});

test("book reader does not mismatch latest audio with historical text", async () => {
  let requestCount = 0;
  const { page } = loadBookReaderPage({
    prepareMemoirAudio: async () => {
      requestCount += 1;
      throw new Error("should not run");
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    bookContent: "历史版本正文",
    bookViewingHistoricalVersion: true,
  });
  context.readerText = "历史版本正文";

  await page.playReaderAudio.call(context);

  assert.equal(requestCount, 0);
  assert.match(context.data.playbackError, /历史版本/);
});

test("book reader keeps the full text visible when narration fails", async () => {
  const { page } = loadBookReaderPage({
    prepareMemoirAudio: async () => {
      throw new Error("朗读服务暂时不可用");
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    bookContent: "仍然可以阅读的全书正文",
    bookViewingHistoricalVersion: false,
  });
  context.readerText = "仍然可以阅读的全书正文";

  await page.playReaderAudio.call(context);

  assert.equal(context.data.error, "");
  assert.equal(context.data.bookContent, "仍然可以阅读的全书正文");
  assert.equal(context.data.playbackError, "朗读服务暂时不可用");
});


test("friend-view preview uses the private projection, keeps text if an image fails, and never forwards it", async () => {
  global.wx = { getStorageSync: () => "", hideShareMenu() {} };
  mockModule("../services/authenticated-media.ts", { downloadAuthenticatedFile: async () => { throw new Error("image unavailable"); } });
  const calls = [];
  const output = sampleFinishedBook("分享正文");
  output.share_scope = "chapter";
  output.latest_version.manuscript.chapters[0].illustrations = [{ image_asset_id: "photo", image_url: "/api/mobile/memoir/projects/p/image-assets/photo/file", section_id: "section-1", caption: "老照片", disclosure: "" }];
  const { page } = loadBookReaderPage({ previewMemoirBookShare: async (...args) => { calls.push(args); return output; } });
  const context = pageContext(page);
  await context.onLoad({ projectId: "p", chapterId: "c", sharePreview: "1" });
  assert.deepEqual(calls, [["p", "c", undefined]]);
  assert.equal(context.data.error, "");
  assert.equal(context.readerText, "分享正文");
  assert.equal(context.data.readerMode, "chapter");
  assert.equal(context.data.shareByline, "家人整理");
  assert.equal(context.data.shareImages.length, 0);
  assert.equal(context.data.shareLikeVisible, false);
  assert.equal(context.onShareAppMessage().path, "/pages/home/index?from=share_home");
});

test("slow optional preview images do not keep the saved text behind loading", async () => {
  let finishImage;
  global.wx = { getStorageSync: () => "", hideShareMenu() {} };
  mockModule("../services/authenticated-media.ts", {
    downloadAuthenticatedFile: () => new Promise(resolve => { finishImage = resolve; }),
  });
  const output = sampleFinishedBook("先显示这段正文");
  output.share_scope = "chapter";
  output.latest_version.manuscript.chapters[0].illustrations = [{
    image_asset_id: "photo", image_url: "/api/mobile/memoir/projects/p/image-assets/photo/file",
    section_id: "section-1", caption: "老照片", disclosure: "",
  }];
  const { page } = loadBookReaderPage({ previewMemoirBookShare: async () => output });
  const context = pageContext(page);
  const pending = context.onLoad({ projectId: "p", chapterId: "c", sharePreview: "1" });
  await flushAsyncWork();
  assert.equal(context.data.loading, false);
  assert.equal(context.readerText, "先显示这段正文");
  finishImage("file://preview-image");
  await pending;
});

test("shared book displays its saved front matter and forwards the same scope", async () => {
  const { page } = loadBookReaderPage({
    readMemoirBookShare: async () => sampleFinishedBook(),
    readMemoirShareLikes: async () => ({ count: 0, likers: [], liked: false, can_like: true }),
  });
  const context = pageContext(page);
  await context.onLoad({ shareToken: "same-token" });
  assert.equal(context.data.sharePreface, "这些故事来自家里的讲述。");
  assert.equal(context.data.shareAfterword, "这些故事还可以继续补充。");
  assert.equal(context.onShareAppMessage().path, "/pages/book-reader/index?shareToken=same-token");
});


test("retrying a failed share restores both text and the like target", async () => {
  let fail = true;
  const targets = [];
  const { page } = loadBookReaderPage({
    readMemoirBookShare: async () => { if (fail) throw new Error("network"); return sampleFinishedBook(); },
    readMemoirShareLikes: async target => { targets.push(target); return { count: 0, likers: [], liked: false, can_like: true }; },
  });
  const context = pageContext(page);
  await context.onLoad({ shareToken: "retry-token" });
  assert.equal(context.data.shareRetryable, true);
  fail = false;
  await context.retrySharedReading();
  assert.equal(context.data.error, "");
  assert.deepEqual(targets, [{ reading_token: "retry-token" }]);
  assert.equal(context.data.shareLikeVisible, true);
});

test("the sample book retry reloads its public source", async () => {
  let fail = true;
  const { page } = loadBookReaderPage({ readMemoirDemoBook: async () => { if (fail) throw new Error("network"); return sampleFinishedBook(); } });
  const context = pageContext(page);
  await context.onLoad({ demo: "1" });
  fail = false;
  await context.retrySharedReading();
  assert.equal(context.data.error, "");
  assert.equal(context.readerText, "全书正文");
});

test("shared reading restores its chapter and scroll position, then resets obsolete audio after a new version", async () => {
  const storage = {};
  const scrolls = [];
  global.wx = { getStorageSync: key => storage[key], setStorageSync: (key, value) => { storage[key] = value; }, pageScrollTo: options => scrolls.push(options) };
  let output = sampleFinishedBook();
  const { page } = loadBookReaderPage({
    readMemoirBookShare: async () => output,
    readMemoirShareLikes: async () => ({ count: 0, likers: [], liked: false, can_like: true }),
  });
  const first = pageContext(page);
  await first.onLoad({ shareToken: "family" });
  first.showReaderChapter(1);
  first.onPageScroll({ scrollTop: 456 });
  first.readingProgress = { versionId: output.latest_version.id, audioChapterId: "chapter-2", audioHash: "old-hash", audioIndex: 3, audioSeconds: 12 };
  first.onHide();
  const reopened = pageContext(page);
  await reopened.onLoad({ shareToken: "family" });
  assert.equal(reopened.data.readerChapterIndex, 1);
  assert.equal(reopened.readingScrollTop, 456);
  assert.equal(reopened.readingProgress.audioIndex, 3);
  output = JSON.parse(JSON.stringify(output));
  output.latest_version.id = "updated-version";
  const updated = pageContext(page);
  await updated.onLoad({ shareToken: "family" });
  assert.equal(updated.data.readerChapterIndex, 1);
  assert.equal(updated.readingScrollTop, 0);
  assert.equal(updated.readingProgress.audioHash, "");
  assert.equal(scrolls.at(-1).scrollTop, 0);
  delete global.wx;
});

test("shared chapter playback uses only the public plan and stops a late response after leaving", async () => {
  const { flushAsyncWork } = require("./page-harness");
  let resolvePlan;
  const calls = [];
  const output = sampleFinishedBook();
  const { page } = loadBookReaderPage({
    readMemoirBookShare: async () => output,
    readMemoirShareLikes: async () => ({ count: 0, likers: [], liked: false, can_like: true }),
    prepareMemoirAudio: unmockedAsync("private author generation"),
    readMemoirSharedAudioPlan: (...args) => { calls.push(args); return new Promise(resolve => { resolvePlan = resolve; }); },
    downloadMemoirSharedAudio: unmockedAsync("download after hiding"),
  });
  const context = pageContext(page);
  await context.onLoad({ shareToken: "public-link" });
  context.showReaderChapter(1);
  const playing = context.playReaderAudio();
  await flushAsyncWork();
  assert.deepEqual(calls, [["public-link", output.latest_version.id, context.bookChapters[1].chapterId]]);
  context.onHide();
  resolvePlan({ ...sampleAudioPlan(), content: "本章的声音" });
  await playing;
  assert.equal(context.data.playingTarget, "");
  assert.equal(context.data.ttsBusyTarget, "");
});

test("returning from login switches reading progress to that visitor without overwriting guest progress", async () => {
  const storage = {};
  global.wx = { getStorageSync: key => storage[key], setStorageSync: (key, value) => { storage[key] = value; }, pageScrollTo() {} };
  const { page } = loadBookReaderPage({ readMemoirBookShare: async () => sampleFinishedBook(),
    readMemoirShareLikes: async () => ({ count: 0, likers: [], liked: false, can_like: true }) });
  const context = pageContext(page);
  await context.onLoad({ shareToken: "family" });
  const guestKey = context.readingProgressKey;
  context.showReaderChapter(1);
  context.onPageScroll({ scrollTop: 80 });
  context.onHide();
  storage.pawtale_backend_cookie = "member-session";
  storage.pawtale_memoir_session = { cache_identity: { user_id: "member" } };
  context.onShow();
  assert.notEqual(context.readingProgressKey, guestKey);
  assert.equal(context.data.readerChapterIndex, 0);
  assert.equal(context.readingScrollTop, 0);
  context.onPageScroll({ scrollTop: 220 });
  context.onHide();
  const { readSharedReadingProgress } = require("../stores/reading-progress.ts");
  assert.equal(readSharedReadingProgress(guestKey).scrollTop, 80);
  assert.equal(readSharedReadingProgress(context.readingProgressKey).scrollTop, 220);
  delete global.wx;
});


test("shared narration generates only after play and resumes at the saved segment", async () => {
  const requests = [], played = [];
  const initial = sampleAudioPlan();
  initial.segments = Array.from({ length: 4 }, (_, index) => ({
    ...initial.segments[0], index, status: "idle", audio_file_id: null,
  }));
  const { page } = loadBookReaderPage({
    readMemoirBookShare: async () => sampleFinishedBook(),
    readMemoirShareLikes: async () => ({ count: 0, likers: [], liked: false, can_like: true }),
    readMemoirSharedAudioPlan: async (_token, _version, _chapter, playback) => {
      requests.push(playback);
      const plan = JSON.parse(JSON.stringify(initial));
      if (playback) {
        plan.segments[playback.start_index].status = "succeeded";
        plan.segments[playback.start_index].audio_file_id = `audio-${playback.start_index}`;
      }
      return { ...plan, content: "分享正文" };
    },
  });
  const context = pageContext(page);
  await context.onLoad({ shareToken: "family" });
  assert.deepEqual(requests, []);
  context.readingProgress = { versionId: context.readerAudioVersionId, audioHash: initial.source_text_hash,
    audioChapterId: context.bookChapters[0].chapterId, audioIndex: 2, audioSeconds: 0 };
  context.audioPlayback.playFile = async (id) => {
    played.push(id);
    context.onHide();
  };
  context.audioPlayback.prefetchFile = async () => {};
  await context.playReaderAudio();
  assert.equal(requests[0], undefined);
  assert.deepEqual(requests.slice(1).map(r => r.start_index), [2, 3]);
  assert.ok(requests.slice(1).every(r => r.generate === true && r.source_text_hash === initial.source_text_hash
    && r.profile_key === initial.profile_key));
  assert.deepEqual(played, ["audio-2"]);
});

for (const shared of [false, true]) test(`${shared ? "shared" : "owner"} chapter shows images before, within and after unchanged text`, async () => {
  global.wx = { getStorageSync: () => "", hideShareMenu() {}, showShareMenu() {} };
  const downloads = [];
  mockModule("../services/authenticated-media.ts", { downloadAuthenticatedFile: async url => { downloads.push(url); return `local:${url}`; } });
  const content = "开头。\n\n中间。\n\n结尾。";
  const illustrations = ["before", "middle", "after"].map(position => ({
    image_asset_id: position, image_url: `/api/mobile/memoir/projects/p/image-assets/${position}/file?variant=reading`,
    placement: "chapter_inline", section_id: "section-1", inline_position: position, caption: "", disclosure: "AI 插图",
  }));
  const output = sampleFinishedBook(content);
  output.share_scope = "chapter";
  output.latest_version.manuscript.chapters = [{ ...output.latest_version.manuscript.chapters[0], chapter_id: "chapter-1",
    content, sections: [{ section_id: "section-1", title: "故事", content }], illustrations }];
  const { page } = loadBookReaderPage({
    readMemoirBookShare: async () => output,
    getMemoirChapterWorkspace: async () => ({ chapter_id: "chapter-1", chapter_title: "往事", draft_content: content,
      draft_sections: [{ section_id: "section-1", title: "故事", content }], illustrations }),
  });
  const context = pageContext(page);
  await context.onLoad(shared ? { shareToken: "family" } : { projectId: "p", chapterId: "chapter-1" });
  assert.deepEqual(context.data.bookPreviewChapters[0].sections[0].paragraphs, ["开头。", "中间。", "结尾。"]);
  assert.deepEqual(context.data.shareImages.map(image => image.paragraphBefore), [0, 2, 3]);
  assert.equal(context.data.shareImages.every(image => image.chapterId === "chapter-1" && image.sectionId === "section-1" && image.url), true);
  assert.equal(downloads.length, shared ? 0 : 3);
  for (const image of context.data.shareImages) {
    context.onReadingImageLoad({ currentTarget: { dataset: { id: image.id, url: image.url } }, detail: { width: 600, height: 900 } });
  }
  assert.deepEqual(context.data.shareImages.map(image => image.paragraphBefore), [0, 1, 2]);
  assert.equal(context.data.shareImages.every(image => image.portrait), true);
  assert.deepEqual(context.data.bookPreviewChapters[0].sections[0].paragraphs, ["开头。", "中间。", "结尾。"]);
  const image = context.data.shareImages[2];
  context.onReadingImageLoad({ currentTarget: { dataset: { id: image.id, url: image.url } }, detail: { width: 900, height: 600 } });
  assert.equal(context.data.shareImages[2].portrait, false);
  assert.equal(context.data.shareImages[2].paragraphBefore, 3);
});

function excerptContext(api = {}) {
  global.wx = { getStorageSync: () => "", setStorageSync() {}, hideShareMenu() {}, showShareMenu() {}, setNavigationBarTitle() {} };
  const { page } = loadBookReaderPage(api);
  const context = pageContext(page);
  context.loadShareLikes = async (target) => { context.likeTarget = target; };
  return context;
}

test("excerpt selection previews only the chosen text and forwards the same immutable card", async () => {
  let request;
  const source = "一段开头。\n奶奶坐在门口。\n不想分享的正文。";
  const context = excerptContext({
    getMemoirChapterWorkspace: async () => ({ chapter_id: "c", chapter_title: "第一章", draft_version_id: "v1", draft_content: source, draft_sections: [] }),
    createMemoirExcerptShare: async (...args) => { request = args; return { token: "x1.token", content: args[2].content, book_title: "回忆录", chapter_title: "第一章" }; },
  });
  await context.onLoad({ projectId: "p", chapterId: "c", excerptSelect: "1" });
  assert.equal(context.data.excerptMode, "select");
  assert.equal(context.excerptSource, source);
  context.excerptEditor = { getSelectionText: ({ success }) => success({ text: "奶奶坐在门口。" }) };
  await context.previewExcerptSelection();
  assert.deepEqual(request, ["p", "c", { version_id: "v1", content: "奶奶坐在门口。" }]);
  assert.equal(context.data.excerptMode, "preview");
  assert.equal(context.data.excerptContent, "奶奶坐在门口。");
  assert.equal(context.data.excerptImageReady, false);
  assert.ok(!context.onShareAppMessage().path.includes("x1.token"));
  context.onExcerptImageLoad({ currentTarget: { dataset: { url: context.data.excerptImageUrl } } });
  const payload = context.onShareAppMessage();
  assert.equal(payload.title, "奶奶坐在门口。");
  assert.equal(payload.path, "/pages/book-reader/index?excerptToken=x1.token");
  assert.match(payload.imageUrl, /\/excerpt-shares\/x1.token\/card.jpg$/);
  context.onHide();
  assert.deepEqual(context.onShareAppMessage(), payload);
});

test("excerpt recipient reads only the snapshot and uses its token for the chapter likes", async () => {
  const context = excerptContext({
    readMemoirExcerptShare: async () => ({ book_title: "原书名", chapter_title: "原章名", content: "只公开这句话。" }),
    readMemoirBookShare: async () => assert.fail("must not load a whole chapter"),
  });
  await context.onLoad({ excerptToken: "x1.token", projectId: "ignored", chapterId: "ignored" });
  assert.equal(context.data.excerptMode, "shared");
  assert.equal(context.data.excerptContent, "只公开这句话。");
  assert.equal(context.readerText, "");
  assert.equal(context.data.bookPreviewChapters.length, 0);
  assert.equal(context.data.responseToken, "");
  assert.deepEqual(context.likeTarget, { reading_token: "x1.token" });
  assert.equal(context.onShareAppMessage().path, "/pages/book-reader/index?excerptToken=x1.token");
  assert.ok(!context.onShareAppMessage().path.includes("chapterId"));
});

test("excerpt selection rejects empty, overlong and edited text before creating a share", async () => {
  let selection = "";
  const context = excerptContext({ createMemoirExcerptShare: async () => assert.fail("invalid selection") });
  context.excerptSource = "有效原文。";
  context.setData({ excerptMode: "select" });
  context.excerptEditor = { getSelectionText: ({ success }) => success({ text: selection }) };
  for (selection of ["", "字".repeat(513), "篡改文字"]) {
    await context.previewExcerptSelection();
    assert.ok(context.data.error);
    assert.equal(context.excerptToken, "");
    assert.equal(context.data.excerptBusy, false);
  }
});

for (const leave of ["onHide", "onUnload"]) {
  test(`${leave} discards pending excerpt selection and does not publish its result`, async () => {
    let complete;
    const context = excerptContext({ createMemoirExcerptShare: async () => assert.fail("left before selection completed") });
    context.excerptSource = "真实原文。";
    context.setData({ excerptMode: "select" });
    context.excerptEditor = { getSelectionText: ({ success }) => { complete = success; } };
    const pending = context.previewExcerptSelection();
    context[leave]();
    complete({ text: "真实原文。" });
    await pending;
    assert.equal(context.excerptToken, "");
    assert.equal(context.data.excerptBusy, false);
    assert.equal(context.data.excerptMode, "select");
  });
}

test("excerpt viewer has no whole-reading, audio or response entry points", () => {
  const template = fs.readFileSync(path.join(__dirname, "../pages/book-reader/index.wxml"), "utf8");
  const excerptBranch = template.split('<view wx:else class="ui-page-shell')[0];
  assert.match(template, /read-only="\{\{true\}\}"/);
  assert.match(template, /catchtouchend="previewExcerptSelection"/);
  assert.ok(!/openReaderChapter|playReaderAudio|share-response-form|分享整章|阅读整章/.test(excerptBranch));
});

test("excerpt editor readiness ignores missing or detached nodes after page exit", () => {
  const context = excerptContext();
  let complete;
  const query = { select: () => query, context: callback => { complete = callback; return query; }, exec() {} };
  context.createSelectorQuery = () => query;
  context.setData({ excerptMode: "select" });
  context.onExcerptEditorReady();
  assert.doesNotThrow(() => complete(null));
  context.onExcerptEditorReady();
  context.onUnload();
  complete({ context: { setContents: () => assert.fail("detached editor") } });
  assert.equal(context.excerptEditor, null);
});

for (const timing of ["early preview", "hidden callback", "late callback after return"]) {
  test(`excerpt editor initialization recovers from ${timing}`, async () => {
    const source = "奶奶坐在门口。";
    const context = excerptContext({
      getMemoirChapterWorkspace: async () => ({ chapter_id: "c", chapter_title: "第一章", draft_version_id: "v1", draft_content: source, draft_sections: [] }),
      createMemoirExcerptShare: async (_project, _chapter, { content }) => ({ token: "x1.token", content, book_title: "回忆录", chapter_title: "第一章" }),
    });
    const callbacks = [];
    const query = { select: () => query, context: callback => { callbacks.push(callback); return query; }, exec() {} };
    context.createSelectorQuery = () => query;
    let displayed = "";
    const editor = {
      setContents: ({ delta }) => { displayed = delta.ops[0].insert; },
      getSelectionText: ({ success }) => success({ text: source }),
    };
    await context.onLoad({ projectId: "p", chapterId: "c", excerptSelect: "1" });
    context.onExcerptEditorReady();
    const originalCallback = callbacks.shift();
    if (timing === "early preview") {
      await context.previewExcerptSelection();
      assert.equal(context.data.excerptMode, "select");
      originalCallback({ context: editor });
    } else {
      context.onHide();
      if (timing === "hidden callback") originalCallback({ context: editor });
      assert.equal(displayed, "");
      context.onShow();
      assert.equal(callbacks.length, 1, "returning reinitializes the existing editor");
      callbacks.shift()({ context: editor });
      if (timing === "late callback after return") {
        originalCallback({ context: { setContents: () => assert.fail("old hidden callback") } });
      }
    }
    assert.equal(displayed, source + "\n");
    assert.equal(context.data.error, "");
    context.onHide();
    context.onShow();
    assert.equal(callbacks.length, 0, "ready editor keeps its current selection on return");
    await context.previewExcerptSelection();
    assert.equal(context.data.excerptMode, "preview");
    assert.equal(context.data.excerptContent, source);
  });
}

test("excerpt version conflict requires reloading source before another selection", async () => {
  const context = excerptContext({
    createMemoirExcerptShare: async () => { throw new ApiError(409, "正文已更新，请重新选择分享内容", {}); },
    getMemoirChapterWorkspace: async () => ({ chapter_id: "c", chapter_title: "新版章节", draft_version_id: "v2", draft_content: "新的原文。", draft_sections: [] }),
  });
  context.excerptSource = "原来的文字。";
  context.setData({ excerptMode: "select", projectId: "p", chapterId: "c" });
  context.excerptEditor = { getSelectionText: ({ success }) => success({ text: "原来的文字。" }) };
  await context.previewExcerptSelection();
  assert.equal(context.data.excerptReloadRequired, true);
  assert.equal(context.excerptToken, "");
  context.retryExcerpt();
  await flushAsyncWork();
  assert.equal(context.data.excerptReloadRequired, false);
  assert.equal(context.excerptVersionId, "v2");
  assert.equal(context.excerptSource, "新的原文。");
});


test("selected-text entry reuses the chapter preview loader and presentation", async () => {
  let loads = 0;
  const workspace = { chapter_id: "c", chapter_title: "放学的路", draft_version_id: "v1", draft_content: "回家的路很长。", draft_sections: [] };
  const context = excerptContext({
    getMemoirChapterWorkspace: async () => { loads++; return workspace; },
    previewMemoirBookShare: async () => assert.fail("selection must use the chapter reader"),
  });
  await context.onLoad({ projectId: "p", chapterId: "c" });
  const original = { title: context.data.readerTitle, chapters: context.data.bookPreviewChapters, subtitle: context.data.readerSubtitle };
  await context.onLoad({ projectId: "p", chapterId: "c", excerptSelect: "1" });
  assert.equal(loads, 2);
  assert.equal(context.data.readerMode, "chapter");
  assert.deepEqual({ title: context.data.readerTitle, chapters: context.data.bookPreviewChapters, subtitle: context.data.readerSubtitle }, original);
  assert.equal(context.excerptSource, workspace.draft_content);
  assert.equal(context.excerptVersionId, "v1");
});

test("512 Unicode characters remain complete in selected-text preview and recipient while the card title is shortened", async () => {
  const content = "回忆🌿。".repeat(128);
  let posted;
  const snapshot = { token: "x1.long", content, book_title: "回忆录", chapter_title: "童年" };
  const context = excerptContext({
    createMemoirExcerptShare: async (_p, _c, selection) => { posted = selection.content; return snapshot; },
    readMemoirExcerptShare: async () => snapshot,
  });
  context.excerptSource = content;
  context.setData({ excerptMode: "select" });
  context.excerptEditor = { getSelectionText: ({ success }) => success({ text: content }) };
  await context.previewExcerptSelection();
  assert.equal(context.data.excerptSelectionCount, 512);
  assert.equal(posted, content);
  assert.equal(context.data.excerptContent, content);
  context.onExcerptImageLoad({ currentTarget: { dataset: { url: context.data.excerptImageUrl } } });
  assert.equal(context.onShareAppMessage().title, Array.from(content).slice(0, 80).join("") + "…");
  await context.onLoad({ excerptToken: snapshot.token });
  assert.equal(context.data.excerptContent, content);
  assert.equal(context.data.excerptBeforeHighlight + context.data.excerptHighlight, content);
  assert.deepEqual(context.likeTarget, { reading_token: snapshot.token });
});

test("returning to selection retries a hidden chapter load without letting the old result overwrite it", async () => {
  let finishOld;
  let calls = 0;
  const workspace = { chapter_id: "c", chapter_title: "新稿", draft_version_id: "v2", draft_content: "新原文", draft_sections: [] };
  const context = excerptContext({ getMemoirChapterWorkspace: () => ++calls === 1
    ? new Promise(resolve => { finishOld = resolve; }) : Promise.resolve(workspace) });
  const first = context.onLoad({ projectId: "p", chapterId: "c", excerptSelect: "1" });
  context.onHide();
  context.onShow();
  await flushAsyncWork();
  finishOld({ ...workspace, draft_version_id: "v1", draft_content: "旧原文" });
  await first;
  assert.equal(context.excerptSource, "新原文");
  assert.equal(context.excerptVersionId, "v2");
  assert.equal(context.data.loading, false);
});
