require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { mockMemoirApiModules, loadPageModule, mockModule, pageContext } = require("./page-harness");

const {
  sampleAutomationEvent,
  sampleBookManuscript: baseSampleBookManuscript,
  sampleBookOutput: baseSampleBookOutput,
  sampleBookOutputVersion: baseSampleBookOutputVersion,
  samplePlan: baseSamplePlan,
} = require("./book-fixtures");

function samplePlan(status = "confirmed") {
  return baseSamplePlan(status, { chapterCount: 1 });
}

function sampleBookManuscript(title = "外婆回忆录", coverStyle = "classic_plain") {
  return baseSampleBookManuscript(title, coverStyle, { chapterCount: 1 });
}

function sampleBookOutputVersion(
  version,
  content,
  createdAt = "2026-06-17T00:00:00.000Z",
  manuscript = sampleBookManuscript(),
) {
  return baseSampleBookOutputVersion(
    version,
    content,
    createdAt,
    manuscript,
  );
}

function sampleBookOutput() {
  const latestVersion = sampleBookOutputVersion(2, "全书第二版正文");
  const historicalVersion = sampleBookOutputVersion(
    1,
    "全书第一版正文",
    "2026-06-16T00:00:00.000Z",
  );
  return {
    ...baseSampleBookOutput(2, "全书第二版正文", false, [], [
      historicalVersion,
      latestVersion,
    ].map(({ id, version, created_by, created_at }) => ({
      id,
      version,
      created_by,
      created_at,
    })), latestVersion.manuscript),
    latest_version: latestVersion,
  };
}

function sampleEditorialJob(target, status = "pending", patch = {}) {
  return {
    target,
    job_id: `job-${target}`,
    status,
    error_message: "",
    updated_at: "2026-06-17T00:01:00.000Z",
    ...patch,
  };
}

function loadBookExportPage(voiceApiExports = {}) {
  mockMemoirApiModules({
    getMemoirBookPlan: async () => samplePlan(),
    getMemoirBookOutput: async () => sampleBookOutput(),
    getMemoirDraftProgress: async () => ({ status: "ready", needs_user_material_count: 0 }),
    getMemoirBookOutputVersion: async () => sampleBookOutputVersion(1, "全书第一版正文"),
    downloadMemoirBookOutputExport: async () => "/tmp/book.pdf",
    suggestMemoirBookOutputTitles: async () => ({ suggestions: [] }),
    assembleMemoirBookOutput: async () => undefined,
    updateMemoirBookOutput: async () => sampleBookOutput(),
    enqueueMemoirBookOutputEditorialCopyGeneration: async () => sampleAutomationEvent({
      id: "event-1",
      event_type: "book_editorial_copy_generated",
      chapter_id: null,
      summary_text: "后记已提交后台重生成。",
      metadata_json: { target_label: "后记", targets: ["afterword"] },
    }),
    getMemoirBookEditorialCopyJobs: async () => ({ items: [] }),
    ...voiceApiExports,
  });
  return loadPageModule("../pages/book-export/index.ts");
}

test("book export page is registered", () => {
  const appJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../app.json"), "utf8"));
  const pageJson = JSON.parse(fs.readFileSync(path.join(__dirname, "../pages/book-export/index.json"), "utf8"));

  assert.ok(appJson.pages.includes("pages/book-export/index"));
  assert.equal(pageJson.navigationBarTitleText, "导出文件");
});

test("book export overview binds one reading entry and focused settings without duplicating the book", () => {
  const template = fs.readFileSync(path.join(__dirname, "../pages/book-export/index.wxml"), "utf8");

  assert.match(template, /bindtap="openBookReader"/);
  assert.doesNotMatch(template, /bindtap="openBookNarration"/);
  assert.match(template, /data-section="title" bindtap="openSection"/);
  assert.match(template, /bindtap="openImageAssetsPage"/);
  assert.doesNotMatch(template, /bookPreviewTocItems|bookManuscriptStructureItems|bookManuscriptChapterItems/);
});

test("book design section shows one curated gallery and keeps custom reading controls", () => {
  const template = fs.readFileSync(path.join(__dirname, "../pages/book-export/index.wxml"), "utf8");
  assert.match(template, /data-section="layout" bindtap="openSection"/);
  assert.match(template, /data-template="\{\{option.value\}\}" bindtap="previewBookLayout"/);
  assert.match(template, /data-template="\{\{option.value\}\}" bindtap="selectBookLayout"/);
  assert.match(template, /src="\{\{option.coverPreviewImage\}\}"/);
  assert.match(template, /src="\{\{option.ornamentThumbnail\}\}"/);
  assert.doesNotMatch(template, /bindchange="changeBookCoverStyle"/);
  assert.doesNotMatch(template, /预览整套 PDF|预览阅读版 PDF/);
  assert.match(template, /bookLayoutFeedbackTarget === option.value/);
  assert.match(template, /data-key="font_scale" bindchange="changeBookCustomLayout"/);
  assert.match(template, /data-key="line_spacing" bindchange="changeBookCustomLayout"/);
  assert.match(template, /data-key="page_margin" bindchange="changeBookCustomLayout"/);
});

