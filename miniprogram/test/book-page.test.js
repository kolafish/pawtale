require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function sampleRef(id = "utterance-1", quote = "外婆家的院子") {
  return {
    voice_session_id: "voice-1",
    message_id: `message-${id}`,
    utterance_id: id,
    transcript_version_id: `version-${id}`,
    audio_file_id: `audio-${id}`,
    start_ms: 0,
    end_ms: 8000,
    quote,
  };
}

function samplePlan(status = "draft") {
  return {
    id: "plan-1",
    project_id: "project/1",
    title: "外婆回忆录章节规划",
    status,
    created_by: "ai",
    model: "fake",
    metadata_json: {},
    created_at: "2026-06-17T00:00:00.000Z",
    updated_at: "2026-06-17T00:00:00.000Z",
    chapters: [
      {
        id: "chapter-1",
        plan_id: "plan-1",
        project_id: "project/1",
        title: "外婆家的童年",
        summary: "整理外婆家院子的童年生活。",
        sort_order: 0,
        source_refs: [sampleRef("utterance-1", "外婆家的院子")],
        metadata_json: {},
        created_at: "2026-06-17T00:00:00.000Z",
        updated_at: "2026-06-17T00:00:00.000Z",
      },
      {
        id: "chapter-2",
        plan_id: "plan-1",
        project_id: "project/1",
        title: "第一次离家",
        summary: "整理第一次离家求学。",
        sort_order: 1,
        source_refs: [sampleRef("utterance-2", "第一次离家")],
        metadata_json: {},
        created_at: "2026-06-17T00:00:00.000Z",
        updated_at: "2026-06-17T00:00:00.000Z",
      },
    ],
  };
}

function sampleDraft(
  version = 1,
  status = "draft",
  content = "我小时候常在外婆家的院子里晒太阳。",
  metadata = {},
) {
  return {
    id: "draft-1",
    project_id: "project/1",
    chapter_id: "chapter-1",
    status,
    metadata_json: metadata,
    latest_version: {
      id: `draft-version-${version}`,
      draft_id: "draft-1",
      project_id: "project/1",
      chapter_id: "chapter-1",
      version,
      content,
      created_by: "ai",
      model: "fake",
      prompt_snapshot: "prompt",
      feedback: null,
      created_at: "2026-06-17T00:00:00.000Z",
    },
    versions: [],
    created_at: "2026-06-17T00:00:00.000Z",
    updated_at: "2026-06-17T00:00:00.000Z",
  };
}

function sampleBookOutput(
  version = 1,
  content = "外婆回忆录\n\n目录\n1. 外婆家的童年\n\n第1章 外婆家的童年\n\n正文",
  isStale = false,
  staleChapterTitles = [],
) {
  return {
    id: "book-output-1",
    project_id: "project/1",
    plan_id: "plan-1",
    title: "外婆回忆录",
    status: "draft",
    metadata_json: {},
    is_stale: isStale,
    stale_chapter_titles: staleChapterTitles,
    latest_version: {
      id: `book-output-version-${version}`,
      output_id: "book-output-1",
      project_id: "project/1",
      plan_id: "plan-1",
      version,
      content,
      chapter_snapshot_json: [],
      created_by: "system",
      created_at: "2026-06-17T00:00:00.000Z",
    },
    versions: [],
    created_at: "2026-06-17T00:00:00.000Z",
    updated_at: "2026-06-17T00:00:00.000Z",
  };
}

function sampleSourceCoverage(count = 1) {
  return {
    project_id: "project/1",
    plan_id: "plan-1",
    total_source_count: count + 2,
    covered_source_count: 2,
    uncovered_source_count: count,
    uncovered_sources: Array.from({ length: count }, (_, index) => ({
      source_index: index + 3,
      voice_session_id: "voice-1",
      message_id: `message-new-${index + 1}`,
      utterance_id: `utterance-new-${index + 1}`,
      transcript_version_id: `version-new-${index + 1}`,
      audio_file_id: `audio-new-${index + 1}`,
      start_ms: 0,
      end_ms: 8000,
      text: "新增转写",
      created_at: "2026-06-17T00:00:00.000Z",
    })),
  };
}

