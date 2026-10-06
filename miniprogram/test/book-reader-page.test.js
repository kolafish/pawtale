require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  mockMemoirApiModules,
  loadPageModule,
  mockModule,
  pageContext,
  unmockedAsync,
} = require("./page-harness");

const {
  sampleBookManuscript,
  sampleBookOutput,
} = require("./book-fixtures");
const { readingParagraphs } = require("../pages/book/book-view-model.ts");

function sampleFinishedBook(content = "全书正文") {
  return sampleBookOutput(1, content, false, [], [], sampleBookManuscript());
}

function loadBookReaderPage(voiceApiExports = {}, ttsExports = {}) {
  mockMemoirApiModules({
    createMemoirBookChapterDraftTTS: unmockedAsync("createMemoirBookChapterDraftTTS"),
    createMemoirBookOutputTTS: unmockedAsync("createMemoirBookOutputTTS"),
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
  assert.equal(context.data.bookPreviewTocItems[0].storyCountText, "2个故事");
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
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { shareToken: "family-token" });

  assert.deepEqual(calls, ["family-token"]);
  assert.equal(context.data.readerShared, true);
  assert.equal(context.data.projectId, "");
  assert.equal(context.readerText, "只读分享正文");
  assert.equal(context.data.bookPreviewChapters.length, 1);
  assert.equal(context.data.error, "");
  await page.playReaderAudio.call(context);
  assert.equal(context.data.ttsBusyTarget, "");
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
    assert.equal(page.onShareAppMessage, undefined);
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
      storyCountText: "1个故事",
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

test("book reader presents chapter updates as reading context", async () => {
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

  assert.equal(context.data.readerUpdateNotice, "《工作与创业经历》等章节刚刚更新");
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

test("book reader reads the latest full book with saved book voice settings", async () => {
  const requests = [];
  const playCalls = [];
  const { page } = loadBookReaderPage({
    createMemoirBookOutputTTS: async (...args) => {
      requests.push(args);
      return {
        audio_file_ids: ["audio-book-1"],
        audio_file_durations_seconds: [1.2],
        provider: "fake",
        model: "fake-tts",
        mime_type: "audio/mpeg",
        chunk_count: 1,
        source_text_hash: "hash",
        duration_seconds: 1.2,
        alignment: [],
      };
    },
  }, {
    readBookTTSRequest: () => ({
      voice: "zh_male_m191_uranus_bigtts",
      speed_ratio: 1.25,
    }),
  });
  const context = pageContext(page, {
    projectId: "project/1",
    bookContent: "外婆回忆录正文",
    bookViewingHistoricalVersion: false,
  });
  context.readerText = "外婆回忆录正文";
  context.playAudioFiles = async (...args) => {
    playCalls.push(args);
  };

  await page.playReaderAudio.call(context);

  assert.deepEqual(requests, [[
    "project/1",
    {
      voice: "zh_male_m191_uranus_bigtts",
      speed_ratio: 1.25,
    },
  ]]);
  assert.equal(playCalls.length, 1);
  assert.equal(playCalls[0][1], "book");
  assert.equal(playCalls[0][3], "外婆回忆录正文");
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
    createMemoirBookOutputTTS: async () => {
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
    createMemoirBookOutputTTS: async () => {
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

test("chapter reader uses the existing chapter TTS and progress pipeline", async () => {
  const requests = [];
  const playCalls = [];
  const { page } = loadBookReaderPage({
    createMemoirBookChapterDraftTTS: async (...args) => {
      requests.push(args);
      return {
        audio_file_ids: ["audio-chapter-1"],
        audio_file_durations_seconds: [1.2],
        provider: "fake",
        model: "fake-tts",
        mime_type: "audio/mpeg",
        chunk_count: 1,
        source_text_hash: "hash",
        duration_seconds: 1.2,
        alignment: [],
      };
    },
  }, {
    readBookTTSRequest: () => ({ voice: "voice-1", speed_ratio: 1.25 }),
  });
  const context = pageContext(page, {
    readerMode: "chapter",
    projectId: "project-1",
    chapterId: "chapter-1",
    bookContent: "第一小节\n\n整章正文。",
  });
  context.readerText = "第一小节\n\n整章正文。";
  context.playAudioFiles = async (...args) => {
    playCalls.push(args);
  };

  await page.playReaderAudio.call(context);

  assert.deepEqual(requests, [[
    "project-1",
    "chapter-1",
    { voice: "voice-1", speed_ratio: 1.25 },
  ]]);
  assert.equal(playCalls[0][1], "chapter");
  assert.equal(playCalls[0][3], "第一小节\n\n整章正文。");
});