test("book export page loads plan and output from query project", async () => {
  const calls = [];
  const { page } = loadBookExportPage({
    getMemoirBookPlan: async (projectId) => {
      calls.push(["plan", projectId]);
      return samplePlan();
    },
    getMemoirBookOutput: async (projectId) => {
      calls.push(["output", projectId]);
      return sampleBookOutput();
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project/1", action: "print_pdf" });

  assert.deepEqual(calls, [
    ["plan", "project/1"],
    ["output", "project/1"],
  ]);
  assert.equal(context.data.projectId, "project/1");
  assert.equal(context.data.exportNotice, "打印版字号更大，适合装订保存。");
  assert.equal(context.data.bookContent, "全书第二版正文");
  assert.equal(context.data.bookPreviewChapters.length, 1);
  assert.equal(context.data.bookPreviewChapters[0].sections.length, 2);
  assert.equal(
    context.data.bookPreviewChapters[0].sections[0].excerpt,
    "我小时候常在院子里晒太阳。",
  );
  assert.equal(context.data.bookManuscriptAvailable, true);
  assert.equal(context.data.bookManuscriptTemplateLabel, "素雅书籍版");
  assert.equal(context.data.bookManuscriptStructureItems.some((item) => item.title === "目录"), true);
  assert.equal(context.data.bookManuscriptChapterItems[0].intro, "这一章主要记录外婆小时候在院子里的生活。");
  assert.equal(context.data.bookManuscriptBackMatterItems.some((item) => item.title === "原始讲述索引"), true);
  assert.equal("bookOutput" in context.data, false);
  assert.equal("plan" in context.data, false);
  assert.equal("chapters" in context.data, false);
});

test("book export keeps loaded content visible when suggesting titles fails", async () => {
  const { page } = loadBookExportPage({
    suggestMemoirBookOutputTitles: async () => {
      throw new Error("起名失败");
    },
  });
  const context = pageContext(page);
  await page.onLoad.call(context, { projectId: "project-1" });

  await page.suggestBookTitles.call(context);

  assert.equal(context.data.error, "起名失败");
  assert.equal(context.data.loadError, "");
  assert.equal(context.data.bookContent, "全书第二版正文");
  const template = fs.readFileSync(
    path.join(__dirname, "../pages/book-export/index.wxml"),
    "utf8",
  );
  assert.match(template, /wx:elif="\{\{loadError\}\}"/);
  assert.match(template, /<block wx:else>\s*<view wx:if="\{\{error\}\}"/);
});

test("book export page keeps a production-sized book within the page-data budget", () => {
  const output = sampleBookOutput();
  const chapterBody = "那年院子里的桂花开了，我们围在桌边听外婆慢慢讲从前。".repeat(200);
  const chapters = Array.from({ length: 8 }, (_, index) => ({
    ...output.latest_version.manuscript.chapters[0],
    chapter_id: `chapter-${index + 1}`,
    index: index + 1,
    title: `第 ${index + 1} 段往事`,
    content: chapterBody,
    sections: [],
  }));
  output.latest_version.content = chapters
    .map((chapter) => `第 ${chapter.index} 章 ${chapter.title}\n\n${chapter.content}`)
    .join("\n\n");
  assert.ok(output.latest_version.content.length > 40_000);
  output.latest_version.manuscript.chapters = chapters;
  output.latest_version.manuscript.table_of_contents = chapters.map((chapter) => ({
    chapter_id: chapter.chapter_id,
    index: chapter.index,
    title: chapter.title,
    char_count: chapter.content.length,
    source_count: 1,
    sections: [],
  }));
  output.versions = Array.from({ length: 9 }, (_, index) => ({
    id: `book-output-version-${index + 1}`,
    version: index + 1,
    created_by: "system",
    created_at: `2026-06-${String(index + 1).padStart(2, "0")}T00:00:00.000Z`,
  }));
  const { page } = loadBookExportPage();
  const context = pageContext(page);

  page.applyBookExportData.call(context, samplePlan(), output);

  const pageDataBytes = Buffer.byteLength(JSON.stringify(context.data), "utf8");
  assert.ok(pageDataBytes < 512 * 1024, `Page.data is ${pageDataBytes} bytes`);
  assert.equal("bookOutput" in context.data, false);
  assert.equal("plan" in context.data, false);
  assert.equal("chapters" in context.data, false);
});

test("book export opens book settings when linked from whole-book review", async () => {
  let outputLoads = 0;
  const { page } = loadBookExportPage({
    getMemoirBookPlan: async () => samplePlan(),
    getMemoirBookOutput: async () => {
      outputLoads += 1;
      return sampleBookOutput();
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    action: "preview",
    settings: "book",
  });

  assert.equal(context.data.pagePurpose, "settings");
  assert.equal(context.data.activeSection, "");
  assert.equal(outputLoads, 1);
});

test("book export refreshes when a completed editorial job is newer than the loaded output", async () => {
  const output = sampleBookOutput();
  output.updated_at = "2026-06-17T00:00:00.000Z";
  let outputLoads = 0;
  const { page } = loadBookExportPage({
    getMemoirBookPlan: async () => samplePlan(),
    getMemoirBookOutput: async () => {
      outputLoads += 1;
      return output;
    },
    getMemoirBookEditorialCopyJobs: async () => ({
      items: [sampleEditorialJob("preface", "succeeded")],
    }),
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { projectId: "project-1" });
  context.setData({ activeSection: "editorial" });
  await page.refreshBookEditorialCopyProgress.call(context);

  assert.equal(outputLoads, 2);
  assert.equal(context.data.bookEditorialCopyJobs.preface.statusText, "已完成");
});

test("book export page counts the current output when included plan chapters have no draft", () => {
  const { page } = loadBookExportPage();
  const context = pageContext(page);
  const plan = baseSamplePlan("confirmed", { chapterCount: 2 });

  page.applyBookExportData.call(context, plan, sampleBookOutput());

  assert.equal(context.data.totalChapterCount, 1);
  assert.equal(context.data.bookPreviewChapters.length, 1);
});

test("book export updates the chapter count with the selected historical output", async () => {
  const historical = sampleBookOutputVersion(1, "历史书稿正文", undefined, baseSampleBookManuscript());
  const { page } = loadBookExportPage({ getMemoirBookOutputVersion: async () => historical });
  const context = pageContext(page, { projectId: "project-1" });
  page.applyBookExportData.call(context, baseSamplePlan("confirmed", { chapterCount: 2 }), sampleBookOutput());
  await page.changeBookOutputVersion.call(context, { detail: { value: "1" } });

  assert.equal(context.data.bookContent, "历史书稿正文");
  assert.equal(context.data.totalChapterCount, 2);
  assert.equal(context.data.bookPreviewChapters.length, 2);
});

test("book export waits only for active draft processing and preserves failed or material-required guidance", async () => {
  const output = { ...sampleBookOutput(), is_stale: true, stale_chapter_titles: ["童年"] };
  for (const [status, needsMaterial, expected] of [
    ["processing", 0, /正在整理新版全书/],
    ["failed", 0, /遇到问题.*重试/],
    ["needs_input", 1, /需要补充讲述.*首页.*回忆/],
    ["needs_input", 0, /书稿建议.*尚未更新/],
    ["idle", 0, /尚未更新.*当前步骤/],
  ]) {
    const calls = [];
    const { page } = loadBookExportPage({
      getMemoirBookOutput: async () => output,
      getMemoirDraftProgress: async (projectId) => {
        calls.push(projectId);
        return { status, needs_user_material_count: needsMaterial };
      },
    });
    const context = pageContext(page);
    await page.onLoad.call(context, { projectId: "fixture-project" });

    assert.deepEqual(calls, ["fixture-project"]);
    assert.match(context.data.bookStaleNotice, expected);
    if (status !== "processing") assert.doesNotMatch(context.data.bookStaleNotice, /正在|会在后台自动更新/);
    assert.equal(context.data.bookContent, output.latest_version.content);
  }
});

test("book export uses the finished book for chapter count when the plan is unavailable", () => {
  const { page } = loadBookExportPage();
  const context = pageContext(page);

  page.applyBookExportData.call(context, null, sampleBookOutput());

  assert.equal(context.data.totalChapterCount, 1);
});

test("book export opens focused settings and the selected book for reading", async () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
    pageScrollTo() {},
  };
  const { page } = loadBookExportPage();
  const context = pageContext(page, {
    projectId: "project/1",
    bookContent: "全书正文",
    bookSelectedVersionId: "book-output-version-2",
  });

  assert.equal(context.data.activeSection, "");
  page.openSection.call(context, { currentTarget: { dataset: { section: "export" } } });
  assert.equal(context.data.activeSection, "export");
  await page.closeSection.call(context);
  assert.equal(context.data.activeSection, "");

  page.openBookReader.call(context);
  assert.deepEqual(navigations, [
    "/pages/book-reader/index?projectId=project%2F1&versionId=book-output-version-2",
  ]);
});

test("leaving history returns the book overview to the latest version", async () => {
  global.wx = { pageScrollTo() {} };
  const { page } = loadBookExportPage();
  const context = pageContext(page, { projectId: "project/1", pagePurpose: "settings" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());
  page.openSection.call(context, { currentTarget: { dataset: { section: "history" } } });

  await page.changeBookOutputVersion.call(context, { detail: { value: "1" } });
  assert.equal(context.data.bookViewingHistoricalVersion, true);
  await page.closeSection.call(context);

  assert.equal(context.data.activeSection, "");
  assert.equal(context.data.bookViewingHistoricalVersion, false);
  assert.equal(context.data.bookSelectedVersionId, "book-output-version-2");
});

test("book settings return to the overview scroll position", async () => {
  const scrolls = [];
  global.wx = { pageScrollTo(options) { scrolls.push(options); } };
  const { page } = loadBookExportPage();
  const context = pageContext(page, { pagePurpose: "settings", bookContent: "正文" });

  page.onPageScroll.call(context, { scrollTop: 920 });
  page.openSection.call(context, { currentTarget: { dataset: { section: "editorial" } } });
  page.onPageScroll.call(context, { scrollTop: 150 });
  await page.closeSection.call(context);

  assert.deepEqual(scrolls, [
    { scrollTop: 0, duration: 0 },
    { scrollTop: 920, duration: 0 },
  ]);
});

test("book export does not save settings while viewing a historical version", async () => {
  let updateCalls = 0;
  const { page } = loadBookExportPage({
    updateMemoirBookOutput: async () => {
      updateCalls += 1;
      return sampleBookOutput();
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    bookViewingHistoricalVersion: true,
  });

  await page.saveBookDesignSettings.call(context);

  assert.equal(updateCalls, 0);
});

test("book export page saves book metadata without changing the selected cover", async () => {
  const updates = [];
  const { page } = loadBookExportPage({
    updateMemoirBookOutput: async (projectId, payload) => {
      updates.push({ projectId, payload });
      return sampleBookOutput();
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  page.changeBookTitle.call(context, {
    currentTarget: { dataset: {} },
    detail: { value: "外婆的月光" },
  });
  page.changeBookMetadataField.call(context, {
    currentTarget: { dataset: { key: "subtitle" } },
    detail: { value: "根据口述访谈整理" },
  });
  page.changeBookMetadataField.call(context, {
    currentTarget: { dataset: { key: "narrator_name" } },
    detail: { value: "外婆" },
  });
  page.changeBookMetadataField.call(context, {
    currentTarget: { dataset: { key: "preface" } },
    detail: { value: "写给家人的开篇。" },
  });
  await page.saveBookDesignSettings.call(context);

  assert.deepEqual(updates[0], {
    projectId: "project/1",
    payload: {
      title: "外婆的月光",
      subtitle: "根据口述访谈整理",
      narrator_name: "外婆",
      prepared_by: "家人整理",
      preface: "写给家人的开篇。",
      afterword: "这些故事还可以继续补充。",
      back_cover_quote: "我小时候常在院子里晒太阳。",
    },
  });
  assert.equal(context.data.bookTitleChanged, false);
  assert.equal(context.data.bookMetadataChanged, false);
  assert.equal(context.data.bookCoverStyle, "classic_plain");
  assert.equal(context.data.bookDesignSaveStatusText, "已自动保存");
});

test("book export page suggests titles and queues book-level editorial copy", async () => {
  const titleRequests = [];
  const editorialRequests = [];
  const navigations = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
    redirectTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadBookExportPage({
    suggestMemoirBookOutputTitles: async (projectId, payload) => {
      titleRequests.push({ projectId, payload });
      return { suggestions: [" 外婆的月光 ", "庭院往事"] };
    },
    enqueueMemoirBookOutputEditorialCopyGeneration: async (projectId, payload) => {
      editorialRequests.push({ projectId, payload });
      return sampleAutomationEvent({
        id: "event-1",
        event_type: "book_editorial_copy_generated",
        chapter_id: null,
        summary_text: "后记已提交后台重生成。",
        metadata_json: { target_label: "后记", targets: ["afterword"] },
      });
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  await page.suggestBookTitles.call(context);
  page.chooseBookTitleSuggestion.call(context, {
    currentTarget: { dataset: { title: "外婆的月光" } },
  });
  assert.deepEqual(context.data.bookTitleSuggestions, ["外婆的月光", "庭院往事"]);
  assert.equal(context.data.bookTitleInput, "外婆的月光");

  page.clearBookDesignAutosaveTimer.call(context);
  await page.regenerateBookEditorialCopy.call(context, {
    currentTarget: { dataset: { target: "afterword" } },
  });

  assert.deepEqual(titleRequests[0], { projectId: "project/1", payload: { count: 6 } });
  assert.equal(context.data.bookTitleInput, "外婆的月光");
  assert.deepEqual(editorialRequests[0], {
    projectId: "project/1",
    payload: { targets: ["afterword"] },
  });
  assert.equal(toasts[0].title, "已加入后台任务");
  assert.equal(context.data.bookEditorialCopyBusyTarget, "");
  assert.equal(context.data.bookEditorialCopyJobs.afterword.statusText, "排队中");
  assert.equal(context.data.bookEditorialCopyJobs.afterword.active, true);
  assert.equal(context.data.bookEditorialCopyJobs.preface.visible, false);
  page.openBookHome.call(context);
  assert.deepEqual(navigations, ["/pages/book/index"]);
  page.clearBookEditorialCopyPoll.call(context);
});

test("book export page submits only one editorial copy task at a time", async () => {
  const editorialRequests = [];
  let resolveEnqueue;
  global.wx = {
    showToast() {},
  };
  const { page } = loadBookExportPage({
    enqueueMemoirBookOutputEditorialCopyGeneration: async (projectId, payload) => {
      editorialRequests.push({ projectId, payload });
      await new Promise((resolve) => {
        resolveEnqueue = resolve;
      });
      return sampleAutomationEvent({
        id: "event-1",
        event_type: "book_editorial_copy_generated",
        chapter_id: null,
        summary_text: "前言已提交后台重生成。",
        metadata_json: { target_label: "前言", targets: ["preface"] },
      });
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  const firstSubmission = page.regenerateBookEditorialCopy.call(context, {
    currentTarget: { dataset: { target: "preface" } },
  });
  await new Promise((resolve) => setImmediate(resolve));
  await page.regenerateBookEditorialCopy.call(context, {
    currentTarget: { dataset: { target: "afterword" } },
  });

  assert.deepEqual(editorialRequests, [{
    projectId: "project/1",
    payload: { targets: ["preface"] },
  }]);
  assert.equal(context.data.bookEditorialCopyBusyTarget, "preface");

  resolveEnqueue();
  await firstSubmission;
  assert.equal(context.data.bookEditorialCopyBusyTarget, "");
  page.clearBookEditorialCopyPoll.call(context);
});

test("book export keeps separate queued tasks for different editorial fields", async () => {
  const requests = [];
  global.wx = { showToast() {} };
  const { page } = loadBookExportPage({
    enqueueMemoirBookOutputEditorialCopyGeneration: async (_projectId, payload) => {
      const target = payload.targets[0];
      requests.push(target);
      return sampleAutomationEvent({
        event_type: "book_editorial_copy_generated",
        editing_job_id: `job-${target}`,
      });
    },
  });
  const context = pageContext(page, { projectId: "project/1", activeSection: "editorial" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  for (const target of ["preface", "afterword"]) {
    await page.regenerateBookEditorialCopy.call(context, {
      currentTarget: { dataset: { target } },
    });
  }
  await page.regenerateBookEditorialCopy.call(context, {
    currentTarget: { dataset: { target: "preface" } },
  });

  assert.deepEqual(requests, ["preface", "afterword"]);
  assert.equal(context.data.bookEditorialCopyJobs.preface.statusText, "排队中");
  assert.equal(context.data.bookEditorialCopyJobs.afterword.statusText, "排队中");
  assert.equal(context.bookEditorialCopyPoller.pending, true);
  page.clearBookEditorialCopyPoll.call(context);
});

test("book export tracks all editorial jobs through queue, execution, completion, and failure", async () => {
  const updatedOutput = sampleBookOutput();
  updatedOutput.latest_version.manuscript.back_matter.afterword.content = "新的后记。";
  const preface = sampleEditorialJob("preface", "pending");
  const afterword = sampleEditorialJob("afterword", "running");
  const backCover = sampleEditorialJob("back_cover_quote", "succeeded");
  let jobs = [preface, afterword, backCover];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadBookExportPage({
    getMemoirBookEditorialCopyJobs: async () => ({ items: jobs }),
    getMemoirBookOutput: async () => updatedOutput,
  });
  const context = pageContext(page, { projectId: "project/1", activeSection: "editorial" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());
  page.applyBookEditorialCopyJobs.call(context, jobs);
  assert.equal(context.data.bookEditorialCopyJobs.preface.statusText, "排队中");
  assert.equal(context.data.bookEditorialCopyJobs.afterword.statusText, "执行中");
  assert.equal(context.data.bookEditorialCopyJobs.back_cover_quote.statusText, "已完成");
  assert.equal(context.bookEditorialCopyPoller.pending, true);

  jobs = [
    sampleEditorialJob("preface", "running"),
    sampleEditorialJob("afterword", "succeeded"),
    backCover,
  ];
  await page.refreshBookEditorialCopyProgress.call(context);
  assert.equal(context.data.bookEditorialCopyJobs.preface.statusText, "执行中");
  assert.equal(context.data.bookEditorialCopyJobs.afterword.statusText, "已完成");
  assert.equal(context.bookEditorialCopyPoller.pending, true);
  assert.equal(context.data.bookAfterwordInput, "新的后记。");
  assert.equal(toasts[0].title, "后记已更新");

  jobs = [
    sampleEditorialJob("preface", "failed", { error_message: "服务暂时不可用" }),
    jobs[1],
    backCover,
  ];
  await page.refreshBookEditorialCopyProgress.call(context);
  assert.equal(context.data.bookEditorialCopyJobs.preface.statusText, "失败，可重试");
  assert.equal(context.data.bookEditorialCopyJobs.preface.errorText, "服务暂时不可用");
  assert.equal(context.bookEditorialCopyPoller.pending, false);
  assert.equal(toasts[1].title, "前言重写失败");
  page.clearBookEditorialCopyPoll.call(context);
});

test("book export resumes editorial job updates after returning to the page", async () => {
  let refreshes = 0;
  const running = sampleEditorialJob("preface", "running");
  const { page } = loadBookExportPage({
    getMemoirBookEditorialCopyJobs: async () => {
      refreshes += 1;
      return { items: [running] };
    },
  });
  const context = pageContext(page, { projectId: "project/1", activeSection: "editorial" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());
  page.applyBookEditorialCopyJobs.call(context, [running]);
  assert.equal(context.bookEditorialCopyPoller.pending, true);

  page.onHide.call(context);
  assert.equal(context.bookEditorialCopyPoller.pending, false);
  page.onShow.call(context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(refreshes, 1);
  assert.equal(context.data.bookEditorialCopyJobs.preface.statusText, "执行中");
  assert.equal(context.bookEditorialCopyPoller.pending, true);
  page.clearBookEditorialCopyPoll.call(context);
});

test("book export marks job status as stale when refresh fails and retries", async () => {
  const running = sampleEditorialJob("afterword", "running");
  let unavailable = true;
  const { page } = loadBookExportPage({
    getMemoirBookEditorialCopyJobs: async () => {
      if (unavailable) throw new Error("offline");
      return { items: [running] };
    },
  });
  const context = pageContext(page, { projectId: "project/1", activeSection: "editorial" });

  await page.refreshBookEditorialCopyProgress.call(context);
  assert.equal(context.data.bookEditorialCopyStatusError, "任务进度暂时无法更新，请稍后再试");
  assert.equal(context.bookEditorialCopyPoller.pending, true);

  unavailable = false;
  await page.refreshBookEditorialCopyProgress.call(context);
  assert.equal(context.data.bookEditorialCopyStatusError, "");
  assert.equal(context.data.bookEditorialCopyJobs.afterword.statusText, "执行中");
  page.clearBookEditorialCopyPoll.call(context);
});

test("book export page downloads selected PDF layout and source appendix setting", async () => {
  const exportCalls = [];
  const opened = [];
  const toasts = [];
  global.wx = {
    openDocument(options) {
      opened.push(options);
      options.success();
    },
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadBookExportPage({
    downloadMemoirBookOutputExport: async (projectId, format, versionId, layout, showSourceAppendix) => {
      exportCalls.push({ projectId, format, versionId, layout, showSourceAppendix });
      return "/tmp/book.pdf";
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());
  await page.changeBookOutputVersion.call(
    context,
    { currentTarget: { dataset: {} }, detail: { value: "1" } },
  );
  page.toggleBookExportSourceAppendix.call(context, {
    currentTarget: { dataset: {} },
    detail: { value: false },
  });

  await page.downloadBookOutputExport.call(context, {
    currentTarget: { dataset: { format: "pdf", layout: "large_print" } },
  });

  assert.deepEqual(exportCalls[0], {
    projectId: "project/1",
    format: "pdf",
    versionId: "book-output-version-1",
    layout: "large_print",
    showSourceAppendix: false,
  });
  assert.equal(opened[0].filePath, "/tmp/book.pdf");
  assert.equal(opened[0].fileType, "pdf");
  assert.equal(toasts[0].title, "PDF 已打开");
  assert.equal(context.data.bookExportBusyTarget, "");
});

test("book export page downloads reading PDF with the standard layout", async () => {
  const exportCalls = [];
  const opened = [];
  const toasts = [];
  global.wx = {
    openDocument(options) {
      opened.push(options);
      options.success();
    },
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadBookExportPage({
    downloadMemoirBookOutputExport: async (projectId, format, versionId, layout, showSourceAppendix) => {
      exportCalls.push({ projectId, format, versionId, layout, showSourceAppendix });
      return "/tmp/reading.pdf";
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
  });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  await page.downloadBookOutputExport.call(context, {
    currentTarget: { dataset: { format: "pdf", layout: "standard" } },
  });

  assert.equal(exportCalls[0].layout, "standard");
  assert.equal(exportCalls[0].showSourceAppendix, false);
  assert.equal(opened[0].filePath, "/tmp/reading.pdf");
  assert.equal(toasts[0].title, "PDF 已打开");
});

test("current book design appears in the gallery and previews the saved design without changes", async () => {
  const output = sampleBookOutput();
  const downloads = [];
  const updates = [];
  const opened = [];
  global.wx = {
    openDocument(options) { opened.push(options.filePath); options.success(); },
  };
  const { page } = loadBookExportPage({
    downloadMemoirBookOutputExport: async (...args) => {
      downloads.push(args);
      return "/tmp/current-book.pdf";
    },
    updateMemoirBookOutput: async (_projectId, payload) => {
      updates.push(payload);
      return output;
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), output);
  const current = context.data.bookLayoutOptions[0];
  assert.equal(current.value, "current");
  assert.equal(current.label, context.data.bookManuscriptTemplateLabel);
  assert.equal(current.previewClass, "classic");
  assert.equal(current.coverStyle, "classic_plain");
  assert.equal(current.coverPreviewClass, context.data.bookCoverStylePreviewClass);
  assert.equal(current.coverPreviewImage, "");
  assert.equal(current.ornamentThumbnail, "");

  const event = { currentTarget: { dataset: { template: "current" } } };
  await page.previewBookLayout.call(context, event);
  await page.selectBookLayout.call(context, event);
  assert.deepEqual(downloads.map((args) => args.slice(0, 6)), [[
    "project/1", "pdf", "book-output-version-2", "standard", false, undefined,
  ]]);
  assert.deepEqual(opened, ["/tmp/current-book.pdf"]);
  assert.deepEqual(updates, []);
  assert.equal(context.data.bookLayoutFeedbackTarget, "current");
  assert.equal(context.data.bookLayoutBusyTarget, "");

  output.latest_version.manuscript.render_settings.cover_style = "modern_minimal";
  page.applyBookExportData.call(context, samplePlan(), output);
  assert.equal(context.data.bookLayoutOptions[0].coverPreviewImage, context.data.bookCoverStylePreviewImage);
  assert.equal(context.data.bookLayoutOptions[0].previewClass, "classic");
  output.latest_version.manuscript.profile.template = "modern_minimal";
  page.applyBookExportData.call(context, samplePlan(), output);
  assert.equal(context.data.bookLayoutOptions.some((option) => option.value === "current"), false);
});

test("new complete templates preview without saving and apply cover with interior", async () => {
  const output = sampleBookOutput();
  const downloads = [];
  const updates = [];
  global.wx = {
    openDocument(options) { options.success(); },
    showToast() {},
  };
  const { page } = loadBookExportPage({
    downloadMemoirBookOutputExport: async (...args) => {
      downloads.push(args);
      return "/tmp/interior-layout-preview.pdf";
    },
    updateMemoirBookOutput: async (_projectId, payload) => {
      updates.push(payload);
      output.latest_version.manuscript.profile.template = payload.manuscript_template;
      output.latest_version.manuscript.render_settings.cover_style = payload.cover_style;
      return output;
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), output);
  assert.deepEqual(
    context.data.bookLayoutOptions.map((option) => option.value),
    ["current", "heritage_classic", "warm_journal", "modern_minimal"],
  );

  const newTemplates = [
    ["heritage_classic", "典藏文集版", "heritage"],
    ["warm_journal", "家书手记版", "journal"],
    ["modern_minimal", "现代简约版", "modern"],
  ];
  for (const [template, label, previewClass] of newTemplates) {
    const option = context.data.bookLayoutOptions.find((item) => item.value === template);
    assert.equal(option?.label, label);
    assert.equal(option?.previewClass, previewClass);
    assert.equal(option?.coverStyle, template);
    assert.equal(option?.coverPreviewImage, `/assets/book_covers/${template}.jpg`);
    assert.equal(option?.ornamentThumbnail, `/assets/book_ornaments/${template}.png`);
    assert.ok(fs.existsSync(path.join(__dirname, `../assets/book_covers/${template}.jpg`)));
    assert.ok(fs.existsSync(path.join(__dirname, `../assets/book_ornaments/${template}.png`)));
    const event = { currentTarget: { dataset: { template } } };
    await page.previewBookLayout.call(context, event);
    assert.deepEqual(downloads.at(-1).slice(0, 6), [
      "project/1", "pdf", "book-output-version-2", "standard", false,
      { template, custom_layout: undefined },
    ]);
    assert.equal(updates.length, downloads.length - 1);
    assert.equal(context.data.bookLayoutFeedbackTarget, template);
    assert.equal(context.data.bookLayoutFeedbackText, "PDF 已打开");
    await page.selectBookLayout.call(context, event);
    assert.deepEqual(updates.at(-1), { manuscript_template: template, cover_style: template });
    assert.equal(context.data.bookManuscriptTemplateLabel, label);
    assert.equal(context.data.bookLayoutTemplate, template);
    assert.equal(context.data.bookCoverStyle, template);
    assert.equal(context.data.bookCoverStylePreviewImage, `/assets/book_covers/${template}.jpg`);
  }
});

test("stale books still start PDF preview and show progress beside the chosen template", async () => {
  const output = sampleBookOutput();
  output.is_stale = true;
  const opened = [];
  let finishDownload;
  const download = new Promise((resolve) => { finishDownload = resolve; });
  global.wx = {
    openDocument(options) { opened.push(options.filePath); options.success(); },
  };
  const { page } = loadBookExportPage({
    downloadMemoirBookOutputExport: () => download,
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), output);

  const preview = page.previewBookLayout.call(context, {
    currentTarget: { dataset: { template: "heritage_classic" } },
  });
  assert.equal(context.data.bookLayoutBusyTarget, "preview:heritage_classic");
  assert.equal(context.data.bookLayoutFeedbackText, "正在预览上次保存的书稿…");
  finishDownload("/tmp/stale-book-preview.pdf");
  await preview;
  assert.deepEqual(opened, ["/tmp/stale-book-preview.pdf"]);
  assert.equal(context.data.bookLayoutFeedbackText, "PDF 已打开；章节更新尚未收录");
  assert.equal(context.data.bookLayoutBusyTarget, "");
});

test("PDF preview failure is shown beside the tapped template", async () => {
  global.wx = {};
  const { page } = loadBookExportPage({
    downloadMemoirBookOutputExport: async () => { throw new Error("downloadFile 合法域名未配置"); },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  await page.previewBookLayout.call(context, {
    currentTarget: { dataset: { template: "warm_journal" } },
  });
  assert.equal(context.data.bookLayoutFeedbackTarget, "warm_journal");
  assert.equal(context.data.bookLayoutFeedbackText, "downloadFile 合法域名未配置");
  assert.equal(context.data.bookLayoutFeedbackError, true);
  assert.equal(context.data.bookLayoutBusyTarget, "");
});

test("double tapping a complete template waits for title autosave and saves only once", async () => {
  const output = sampleBookOutput();
  output.latest_version.manuscript.profile.template = "heritage_classic";
  output.latest_version.manuscript.render_settings.cover_style = "heritage_classic";
  const updates = [];
  let releaseAutosave;
  const autosaveGate = new Promise((resolve) => { releaseAutosave = resolve; });
  global.wx = { showToast() {} };
  const { page } = loadBookExportPage({
    updateMemoirBookOutput: async (_projectId, payload) => {
      updates.push(payload);
      if (payload.title) {
        await autosaveGate;
        output.title = payload.title;
        output.latest_version.manuscript.profile.title = payload.title;
      }
      if (payload.manuscript_template) {
        output.latest_version.manuscript.profile.template = payload.manuscript_template;
        output.latest_version.manuscript.render_settings.cover_style = payload.cover_style;
      }
      return output;
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), output);
  page.changeBookTitle.call(context, { detail: { value: "外婆的新书" } });
  const event = { currentTarget: { dataset: { template: "warm_journal" } } };
  const firstSelection = page.selectBookLayout.call(context, event);
  assert.equal(context.data.bookLayoutBusyTarget, "save:warm_journal");
  assert.equal(updates.length, 1);
  await page.selectBookLayout.call(context, event);
  assert.equal(updates.length, 1);
  releaseAutosave();
  await firstSelection;

  assert.equal(updates.length, 2);
  assert.equal(updates[0].title, "外婆的新书");
  assert.equal("cover_style" in updates[0], false);
  assert.deepEqual(updates[1], {
    manuscript_template: "warm_journal",
    cover_style: "warm_journal",
  });
  assert.equal(context.data.bookCoverStyle, "warm_journal");
  assert.equal(context.bookDesignAutosaveTimer, null);
});

test("book layout cannot be previewed or selected for a plain text legacy book", async () => {
  const output = sampleBookOutput();
  output.latest_version.manuscript = null;
  const downloads = [];
  const updates = [];
  const { page } = loadBookExportPage({
    downloadMemoirBookOutputExport: async (...args) => downloads.push(args),
    updateMemoirBookOutput: async (...args) => updates.push(args),
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), output);
  const event = { currentTarget: { dataset: { template: "heritage_classic" } } };

  await page.previewBookLayout.call(context, event);
  assert.equal(context.data.error, "请先更新书稿，再预览版式");
  await page.selectBookLayout.call(context, event);
  assert.equal(context.data.error, "请先更新书稿，再选择版式");
  assert.deepEqual(downloads, []);
  assert.deepEqual(updates, []);
});

test("custom book layout previews pending changes and saves all three controls", async () => {
  const output = sampleBookOutput();
  const updates = [];
  const downloads = [];
  global.wx = {
    openDocument(options) { options.success(); },
    showToast() {},
  };
  const { page } = loadBookExportPage({
    downloadMemoirBookOutputExport: async (...args) => {
      downloads.push(args);
      return "/tmp/custom-preview.pdf";
    },
    updateMemoirBookOutput: async (_projectId, payload) => {
      updates.push(payload);
      output.latest_version.manuscript.profile.template = payload.manuscript_template;
      output.latest_version.manuscript.render_settings.custom_layout = payload.custom_layout;
      return output;
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), output);
  page.changeBookCustomLayout.call(context, {
    currentTarget: { dataset: { key: "font_scale" } }, detail: { value: "1" },
  });
  page.changeBookCustomLayout.call(context, {
    currentTarget: { dataset: { key: "line_spacing" } }, detail: { value: "2" },
  });
  page.changeBookCustomLayout.call(context, {
    currentTarget: { dataset: { key: "page_margin" } }, detail: { value: "0" },
  });
  assert.equal(context.data.bookLayoutCustomChanged, true);
  assert.deepEqual(context.data.bookCustomLayout, {
    font_scale: "large", line_spacing: "relaxed", page_margin: "narrow",
  });

  await page.previewBookLayout.call(context, {
    currentTarget: { dataset: { template: "custom" } },
  });
  assert.deepEqual(downloads[0][5], {
    template: "custom",
    custom_layout: { font_scale: "large", line_spacing: "relaxed", page_margin: "narrow" },
  });
  assert.deepEqual(updates, []);

  await page.selectBookLayout.call(context, {
    currentTarget: { dataset: { template: "custom" } },
  });
  assert.deepEqual(updates[0], {
    manuscript_template: "custom",
    custom_layout: { font_scale: "large", line_spacing: "relaxed", page_margin: "narrow" },
  });
  assert.equal(context.data.bookLayoutCustomChanged, false);
  assert.equal(context.data.bookManuscriptTemplateLabel, "自定义版式");
});

test("book export delivers TXT through WeChat file sharing", async () => {
  const shared = [];
  const toasts = [];
  global.wx = {
    shareFileMessage(options) {
      shared.push(options);
      options.success({ errMsg: "shareFileMessage:ok" });
    },
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadBookExportPage({
    downloadMemoirBookOutputExport: async () => "/tmp/《外婆回忆录》_v2.txt",
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  await page.downloadBookOutputExport.call(context, {
    currentTarget: { dataset: { format: "txt" } },
  });

  assert.equal(shared[0].filePath, "/tmp/《外婆回忆录》_v2.txt");
  assert.equal(shared[0].fileName, "《外婆回忆录》_v2.txt");
  assert.equal(toasts[0].title, "已打开微信分享");
  assert.equal(context.data.bookExportBusyTarget, "");
});

test("canceling TXT sharing does not report a successful download", async () => {
  const toasts = [];
  global.wx = {
    shareFileMessage(options) {
      options.fail({ errMsg: "shareFileMessage:fail cancel" });
    },
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadBookExportPage({
    downloadMemoirBookOutputExport: async () => "/tmp/《外婆回忆录》_v2.txt",
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  await page.downloadBookOutputExport.call(context, {
    currentTarget: { dataset: { format: "txt" } },
  });

  assert.deepEqual(toasts, []);
  assert.equal(context.data.error, "");
  assert.equal(context.data.exportNotice, "已取消分享，可以重新导出文本。");
  assert.equal(context.data.bookExportBusyTarget, "");
});

test("book export page assembles confirmed chapter drafts as a new full-book version", async () => {
  const requests = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadBookExportPage({
    assembleMemoirBookOutput: async (projectId) => {
      requests.push({ kind: "assemble", projectId });
    },
    updateMemoirBookOutput: async (projectId, payload) => {
      requests.push({ kind: "update", projectId, payload });
      return sampleBookOutput();
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyBookExportData.call(context, samplePlan(), sampleBookOutput());

  await page.generateBook.call(context);

  assert.deepEqual(requests[0], { kind: "assemble", projectId: "project/1" });
  assert.deepEqual(requests[1], {
    kind: "update",
    projectId: "project/1",
    payload: {
      title: "外婆回忆录",
      subtitle: "根据口述访谈整理",
      narrator_name: "外婆",
      prepared_by: "家人整理",
      preface: "这些故事来自家里的讲述。",
      afterword: "这些故事还可以继续补充。",
      back_cover_quote: "我小时候常在院子里晒太阳。",
    },
  });
  assert.equal(context.data.generatingBook, false);
  assert.equal(context.data.exportNotice, "已根据各章确认正文更新全书稿。");
  assert.equal(toasts[0].title, "书稿已更新");
});


test("plain text output without a manuscript does not invent book structure", async () => {
  const output = sampleBookOutput();
  output.latest_version.content = "已有的纯文字正文";
  output.latest_version.manuscript = null;
  const { bookOutputData } = require("../pages/book/book-view-model.ts");
  const data = bookOutputData(output);
  assert.equal(data.bookContent, "已有的纯文字正文");
  assert.equal(data.bookManuscriptAvailable, false);
  assert.deepEqual(data.bookManuscriptStructureItems, []);
  assert.deepEqual(data.bookManuscriptChapterItems, []);
  assert.deepEqual(data.bookPreviewChapters, []);
});

test("settings and files open distinct destinations while image assets remain reachable before a draft", async () => {
  const navigations = [];
  global.wx = { navigateTo: ({ url }) => navigations.push(url), pageScrollTo() {} };
  const { page } = loadBookExportPage();
  const context = pageContext(page, { projectId: "project-1", bookContent: "正文", pagePurpose: "files" });
  page.openSection.call(context, { currentTarget: { dataset: { section: "title" } } });
  assert.equal(context.data.activeSection, "");
  context.setData({ pagePurpose: "settings" });
  page.openSection.call(context, { currentTarget: { dataset: { section: "export" } } });
  assert.equal(context.data.activeSection, "");
  page.openSection.call(context, { currentTarget: { dataset: { section: "title" } } });
  assert.equal(context.data.activeSection, "title");
  context.setData({ bookContent: "", activeSection: "" });
  page.openSection.call(context, { currentTarget: { dataset: { section: "title" } } });
  assert.equal(context.data.activeSection, "");
  page.openImageAssetsPage.call(context);
  assert.deepEqual(navigations, ["/pages/image-assets/index?projectId=project-1"]);
});