function sampleAudioResponse() {
  return {
    audio_file_ids: ["audio-book-1", "audio-book-2"],
    provider: "fake",
    model: "fake-tts",
    mime_type: "audio/mpeg",
    chunk_count: 2,
    source_text_hash: "hash",
  };
}

function loadBookPage({ authExports = {}, voiceApiExports = {} } = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const voiceApiPath = require.resolve("../services/memoir-voice-api.ts");
  const bookPath = require.resolve("../pages/book/index.ts");
  delete require.cache[authPath];
  delete require.cache[voiceApiPath];
  delete require.cache[bookPath];
  require.cache[authPath] = {
    id: authPath,
    filename: authPath,
    loaded: true,
    exports: {
      getMemoirIdentity: async () => {
        throw new Error("getMemoirIdentity was not mocked");
      },
      ...authExports,
    },
  };
  require.cache[voiceApiPath] = {
    id: voiceApiPath,
    filename: voiceApiPath,
    loaded: true,
    exports: {
      assignMemoirBookSources: async () => {
        throw new Error("assignMemoirBookSources was not mocked");
      },
      confirmMemoirBookChapterDraft: async () => {
        throw new Error("confirmMemoirBookChapterDraft was not mocked");
      },
      confirmMemoirBookPlan: async () => {
        throw new Error("confirmMemoirBookPlan was not mocked");
      },
      createMemoirBookChapterDraftTTS: async () => {
        throw new Error("createMemoirBookChapterDraftTTS was not mocked");
      },
      createMemoirBookOutputTTS: async () => {
        throw new Error("createMemoirBookOutputTTS was not mocked");
      },
      downloadMemoirVoiceAudio: async () => {
        throw new Error("downloadMemoirVoiceAudio was not mocked");
      },
      generateMemoirBookChapterDraft: async () => {
        throw new Error("generateMemoirBookChapterDraft was not mocked");
      },
      generateMemoirBookOutput: async () => {
        throw new Error("generateMemoirBookOutput was not mocked");
      },
      generateMemoirBookPlan: async () => {
        throw new Error("generateMemoirBookPlan was not mocked");
      },
      getMemoirBookChapterDraft: async () => null,
      getMemoirBookOutput: async () => null,
      getMemoirBookPlan: async () => {
        throw new Error("getMemoirBookPlan was not mocked");
      },
      getMemoirBookSourceCoverage: async () => null,
      updateMemoirBookChapterDraft: async () => {
        throw new Error("updateMemoirBookChapterDraft was not mocked");
      },
      ...voiceApiExports,
    },
  };

  let page = null;
  const previousPage = global.Page;
  global.Page = (definition) => {
    page = definition;
  };
  const mod = require(bookPath);
  if (previousPage === undefined) {
    delete global.Page;
  } else {
    global.Page = previousPage;
  }
  return { mod, page };
}

function pageContext(page, dataPatch = {}) {
  return {
    ...page,
    data: { ...page.data, ...dataPatch },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };
}

test("book page summarizes chapter source quotes without exposing raw ids", () => {
  const { mod } = loadBookPage();

  assert.equal(
    mod.bookChapterSourceSummary({
      source_refs: [
        sampleRef("utterance-1", "外婆家的院子"),
        sampleRef("utterance-2", "第一次离家"),
        sampleRef("utterance-3", "第三段"),
      ],
    }),
    "外婆家的院子 / 第一次离家",
  );
});

test("book page progress counts generated and confirmed chapters", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  const chapters = mod.buildChapterViews(plan, {
    "chapter-1": sampleDraft(1, "confirmed"),
    "chapter-2": sampleDraft(1, "draft"),
  });

  assert.equal(mod.bookProgressText(chapters), "1/2 章已确认，2 章已有正文");
  assert.equal(chapters[0].draftStatusText, "已确认");
  assert.equal(chapters[1].draftStatusText, "待确认");
});

test("book page summarizes latest book output version", () => {
  const { mod } = loadBookPage();
  const output = sampleBookOutput(3, "全书正文");

  assert.equal(mod.latestBookContent(output), "全书正文");
  assert.equal(mod.bookOutputVersionText(output), "全书第 3 版");
  assert.equal(mod.bookOutputStaleNotice(output), "");
  assert.equal(mod.bookOutputVersionText(null), "尚未生成全书");
  assert.equal(
    mod.bookOutputStaleNotice(sampleBookOutput(3, "全书正文", true, ["外婆家的童年", "第一次离家", "县城生活"])),
    "章节「外婆家的童年、第一次离家」等章节已更新，请重新生成全书。",
  );
});

test("book page shows source coverage notice only when there are uncovered transcripts", () => {
  const { mod } = loadBookPage();

  assert.equal(mod.bookSourceCoverageNotice(samplePlan("confirmed"), sampleSourceCoverage(2)), "有 2 条新转写尚未收录到书中");
  assert.equal(mod.bookSourceCoverageNotice(null, sampleSourceCoverage(1)), "有 1 条转写可用于生成章节");
  assert.equal(mod.bookSourceCoverageNotice(samplePlan("confirmed"), sampleSourceCoverage(0)), "");
  assert.equal(mod.bookSourceCoverageNotice(samplePlan("confirmed"), null), "");
});

test("book page builds source assignment views and chapter picker options", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  const chapters = mod.buildChapterViews(plan);
  const coverage = sampleSourceCoverage(2);
  coverage.uncovered_sources[0].created_at = "2026-06-17T00:00:00.000Z";
  coverage.uncovered_sources[1].created_at = "2026-06-17T01:00:00.000Z";
  coverage.uncovered_sources[1].text = "这是一段很长的新增转写，".repeat(10);

  const sources = mod.buildUncoveredSourceViews(coverage, 1);
  const options = mod.buildChapterPickerOptions(chapters);

  assert.equal(sources.length, 1);
  assert.equal(sources[0].utterance_id, "utterance-new-2");
  assert.equal(sources[0].previewText.endsWith("..."), true);
  assert.equal(sources[0].createdAtText, "06-17 09:00 北京时间");
  assert.deepEqual(options, [
    { label: "第 1 章 外婆家的童年", value: "chapter-1" },
    { label: "第 2 章 第一次离家", value: "chapter-2" },
  ]);
});

test("book page shows recent uncovered transcripts before older ones", () => {
  const { mod } = loadBookPage();
  const coverage = sampleSourceCoverage(14);
  coverage.uncovered_sources.forEach((source, index) => {
    source.created_at = `2026-06-17T00:${String(index).padStart(2, "0")}:00.000Z`;
  });

  const sources = mod.buildUncoveredSourceViews(coverage);

  assert.equal(sources.length, 12);
  assert.equal(sources[0].utterance_id, "utterance-new-14");
  assert.equal(sources[11].utterance_id, "utterance-new-3");
});

test("book page marks assigned sources as supplemental chapter work", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  plan.chapters[0].metadata_json = {
    pending_source_utterance_ids: ["utterance-new-1"],
    source_assignment_updated_at: "2026-06-17T00:00:00+00:00",
  };

  const chapters = mod.buildChapterViews(plan, {
    "chapter-1": sampleDraft(1, "draft", "第一版正文", { status_reason: "new_sources_assigned" }),
  });

  assert.equal(chapters[0].pendingSourceCount, 1);
  assert.equal(chapters[0].draftActionText, "补写新增");
  assert.equal(chapters[0].sourceChangeNotice, "1 条新增来源待补写");
  assert.equal(chapters[1].draftActionText, "生成本章");
});

test("book page marks deleted chapter sources for review", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  plan.chapters[0].source_refs = [];
  plan.chapters[0].metadata_json = {
    removed_source_utterance_ids: ["utterance-1"],
    source_refs_removed_at: "2026-06-17T00:00:00+00:00",
  };

  const chapters = mod.buildChapterViews(plan, {
    "chapter-1": sampleDraft(1, "draft", "第一版正文", { status_reason: "sources_deleted" }),
  });

  assert.equal(chapters[0].sourceCount, 0);
  assert.equal(chapters[0].removedSourceCount, 1);
  assert.equal(chapters[0].draftActionText, "整理本章");
  assert.equal(chapters[0].sourceChangeNotice, "1 条来源已删除，请检查本章正文");
});

test("book page confirms chapters with trimmed user edits and original source refs", async () => {
  const submissions = [];
  const confirmedPlan = samplePlan("confirmed");
  global.wx = { showToast() {} };
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      confirmMemoirBookPlan: async (projectId, data) => {
        submissions.push({ projectId, data });
        return confirmedPlan;
      },
      getMemoirBookChapterDraft: async () => null,
    },
  });
  const plan = samplePlan("draft");
  const chapters = mod.buildChapterViews(plan);
  chapters[0].editingTitle = "  童年院子  ";
  chapters[0].editingSummary = "  只写院子里的事  ";
  const context = pageContext(page, {
    projectId: "project/1",
    projectTitle: "外婆回忆录",
    plan,
    planTitle: "  用户确认目录  ",
    chapters,
  });

  await page.confirmPlan.call(context);

  assert.equal(submissions.length, 1);
  assert.equal(submissions[0].projectId, "project/1");
  assert.equal(submissions[0].data.title, "用户确认目录");
  assert.equal(submissions[0].data.chapters[0].title, "童年院子");
  assert.equal(submissions[0].data.chapters[0].summary, "只写院子里的事");
  assert.equal(submissions[0].data.chapters[0].source_refs[0].utterance_id, "utterance-1");
  assert.equal(context.data.stage, "drafting");
  delete global.wx;
});

test("book page assigns uncovered transcript to selected chapter", async () => {
  const calls = [];
  global.wx = { showToast() {}, switchTab() {} };
  const assignedPlan = samplePlan("confirmed");
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      assignMemoirBookSources: async (projectId, data) => {
        calls.push({ projectId, data });
        return assignedPlan;
      },
      getMemoirBookChapterDraft: async () => sampleDraft(1, "draft"),
      getMemoirBookSourceCoverage: async () => sampleSourceCoverage(0),
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    projectTitle: "外婆回忆录",
  });
  page.applyPlan.call(context, assignedPlan, {
    "chapter-1": sampleDraft(1, "confirmed"),
    "chapter-2": sampleDraft(1, "confirmed"),
  });
  page.applySourceCoverage.call(context, sampleSourceCoverage(1));
  page.applyBookOutput.call(context, sampleBookOutput(1, "旧全书"));

  await page.assignUncoveredSource.call(context, {
    currentTarget: { dataset: { id: "utterance-new-1" } },
    detail: { value: 1 },
  });

  assert.deepEqual(calls[0], {
    projectId: "project/1",
    data: {
      assignments: [
        {
          chapter_id: "chapter-2",
          utterance_ids: ["utterance-new-1"],
        },
      ],
    },
  });
  assert.equal(context.data.assigningSourceId, "");
  assert.equal(context.data.bookOutput, null);
  assert.equal(context.data.sourceCoverageNotice, "");
  assert.equal(context.data.chapters[0].draftStatusText, "待确认");
  delete global.wx;
});

test("book page generates full book output after all chapters are confirmed", async () => {
  const calls = [];
  global.wx = { showToast() {} };
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      generateMemoirBookOutput: async (projectId, data) => {
        calls.push({ projectId, data });
        return sampleBookOutput(2, "全书第二版正文");
      },
    },
  });
  const plan = samplePlan("confirmed");
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, plan, {
    "chapter-1": sampleDraft(1, "confirmed"),
    "chapter-2": sampleDraft(1, "confirmed"),
  });
  page.applyBookOutput.call(context, sampleBookOutput(1, "全书第一版正文"));

  await page.generateBook.call(context);

  assert.deepEqual(calls[0], {
    projectId: "project/1",
    data: { force_regenerate: true },
  });
  assert.equal(context.data.bookVersionText, "全书第 2 版");
  assert.equal(context.data.bookContent, "全书第二版正文");
  assert.equal(context.data.bookCharCount, "全书第二版正文".length);
  assert.equal(context.data.bookActionText, "重新生成全书");
  delete global.wx;
});

test("book page shows stale book output state and blocks old audio", async () => {
  const calls = [];
  const { page } = loadBookPage({
    voiceApiExports: {
      createMemoirBookOutputTTS: async () => {
        calls.push("tts");
        return sampleAudioResponse();
      },
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    allChaptersConfirmed: true,
    canGenerateBook: true,
    bookOutput: sampleBookOutput(1, "旧全书", true, ["外婆家的童年"]),
    bookContent: "旧全书",
    hasDraftChanges: false,
  });
  page.applyBookOutput.call(context, context.data.bookOutput);

  assert.equal(context.data.bookActionText, "更新全书");
  assert.equal(context.data.bookStaleNotice, "章节「外婆家的童年」已更新，请重新生成全书。");

  await page.playBookAudio.call(context);

  assert.equal(context.data.error, "章节内容已更新，请先更新全书再朗读");
  assert.equal(calls.length, 0);
});

test("book page regenerates an existing chapter draft with force flag", async () => {
  const calls = [];
  global.wx = { showToast() {} };
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      generateMemoirBookChapterDraft: async (projectId, chapterId, data) => {
        calls.push({ projectId, chapterId, data });
        return sampleDraft(2, "draft", "第二版正文");
      },
    },
  });
  const plan = samplePlan("confirmed");
  const context = pageContext(page, {
    projectId: "project/1",
    chapters: mod.buildChapterViews(plan, { "chapter-1": sampleDraft(1, "draft", "第一版正文") }),
  });

  await page.generateChapterDraft.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });

  assert.deepEqual(calls[0], {
    projectId: "project/1",
    chapterId: "chapter-1",
    data: { force_regenerate: true },
  });
  assert.equal(context.data.chapters[0].draftVersionText, "第 2 版");
  assert.equal(context.data.chapters[0].draftContent, "第二版正文");
  assert.equal(context.data.chapters[0].draftChanged, false);
  delete global.wx;
});

test("book page clears supplemental source state after chapter regeneration", async () => {
  const calls = [];
  global.wx = { showToast() {} };
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      generateMemoirBookChapterDraft: async (projectId, chapterId, data) => {
        calls.push({ projectId, chapterId, data });
        return sampleDraft(2, "draft", "补写后的正文");
      },
    },
  });
  const plan = samplePlan("confirmed");
  plan.chapters[0].metadata_json = {
    pending_source_utterance_ids: ["utterance-new-1"],
    source_assignment_updated_at: "2026-06-17T00:00:00+00:00",
  };
  const context = pageContext(page, {
    projectId: "project/1",
    chapters: mod.buildChapterViews(plan, {
      "chapter-1": sampleDraft(1, "draft", "第一版正文", { status_reason: "new_sources_assigned" }),
    }),
  });

  await page.generateChapterDraft.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });

  assert.deepEqual(calls[0], {
    projectId: "project/1",
    chapterId: "chapter-1",
    data: { force_regenerate: true },
  });
  assert.equal(context.data.chapters[0].pendingSourceCount, 0);
  assert.equal(context.data.chapters[0].draftActionText, "重新生成");
  assert.deepEqual(context.data.chapters[0].metadata_json, {});
  delete global.wx;
});

test("book page blocks regeneration while draft edits are unsaved", async () => {
  const calls = [];
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      generateMemoirBookChapterDraft: async () => {
        calls.push("called");
        return sampleDraft(2);
      },
    },
  });
  const plan = samplePlan("confirmed");
  const chapters = mod.buildChapterViews(plan, { "chapter-1": sampleDraft(1) });
  chapters[0].draftChanged = true;
  const context = pageContext(page, {
    projectId: "project/1",
    chapters,
  });

  await page.generateChapterDraft.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });

  assert.equal(calls.length, 0);
  assert.equal(context.data.chapters[0].draftError, "请先保存当前修改，再重新生成");
});

test("book APIs call encoded backend routes", async () => {
  const requests = [];
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      if (options.url.endsWith("/draft/tts") || options.url.endsWith("/book-output/tts")) {
        options.success({ statusCode: 200, data: sampleAudioResponse(), cookies: [], header: {} });
        return;
      }
      if (options.url.endsWith("/draft") || options.url.endsWith("/draft/confirm")) {
        options.success({ statusCode: 200, data: sampleDraft(), cookies: [], header: {} });
        return;
      }
      if (options.url.endsWith("/book-output") || options.url.endsWith("/book-output/generate")) {
        options.success({ statusCode: 200, data: sampleBookOutput(), cookies: [], header: {} });
        return;
      }
      if (options.url.endsWith("/book-source-coverage")) {
        options.success({ statusCode: 200, data: sampleSourceCoverage(), cookies: [], header: {} });
        return;
      }
      options.success({ statusCode: 200, data: samplePlan("confirmed"), cookies: [], header: {} });
    },
  };
  delete require.cache[require.resolve("../services/auth.ts")];
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const {
    confirmMemoirBookChapterDraft,
    confirmMemoirBookPlan,
    createMemoirBookChapterDraftTTS,
    createMemoirBookOutputTTS,
    generateMemoirBookChapterDraft,
    generateMemoirBookOutput,
    generateMemoirBookPlan,
    getMemoirBookChapterDraft,
    getMemoirBookOutput,
    getMemoirBookPlan,
    getMemoirBookSourceCoverage,
    updateMemoirBookChapterDraft,
  } = require("../services/memoir-voice-api.ts");

  await getMemoirBookPlan("project/1");
  await generateMemoirBookPlan("project/1", { max_chapters: 6 });
  await confirmMemoirBookPlan("project/1", { title: "目录", chapters: [] });
  await getMemoirBookSourceCoverage("project/1");
  await getMemoirBookChapterDraft("project/1", "chapter/1");
  await generateMemoirBookChapterDraft("project/1", "chapter/1", { force_regenerate: true });
  await updateMemoirBookChapterDraft("project/1", "chapter/1", { content: "正文" });
  await confirmMemoirBookChapterDraft("project/1", "chapter/1");
  await createMemoirBookChapterDraftTTS("project/1", "chapter/1", {});
  await getMemoirBookOutput("project/1");
  await generateMemoirBookOutput("project/1", { force_regenerate: true });
  await createMemoirBookOutputTTS("project/1", {});

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan");
  assert.equal(apiRequests[0].method, "GET");
  assert.equal(apiRequests[1].url, "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/generate");
  assert.equal(apiRequests[1].method, "POST");
  assert.deepEqual(apiRequests[1].data, { max_chapters: 6 });
  assert.equal(apiRequests[2].method, "PUT");
  assert.equal(
    apiRequests[3].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-source-coverage",
  );
  assert.equal(apiRequests[3].method, "GET");
  assert.equal(
    apiRequests[4].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/chapters/chapter%2F1/draft",
  );
  assert.equal(apiRequests[4].method, "GET");
  assert.equal(apiRequests[5].method, "POST");
  assert.deepEqual(apiRequests[5].data, { force_regenerate: true });
  assert.equal(apiRequests[6].method, "PATCH");
  assert.deepEqual(apiRequests[6].data, { content: "正文" });
  assert.equal(
    apiRequests[7].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/chapters/chapter%2F1/draft/confirm",
  );
  assert.equal(apiRequests[7].method, "POST");
  assert.equal(
    apiRequests[8].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/chapters/chapter%2F1/draft/tts",
  );
  assert.equal(apiRequests[8].method, "POST");
  assert.equal(
    apiRequests[9].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output",
  );
  assert.equal(apiRequests[9].method, "GET");
  assert.equal(
    apiRequests[10].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output/generate",
  );
  assert.equal(apiRequests[10].method, "POST");
  assert.deepEqual(apiRequests[10].data, { force_regenerate: true });
  assert.equal(
    apiRequests[11].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output/tts",
  );
  assert.equal(apiRequests[11].method, "POST");
  assert.equal(apiRequests[11].header.Cookie, "pawtale_session=abc");
  delete global.wx;
});

test("book source assignment API calls encoded backend route", async () => {
  const requests = [];
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      options.success({ statusCode: 200, data: samplePlan("confirmed"), cookies: [], header: {} });
    },
  };
  delete require.cache[require.resolve("../services/auth.ts")];
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const { assignMemoirBookSources } = require("../services/memoir-voice-api.ts");

  await assignMemoirBookSources("project/1", {
    assignments: [
      {
        chapter_id: "chapter/1",
        utterance_ids: ["utterance/1"],
      },
    ],
  });

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-source-assignments",
  );
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, {
    assignments: [
      {
        chapter_id: "chapter/1",
        utterance_ids: ["utterance/1"],
      },
    ],
  });
  assert.equal(apiRequests[0].header.Cookie, "pawtale_session=abc");
  delete global.wx;
});
