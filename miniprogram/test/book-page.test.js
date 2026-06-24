require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

function sampleRef(id = "utterance-1", quote = "外婆家的院子") {
  return {
    voice_session_id: "voice-1",
    message_id: `message-${id}`,
    utterance_id: id,
    transcript_version_id: `version-${id}`,
    audio_file_id: `audio-${id}`,
    start_ms: 0,
    end_ms: 8000,
    created_at: "2026-06-17T00:00:00.000Z",
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
  const uncoveredSources = Array.from({ length: count }, (_, index) => ({
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
  }));
  return {
    project_id: "project/1",
    plan_id: "plan-1",
    total_source_count: count + 2,
    covered_source_count: 2,
    uncovered_source_count: count,
    sources: [
      {
        source_index: 1,
        voice_session_id: "voice-1",
        message_id: "message-utterance-1",
        utterance_id: "utterance-1",
        transcript_version_id: "version-utterance-1",
        audio_file_id: "audio-utterance-1",
        start_ms: 0,
        end_ms: 8000,
        text: "外婆家的院子",
        created_at: "2026-06-17T00:00:00.000Z",
        is_included: true,
        included_in_chapters: [{ chapter_id: "chapter-1", title: "外婆家的童年", sort_order: 0 }],
      },
      {
        source_index: 2,
        voice_session_id: "voice-1",
        message_id: "message-utterance-2",
        utterance_id: "utterance-2",
        transcript_version_id: "version-utterance-2",
        audio_file_id: "audio-utterance-2",
        start_ms: 0,
        end_ms: 8000,
        text: "第一次离家",
        created_at: "2026-06-17T00:00:00.000Z",
        is_included: true,
        included_in_chapters: [{ chapter_id: "chapter-2", title: "第一次离家", sort_order: 1 }],
      },
      ...uncoveredSources,
    ],
    uncovered_sources: uncoveredSources,
  };
}

function sampleAudioResponse() {
  return {
    audio_file_ids: ["audio-book-1", "audio-book-2"],
    audio_file_durations_seconds: [1.2, 1.4],
    provider: "fake",
    model: "fake-tts",
    mime_type: "audio/mpeg",
    chunk_count: 2,
    source_text_hash: "hash",
    duration_seconds: 2.6,
    alignment_provider: "fake",
    alignment: [
      {
        text: "外婆",
        start_ms: 0,
        end_ms: 600,
        text_start: 0,
        text_end: 2,
      },
      {
        text: "回忆录",
        start_ms: 600,
        end_ms: 1200,
        text_start: 2,
        text_end: 5,
      },
    ],
  };
}

function sampleBookVoiceCommandPreview(overrides = {}) {
  return {
    original_text: "把第一章改名为童年往事",
    intent: "rename_chapter",
    intent_label: "修改章名",
    summary: "准备把「外婆家的童年」改名为「童年往事」。",
    operations: [
      {
        type: "rename_chapter",
        chapter_id: "chapter-1",
        chapter_title: "外婆家的童年",
        new_title: "童年往事",
      },
    ],
    requires_confirmation: true,
    used_ai: true,
    provider: "fake",
    model: "fake-model",
    ...overrides,
  };
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function planFromChapterUpdates(status, updates) {
  const base = samplePlan(status);
  return {
    ...base,
    chapters: updates.map((update, index) => {
      const existing = base.chapters.find((chapter) => chapter.id === update.id) || {};
      return {
        id: update.id || `chapter-${index + 1}`,
        plan_id: "plan-1",
        project_id: "project/1",
        metadata_json: {},
        created_at: "2026-06-17T00:00:00.000Z",
        updated_at: "2026-06-17T00:00:00.000Z",
        ...existing,
        title: update.title,
        sort_order: index,
        source_refs: clone(update.source_refs),
      };
    }),
  };
}

function loadBookPage({
  authExports = {},
  voiceApiExports = {},
  voiceSearchExports = {},
  ttsExports = {},
} = {}) {
  mockModule("../services/auth.ts", {
    getMemoirIdentity: async () => {
      throw new Error("getMemoirIdentity was not mocked");
    },
    ...authExports,
  });
  mockModule("../services/memoir-voice-api.ts", {
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
    createMemoirBookPlanOutlineTTS: async () => {
      throw new Error("createMemoirBookPlanOutlineTTS was not mocked");
    },
    downloadMemoirVoiceAudio: async () => {
      throw new Error("downloadMemoirVoiceAudio was not mocked");
    },
    generateMemoirBookChapterTitle: async () => {
      throw new Error("generateMemoirBookChapterTitle was not mocked");
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
    parseMemoirBookVoiceCommand: async () => {
      throw new Error("parseMemoirBookVoiceCommand was not mocked");
    },
    updateMemoirBookChapterTitle: async () => {
      throw new Error("updateMemoirBookChapterTitle was not mocked");
    },
    updateMemoirBookPlan: async () => {
      throw new Error("updateMemoirBookPlan was not mocked");
    },
    updateMemoirBookChapterDraft: async () => {
      throw new Error("updateMemoirBookChapterDraft was not mocked");
    },
    ...voiceApiExports,
  });
  mockModule("../services/memoir-voice-search.ts", {
    recordMemoirVoiceSearchQuery: async () => {
      throw new Error("recordMemoirVoiceSearchQuery was not mocked");
    },
    stopMemoirVoiceSearchRecording: () => {},
    ...voiceSearchExports,
  });
  mockModule("../services/tts-settings.ts", {
    readBookTTSSettings: () => ({ voice: "", speedRatio: 1 }),
    ...ttsExports,
  });

  return loadPageModule("../pages/book/index.ts");
}

test("book page summarizes chapter source count without exposing raw ids", () => {
  const { mod } = loadBookPage();

  assert.equal(
    mod.bookChapterSourceSummary({
      source_refs: [
        sampleRef("utterance-1", "外婆家的院子"),
        sampleRef("utterance-2", "第一次离家"),
        sampleRef("utterance-3", "第三段"),
      ],
    }),
    "本章素材：3 条收录讲述",
  );
});

test("book page renders chapter source counts in plan and drafting cards", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");
  const matches = wxml.match(/{{item\.sourceCount}} 条素材/g) || [];

  assert.equal(matches.length, 2);
});

test("book page keeps source rows read-only and splits chapter structure actions", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");

  assert.equal(wxml.includes("source-ref-action"), false);
  assert.equal(wxml.includes("移动到"), false);
  assert.equal(wxml.includes('bindchange="splitChapter"'), false);
  assert.equal(wxml.includes('bindtap="toggleSplitSource"'), true);
  assert.equal(wxml.includes('bindtap="splitSelectedChapterSources"'), true);
  assert.equal((wxml.match(/structure-action-row/g) || []).length, 4);
});

test("book page exposes a compact voice assistant entry", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");
  const wxss = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxss"), "utf8");

  assert.match(wxml, /成书语音助手/);
  assert.match(wxml, /听目录/);
  assert.match(wxml, /语音修改/);
  assert.match(wxml, /bindtap="playBookOutlineAudio"/);
  assert.match(wxml, /bindtap="startBookVoiceCommand"/);
  assert.match(wxml, /playingTarget === 'outline'/);
  assert.match(wxml, /bookVoiceCommandSummary/);
  assert.match(wxml, /book-voice-command-preview/);
  assert.match(wxml, /bindtap="executeBookVoiceCommand"/);
  assert.match(wxml, /bindtap="undoBookVoiceCommand"/);
  assert.match(wxss, /\.book-voice-assistant/);
  assert.match(wxss, /\.book-voice-command-preview/);
  assert.match(wxss, /#fff6f1/);
  assert.match(wxss, /#9f4a3c/);
});

test("book page builds outline narration for saved chapter titles", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  const chapters = mod.buildChapterViews(plan);
  chapters[0].editingTitle = "未保存的新标题";

  assert.equal(
    mod.bookOutlineNarrationText(plan.title, chapters),
    [
      "《外婆回忆录章节规划》现在有 2 章。",
      "第 1 章，外婆家的童年。本章收录 1 条语音素材。",
      "第 2 章，第一次离家。本章收录 1 条语音素材。",
      "你可以继续调整章节顺序、合并章节，或者说出想修改的地方。",
    ].join("\n"),
  );
});

test("book voice assistant blocks actions before a plan exists", async () => {
  const toastTitles = [];
  global.wx = {
    showToast(options) {
      toastTitles.push(options.title);
    },
  };
  const { page } = loadBookPage();
  const context = pageContext(page, { plan: null, chapters: [] });

  page.playBookOutlineAudio.call(context);
  assert.equal(context.data.bookVoiceAssistantStatusText, "先生成章节");

  await page.startBookVoiceCommand.call(context);
  assert.equal(context.data.bookVoiceAssistantStatusText, "先生成章节");
  assert.deepEqual(toastTitles, ["先生成章节规划", "先生成章节规划"]);
  delete global.wx;
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

test("book page stays on the book tab when login is required", async () => {
  const switches = [];
  global.wx = {
    switchTab(options) {
      switches.push(options.url);
    },
  };
  const { ApiError } = require("../services/request.ts");
  const { page } = loadBookPage({
    authExports: {
      getMemoirIdentity: async () => {
        throw new ApiError(401, "请先登录", {});
      },
    },
  });
  const context = pageContext(page);

  await page.loadBookHome.call(context);

  assert.equal(context.data.needsLogin, true);
  assert.equal(context.data.projectTitle, "我的回忆录");
  assert.equal(context.data.progressText, "登录后可以开始整理");
  assert.deepEqual(switches, []);
  delete global.wx;
});

test("book page asks logged-in users to create a memoir before loading book content", async () => {
  let planCalls = 0;
  const { page } = loadBookPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        email: "memoir-author@example.com",
        password_login_enabled: true,
        display_name: "作者",
        created_at: "2026-06-15T00:00:00",
        active_project_id: "",
        active_project: null,
        projects: [],
      }),
    },
    voiceApiExports: {
      getMemoirBookPlan: async () => {
        planCalls += 1;
        return samplePlan("confirmed");
      },
    },
  });
  const context = pageContext(page);

  await page.loadBookHome.call(context);

  assert.equal(context.data.needsLogin, false);
  assert.equal(context.data.projectId, "");
  assert.equal(context.data.projectTitle, "尚未创建回忆录");
  assert.equal(context.data.progressText, "还没有章节");
  assert.equal(planCalls, 0);
});

test("book page shows source coverage notice only when there are uncovered transcripts", () => {
  const { mod } = loadBookPage();

  assert.equal(mod.bookSourceCoverageNotice(samplePlan("confirmed"), sampleSourceCoverage(2)), "有 2 条新讲述尚未收录到书中");
  assert.equal(mod.bookSourceCoverageNotice(null, sampleSourceCoverage(1)), "有 1 条讲述记录可用于生成章节");
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
  const sourceActionOptions = mod.buildSourceActionPickerOptions(chapters, true);

  assert.equal(sources.length, 1);
  assert.equal(sources[0].utterance_id, "utterance-new-2");
  assert.equal(sources[0].previewText.endsWith("..."), true);
  assert.equal(sources[0].recordNumber, 4);
  assert.equal(sources[0].createdAtText, "06-17 09:00");
  assert.equal(sources[0].sourceRef.created_at, "2026-06-17T01:00:00.000Z");
  assert.deepEqual(options, [
    { label: "第 1 章 外婆家的童年", value: "chapter-1" },
    { label: "第 2 章 第一次离家", value: "chapter-2" },
  ]);
  assert.deepEqual(sourceActionOptions, [
    { label: "第 1 章 外婆家的童年", value: "chapter-1" },
    { label: "第 2 章 第一次离家", value: "chapter-2" },
    { label: "作为新章", value: "__new_chapter__" },
  ]);
});

test("book page builds chapter structure controls from source refs", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  plan.chapters[0].source_refs = [
    sampleRef("utterance-1", "外婆家的院子"),
    sampleRef("utterance-3", "春节做年糕"),
  ];

  const chapters = mod.buildChapterViews(plan);

  assert.equal(chapters[0].canMoveUp, false);
  assert.equal(chapters[0].canMoveDown, true);
  assert.equal(chapters[1].canMoveUp, true);
  assert.equal(chapters[0].sourceViews.length, 2);
  assert.equal(chapters[0].sourceViews[0].previewText, "外婆家的院子");
  assert.equal(chapters[0].sourceViews[0].recordNumber, 1);
  assert.equal(chapters[0].sourceViews[0].labelText, "语音记录 1 · 素材 1 · 06-17 08:00 · 6 字");
  assert.equal(chapters[0].sourceToggleText, "查看 2 条");
  assert.deepEqual(chapters[0].mergeTargetOptions, [
    { label: "第 2 章 第一次离家", value: "chapter-2" },
  ]);
  assert.equal(chapters[0].splitSelectedCount, 0);
  assert.equal(chapters[0].splitActionText, "拆出所选");
  assert.equal(chapters[0].canSplitSelectedSources, false);
});

test("book page toggles chapter source details", () => {
  const { page } = loadBookPage();
  const context = pageContext(page);
  const plan = samplePlan("confirmed");
  plan.chapters[0].source_refs = [
    sampleRef("utterance-1", "外婆家的院子"),
    sampleRef("utterance-3", "春节做年糕"),
  ];
  page.applyPlan.call(context, plan);

  assert.equal(context.data.chapters[0].sourceListExpanded, false);
  page.toggleChapterSources.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });

  assert.equal(context.data.chapters[0].sourceListExpanded, true);
  assert.equal(context.data.chapters[0].sourceToggleText, "收起素材");
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

test("book page stores uncovered source target before opening transcripts tab", () => {
  const switches = [];
  const storageWrites = [];
  global.wx = {
    setStorageSync(key, value) {
      storageWrites.push({ key, value });
    },
    switchTab(options) {
      switches.push(options.url);
    },
  };
  const { page } = loadBookPage();
  const context = pageContext(page);

  page.openTranscripts.call(context, {
    currentTarget: {
      dataset: {
        id: "utterance-new-1",
      },
    },
  });

  assert.equal(storageWrites[0].key, "pawtale_transcript_navigation_target");
  assert.equal(storageWrites[0].value.utterance_id, "utterance-new-1");
  assert.deepEqual(switches, ["/pages/transcripts/index"]);
  delete global.wx;
});

test("book page avoids placeholder title for generic uncovered transcript", () => {
  const { mod } = loadBookPage();
  const source = mod.buildUncoveredSourceViews(sampleSourceCoverage(1))[0];

  assert.equal(mod.titleFromUncoveredSource(source), "补记往事");
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
  assert.equal(chapters[0].sourceChangeNotice, "1 条新增讲述待补写");
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
  assert.equal(chapters[0].sourceChangeNotice, "1 条素材已删除，章节稿需要更新");
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
  assert.equal("summary" in submissions[0].data.chapters[0], false);
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

test("book page adds uncovered transcript as a new chapter", async () => {
  const calls = [];
  global.wx = { showToast() {}, switchTab() {} };
  const updatedPlan = samplePlan("confirmed");
  updatedPlan.chapters = [
    ...updatedPlan.chapters,
    {
      id: "chapter-3",
      plan_id: "plan-1",
      project_id: "project/1",
      title: "补记往事",
      sort_order: 2,
      source_refs: [sampleRef("utterance-new-1", "新增转写")],
      metadata_json: {},
      created_at: "2026-06-17T00:00:00.000Z",
      updated_at: "2026-06-17T00:00:00.000Z",
    },
  ];
  const { page } = loadBookPage({
    voiceApiExports: {
      updateMemoirBookPlan: async (projectId, data) => {
        calls.push({ projectId, data });
        return updatedPlan;
      },
      getMemoirBookChapterDraft: async () => null,
      getMemoirBookSourceCoverage: async () => sampleSourceCoverage(0),
      getMemoirBookOutput: async () => null,
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    projectTitle: "外婆回忆录",
  });
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applySourceCoverage.call(context, sampleSourceCoverage(1));

  await page.addUncoveredSourceAsChapter.call(context, {
    currentTarget: { dataset: { id: "utterance-new-1" } },
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].projectId, "project/1");
  assert.equal(calls[0].data.chapters.length, 3);
  assert.equal(calls[0].data.chapters[2].title, "补记往事");
  assert.equal(calls[0].data.chapters[2].source_refs[0].utterance_id, "utterance-new-1");
  assert.equal(context.data.chapters.length, 3);
  assert.equal(context.data.assigningSourceId, "");
  delete global.wx;
});

test("book page adds uncovered transcript as a new chapter from picker option", async () => {
  const calls = [];
  global.wx = { showToast() {}, switchTab() {} };
  const updatedPlan = samplePlan("confirmed");
  updatedPlan.chapters = [
    ...updatedPlan.chapters,
    {
      id: "chapter-3",
      plan_id: "plan-1",
      project_id: "project/1",
      title: "补记往事",
      sort_order: 2,
      source_refs: [sampleRef("utterance-new-1", "新增转写")],
      metadata_json: {},
      created_at: "2026-06-17T00:00:00.000Z",
      updated_at: "2026-06-17T00:00:00.000Z",
    },
  ];
  const { page } = loadBookPage({
    voiceApiExports: {
      assignMemoirBookSources: async () => {
        throw new Error("assignMemoirBookSources should not be called for new chapter option");
      },
      updateMemoirBookPlan: async (projectId, data) => {
        calls.push({ projectId, data });
        return updatedPlan;
      },
      getMemoirBookChapterDraft: async () => null,
      getMemoirBookSourceCoverage: async () => sampleSourceCoverage(0),
      getMemoirBookOutput: async () => null,
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    projectTitle: "外婆回忆录",
  });
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applySourceCoverage.call(context, sampleSourceCoverage(1));

  const newChapterOptionIndex = context.data.chapterPickerOptions.findIndex((option) => option.label === "作为新章");
  await page.assignUncoveredSource.call(context, {
    currentTarget: { dataset: { id: "utterance-new-1" } },
    detail: { value: newChapterOptionIndex },
  });

  assert.equal(newChapterOptionIndex, 2);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].data.chapters.length, 3);
  assert.equal(calls[0].data.chapters[2].title, "补记往事");
  assert.equal(context.data.assigningSourceId, "");
  delete global.wx;
});

test("book page saves edited confirmed chapter title", async () => {
  const calls = [];
  global.wx = { showToast() {}, switchTab() {} };
  const updatedPlan = samplePlan("confirmed");
  updatedPlan.chapters[0].title = "院子里的童年";
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      updateMemoirBookChapterTitle: async (projectId, chapterId, data) => {
        calls.push({ projectId, chapterId, data });
        return updatedPlan;
      },
      getMemoirBookChapterDraft: async () => sampleDraft(1, "confirmed"),
      getMemoirBookSourceCoverage: async () => sampleSourceCoverage(0),
      getMemoirBookOutput: async () => sampleBookOutput(),
    },
  });
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "confirmed"),
    "chapter-2": sampleDraft(1, "confirmed"),
  });

  page.changeChapterTitle.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
    detail: { value: "院子里的童年" },
  });
  await page.saveChapterTitle.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });

  assert.deepEqual(calls[0], {
    projectId: "project/1",
    chapterId: "chapter-1",
    data: { title: "院子里的童年" },
  });
  assert.equal(context.data.chapters[0].title, "院子里的童年");
  assert.equal(context.data.chapters[0].titleChanged, false);
  assert.equal(context.data.titleBusyTarget, "");
  delete global.wx;
});

test("book page acknowledges unchanged confirmed chapter title save", async () => {
  const toasts = [];
  const { page } = loadBookPage({
    voiceApiExports: {
      updateMemoirBookChapterTitle: async () => {
        throw new Error("updateMemoirBookChapterTitle should not be called for unchanged title");
      },
    },
  });
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
    switchTab() {},
  };
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"));

  await page.saveChapterTitle.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });

  assert.deepEqual(toasts[0], { title: "章名已保存", icon: "success" });
  assert.equal(context.data.chapters[0].titleChanged, false);
  delete global.wx;
});

test("book page regenerates confirmed chapter title through AI", async () => {
  const calls = [];
  global.wx = { showToast() {}, switchTab() {} };
  const updatedPlan = samplePlan("confirmed");
  updatedPlan.chapters[0].title = "外婆院中的日子";
  const { page } = loadBookPage({
    voiceApiExports: {
      generateMemoirBookChapterTitle: async (projectId, chapterId, data) => {
        calls.push({ projectId, chapterId, data });
        return updatedPlan;
      },
      getMemoirBookChapterDraft: async () => sampleDraft(1, "confirmed"),
      getMemoirBookSourceCoverage: async () => sampleSourceCoverage(0),
      getMemoirBookOutput: async () => sampleBookOutput(),
    },
  });
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"));

  await page.generateChapterTitle.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });

  assert.deepEqual(calls[0], {
    projectId: "project/1",
    chapterId: "chapter-1",
    data: {},
  });
  assert.equal(context.data.chapters[0].title, "外婆院中的日子");
  assert.equal(context.data.titleBusyTarget, "");
  delete global.wx;
});

test("book page deletes confirmed chapter through plan structure update", async () => {
  const calls = [];
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
    showToast() {},
    switchTab() {},
  };
  const updatedPlan = samplePlan("confirmed");
  updatedPlan.chapters = [updatedPlan.chapters[1]];
  updatedPlan.chapters[0].sort_order = 0;
  const { page } = loadBookPage({
    voiceApiExports: {
      updateMemoirBookPlan: async (projectId, data) => {
        calls.push({ projectId, data });
        return updatedPlan;
      },
      getMemoirBookChapterDraft: async () => null,
      getMemoirBookSourceCoverage: async () => sampleSourceCoverage(1),
      getMemoirBookOutput: async () => null,
    },
  });
  const context = pageContext(page, {
    projectId: "project/1",
    projectTitle: "外婆回忆录",
  });
  page.applyPlan.call(context, samplePlan("confirmed"));

  await page.deleteConfirmedChapter.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].data.chapters.length, 1);
  assert.equal(calls[0].data.chapters[0].id, "chapter-2");
  assert.equal(context.data.chapters.length, 1);
  assert.equal(context.data.chapters[0].id, "chapter-2");
  delete global.wx;
});

test("book page reorders chapters through plan structure update", async () => {
  const calls = [];
  global.wx = { showToast() {}, switchTab() {} };
  const { page } = loadBookPage({
    voiceApiExports: {
      updateMemoirBookPlan: async (projectId, data) => {
        calls.push({ projectId, data });
        return planFromChapterUpdates("confirmed", data.chapters);
      },
      getMemoirBookChapterDraft: async () => null,
      getMemoirBookSourceCoverage: async () => sampleSourceCoverage(0),
      getMemoirBookOutput: async () => null,
    },
  });
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"));

  await page.moveChapter.call(context, {
    currentTarget: { dataset: { id: "chapter-1", direction: "down" } },
  });

  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].data.chapters.map((chapter) => chapter.id), ["chapter-2", "chapter-1"]);
  assert.deepEqual(context.data.chapters.map((chapter) => chapter.id), ["chapter-2", "chapter-1"]);
  delete global.wx;
});

test("book page splits selected chapter sources into a new chapter", async () => {
  const calls = [];
  global.wx = { showToast() {}, switchTab() {} };
  const plan = samplePlan("confirmed");
  plan.chapters[0].source_refs = [
    sampleRef("utterance-1", "外婆家的院子"),
    sampleRef("utterance-3", "春节做年糕"),
  ];
  const { page } = loadBookPage({
    voiceApiExports: {
      updateMemoirBookPlan: async (projectId, data) => {
        calls.push({ projectId, data });
        return planFromChapterUpdates("confirmed", data.chapters);
      },
      getMemoirBookChapterDraft: async () => null,
      getMemoirBookSourceCoverage: async () => sampleSourceCoverage(0),
      getMemoirBookOutput: async () => null,
    },
  });
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, plan);

  page.toggleSplitSource.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });
  assert.equal(context.data.chapters[0].splitSelectedCount, 0);

  page.toggleSplitSource.call(context, {
    currentTarget: { dataset: { id: "chapter-1", utteranceId: "utterance-3" } },
  });

  assert.equal(context.data.chapters[0].sourceViews[1].splitSelected, true);
  assert.equal(context.data.chapters[0].splitSelectedCount, 1);
  assert.equal(context.data.chapters[0].canSplitSelectedSources, true);

  await page.splitSelectedChapterSources.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].data.chapters.length, 3);
  assert.equal(calls[0].data.chapters[0].id, "chapter-1");
  assert.equal(calls[0].data.chapters[0].source_refs[0].utterance_id, "utterance-1");
  assert.equal(calls[0].data.chapters[1].id, undefined);
  assert.equal(calls[0].data.chapters[1].title, "外婆家的童年（拆出）");
  assert.equal(calls[0].data.chapters[1].source_refs[0].utterance_id, "utterance-3");
  assert.equal(calls[0].data.chapters[2].id, "chapter-2");
  assert.equal(context.data.splitSourceSelections["chapter-1"], undefined);
  delete global.wx;
});

test("book page merges chapters and regenerates title plus draft for confirmed plans", async () => {
  const calls = [];
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
    showToast() {},
    switchTab() {},
  };
  const { page } = loadBookPage({
    voiceApiExports: {
      updateMemoirBookPlan: async (projectId, data) => {
        calls.push({ kind: "update", projectId, data });
        return planFromChapterUpdates("confirmed", data.chapters);
      },
      generateMemoirBookChapterTitle: async (projectId, chapterId, data) => {
        calls.push({ kind: "title", projectId, chapterId, data });
        const updated = planFromChapterUpdates("confirmed", calls[0].data.chapters);
        updated.chapters[0].title = "童年里的离别";
        return updated;
      },
      generateMemoirBookChapterDraft: async (projectId, chapterId, data) => {
        calls.push({ kind: "draft", projectId, chapterId, data });
        return sampleDraft(2, "draft", "合并后的章节正文");
      },
      getMemoirBookChapterDraft: async () => null,
      getMemoirBookSourceCoverage: async () => sampleSourceCoverage(0),
      getMemoirBookOutput: async () => null,
    },
  });
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "confirmed", "童年正文"),
    "chapter-2": sampleDraft(1, "confirmed", "离家正文"),
  });

  await page.mergeChapterInto.call(context, {
    currentTarget: { dataset: { id: "chapter-2" } },
    detail: { value: 0 },
  });

  assert.deepEqual(calls.map((call) => call.kind), ["update", "title", "draft"]);
  assert.equal(calls[0].data.chapters.length, 1);
  assert.equal(calls[0].data.chapters[0].id, "chapter-1");
  assert.deepEqual(
    calls[0].data.chapters[0].source_refs.map((ref) => ref.utterance_id),
    ["utterance-1", "utterance-2"],
  );
  assert.equal("summary" in calls[0].data.chapters[0], false);
  assert.deepEqual(calls[2].data, { force_regenerate: false });
  assert.equal(context.data.chapters[0].title, "童年里的离别");
  assert.equal(context.data.chapters[0].draftContent, "合并后的章节正文");
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

test("book page sends book-specific TTS voice and speed", async () => {
  const calls = [];
  const { page } = loadBookPage({
    voiceApiExports: {
      createMemoirBookChapterDraftTTS: async (projectId, chapterId, data) => {
        calls.push({ kind: "chapter", projectId, chapterId, data });
        return sampleAudioResponse();
      },
      createMemoirBookOutputTTS: async (projectId, data) => {
        calls.push({ kind: "book", projectId, data });
        return sampleAudioResponse();
      },
      createMemoirBookPlanOutlineTTS: async (projectId, data) => {
        calls.push({ kind: "outline", projectId, data });
        return sampleAudioResponse();
      },
    },
    ttsExports: {
      readBookTTSSettings: () => ({
        voice: "zh_male_m191_uranus_bigtts",
        speedRatio: 1.5,
      }),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });

  await page.requestBookAudio.call(context, "book");
  await page.requestBookAudio.call(context, "outline");
  await page.requestBookAudio.call(context, "chapter:chapter-1");

  assert.deepEqual(calls, [
    {
      kind: "book",
      projectId: "project/1",
      data: {
        voice: "zh_male_m191_uranus_bigtts",
        speed_ratio: 1.5,
      },
    },
    {
      kind: "outline",
      projectId: "project/1",
      data: {
        voice: "zh_male_m191_uranus_bigtts",
        speed_ratio: 1.5,
      },
    },
    {
      kind: "chapter",
      projectId: "project/1",
      chapterId: "chapter-1",
      data: {
        voice: "zh_male_m191_uranus_bigtts",
        speed_ratio: 1.5,
      },
    },
  ]);
});

test("book page plays outline audio through the voice assistant", async () => {
  const calls = [];
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      createMemoirBookPlanOutlineTTS: async (projectId, data) => {
        calls.push({ kind: "outline", projectId, data });
        return sampleAudioResponse();
      },
    },
  });
  const plan = samplePlan("confirmed");
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, plan);
  context.playAudioFiles = async (response, target, token, activeText) => {
    calls.push({ kind: "play", response, target, token, activeText });
    context.setData({ playingTarget: target, bookTTSActiveTarget: target });
  };

  await page.playBookOutlineAudio.call(context);

  assert.deepEqual(calls[0], {
    kind: "outline",
    projectId: "project/1",
    data: {},
  });
  assert.equal(calls[1].target, "outline");
  assert.equal(
    calls[1].activeText,
    mod.bookOutlineNarrationText(plan.title, context.data.chapters),
  );
  assert.equal(context.data.ttsBusyTarget, "");
  assert.equal(context.data.bookVoiceAssistantStatusText, "我可以帮你听和改");
});

test("book page restores assistant status when outline audio stops", () => {
  const { page } = loadBookPage();
  const context = pageContext(page, {
    stage: "drafting",
    playingTarget: "outline",
    bookVoiceAssistantStatusText: "正在朗读目录",
  });

  page.playBookOutlineAudio.call(context);

  assert.equal(context.data.playingTarget, "");
  assert.equal(context.data.bookVoiceAssistantStatusText, "我可以帮你听和改");
});

test("book page records and previews a voice edit command", async () => {
  const calls = [];
  global.wx = { showToast(options) { calls.push({ kind: "toast", title: options.title }); } };
  const { page } = loadBookPage({
    voiceApiExports: {
      parseMemoirBookVoiceCommand: async (projectId, data) => {
        calls.push({ kind: "parse", projectId, data });
        return sampleBookVoiceCommandPreview();
      },
    },
    voiceSearchExports: {
      recordMemoirVoiceSearchQuery: async (options) => {
        calls.push({ kind: "record", emptyTranscriptMessage: options.emptyTranscriptMessage });
        options.onRecordingEnd?.();
        return "把第一章改名为童年往事";
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));

  await page.startBookVoiceCommand.call(context);

  assert.deepEqual(calls[0], {
    kind: "record",
    emptyTranscriptMessage: "没有识别到修改内容，请换个说法再试",
  });
  assert.deepEqual(calls[1], {
    kind: "parse",
    projectId: "project/1",
    data: { text: "把第一章改名为童年往事" },
  });
  assert.equal(context.data.bookVoiceCommandText, "把第一章改名为童年往事");
  assert.equal(context.data.bookVoiceCommandIntentLabel, "修改章名");
  assert.equal(context.data.bookVoiceCommandSummary, "准备把「外婆家的童年」改名为「童年往事」。");
  assert.equal(context.data.bookVoiceCommandRequiresConfirmation, true);
  assert.equal(context.data.bookVoiceAssistantStatusText, "已理解，等待确认");
  assert.equal(context.data.bookVoiceAssistantListening, false);
  assert.equal(context.data.bookVoiceAssistantBusy, false);
  assert.deepEqual(calls[2], { kind: "toast", title: "已识别" });
  delete global.wx;
});

test("book page executes a confirmed voice rename with undo snapshot", async () => {
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
  };
  const { page } = loadBookPage();
  const plan = samplePlan("confirmed");
  const calls = [];
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, plan);
  page.applyBookVoiceCommandPreview.call(context, sampleBookVoiceCommandPreview());
  context.updatePlanStructure = async (chapters, toastTitle) => {
    calls.push({ chapters, toastTitle });
    return planFromChapterUpdates("confirmed", chapters);
  };

  await page.executeBookVoiceCommand.call(context);

  assert.equal(calls[0].toastTitle, "已按语音改名");
  assert.equal(calls[0].chapters[0].id, "chapter-1");
  assert.equal(calls[0].chapters[0].title, "童年往事");
  assert.equal(calls[0].chapters[1].title, "第一次离家");
  assert.equal(context.data.bookVoiceCommandUndoText, "撤回语音改名");
  assert.equal(context.data.bookVoiceCommandUndoSnapshot.chapters[0].title, "外婆家的童年");
  assert.equal(context.data.bookVoiceAssistantStatusText, "已执行，可撤回");
  delete global.wx;
});

test("book page executes and undoes a voice chapter move", async () => {
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
  };
  const { page } = loadBookPage();
  const calls = [];
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applyBookVoiceCommandPreview.call(context, sampleBookVoiceCommandPreview({
    original_text: "把第二章上移",
    intent: "move_chapter",
    intent_label: "移动章节",
    summary: "准备把「第一次离家」上移。",
    operations: [
      {
        type: "move_chapter",
        chapter_id: "chapter-2",
        chapter_title: "第一次离家",
        direction: "up",
      },
    ],
  }));
  context.updatePlanStructure = async (chapters, toastTitle) => {
    calls.push({ chapters, toastTitle });
    return planFromChapterUpdates("confirmed", chapters);
  };

  await page.executeBookVoiceCommand.call(context);
  await page.undoBookVoiceCommand.call(context);

  assert.equal(calls[0].toastTitle, "已按语音调整顺序");
  assert.deepEqual(calls[0].chapters.map((chapter) => chapter.id), ["chapter-2", "chapter-1"]);
  assert.equal(calls[1].toastTitle, "已撤回语音修改");
  assert.deepEqual(calls[1].chapters.map((chapter) => chapter.id), ["chapter-1", "chapter-2"]);
  assert.equal(context.data.bookVoiceCommandUndoText, "");
  assert.equal(context.data.bookVoiceCommandUndoSnapshot, null);
  assert.equal(context.data.bookVoiceAssistantStatusText, "已撤回");
  delete global.wx;
});

test("book page keeps unsupported voice commands as preview only", () => {
  const { page } = loadBookPage();
  const context = pageContext(page);

  page.applyBookVoiceCommandPreview.call(context, sampleBookVoiceCommandPreview({
    intent: "merge_chapters",
    intent_label: "合并章节",
    summary: "准备把「外婆家的童年」合并到「第一次离家」。",
    operations: [
      {
        type: "merge_chapters",
        chapter_id: "chapter-1",
        chapter_title: "外婆家的童年",
        target_chapter_id: "chapter-2",
        target_chapter_title: "第一次离家",
      },
    ],
  }));

  assert.equal(context.data.bookVoiceCommandCanExecute, false);
  assert.equal(context.data.bookVoiceCommandActionText, "暂不执行");
});

test("book page only enables executable voice commands with required fields", () => {
  const { mod } = loadBookPage();

  assert.equal(mod.canExecuteBookVoiceCommand(sampleBookVoiceCommandPreview({
    intent: "rename_chapter",
    operations: [{ type: "rename_chapter", chapter_id: "chapter-1" }],
  })), false);
  assert.equal(mod.canExecuteBookVoiceCommand(sampleBookVoiceCommandPreview({
    intent: "move_chapter",
    operations: [{ type: "move_chapter", chapter_id: "chapter-1", direction: "later" }],
  })), false);
  assert.equal(mod.canExecuteBookVoiceCommand(sampleBookVoiceCommandPreview({
    intent: "read_chapter",
    operations: [{ type: "read_chapter", chapter_id: "chapter-1" }],
  })), true);
});

test("book page replaces old voice undo state when applying a new preview", () => {
  const { page } = loadBookPage();
  const context = pageContext(page);
  context.setData({
    bookVoiceCommandUndoText: "撤回语音改名",
    bookVoiceCommandUndoSnapshot: {
      planTitle: "旧目录",
      chapters: [],
    },
  });

  page.applyBookVoiceCommandPreview.call(context, sampleBookVoiceCommandPreview());

  assert.equal(context.data.bookVoiceCommandUndoText, "");
  assert.equal(context.data.bookVoiceCommandUndoSnapshot, null);
});

test("book page stops active voice edit recording on second tap", async () => {
  const stops = [];
  const { page } = loadBookPage({
    voiceSearchExports: {
      stopMemoirVoiceSearchRecording: () => stops.push("stop"),
    },
  });
  const context = pageContext(page, {
    bookVoiceAssistantListening: true,
    bookVoiceAssistantStatusText: "我在听，请说想怎么改",
  });

  await page.startBookVoiceCommand.call(context);

  assert.deepEqual(stops, ["stop"]);
  assert.equal(context.data.bookVoiceAssistantStatusText, "正在保存录音");
});

test("book page splits active TTS text for highlighting", () => {
  const { page } = loadBookPage();
  const context = pageContext(page, {
    bookTTSActiveText: "外婆回忆录",
    bookTTSDurationSeconds: 1.2,
    bookTTSAlignment: [
      {
        text: "外婆",
        start_ms: 0,
        end_ms: 600,
        text_start: 0,
        text_end: 2,
      },
      {
        text: "回忆录",
        start_ms: 600,
        end_ms: 1200,
        text_start: 2,
        text_end: 5,
      },
    ],
  });

  page.updateBookTTSProgress.call(context, 0.8, 1.2);

  assert.equal(context.data.bookTTSSpokenText, "外婆");
  assert.equal(context.data.bookTTSCurrentText, "回忆录");
  assert.equal(context.data.bookTTSRemainingText, "");
  assert.equal(context.data.bookTTSProgressPercent, 67);
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

test("book page supplements pending sources without force regeneration", async () => {
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
    data: { force_regenerate: false },
  });
  assert.equal(context.data.chapters[0].pendingSourceCount, 0);
  assert.equal(context.data.chapters[0].draftActionText, "重新生成");
  assert.deepEqual(context.data.chapters[0].metadata_json, {});
  delete global.wx;
});

test("book page force regenerates when source removals also need cleanup", async () => {
  const calls = [];
  global.wx = { showToast() {} };
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      generateMemoirBookChapterDraft: async (projectId, chapterId, data) => {
        calls.push({ projectId, chapterId, data });
        return sampleDraft(2, "draft", "整理后的正文");
      },
    },
  });
  const plan = samplePlan("confirmed");
  plan.chapters[0].metadata_json = {
    pending_source_utterance_ids: ["utterance-new-1"],
    removed_source_utterance_ids: ["utterance-removed-1"],
    source_assignment_updated_at: "2026-06-17T00:00:00+00:00",
    source_refs_removed_at: "2026-06-17T00:00:00+00:00",
  };
  const context = pageContext(page, {
    projectId: "project/1",
    chapters: mod.buildChapterViews(plan, {
      "chapter-1": sampleDraft(1, "draft", "第一版正文", { status_reason: "sources_changed" }),
    }),
  });

  assert.equal(context.data.chapters[0].draftActionText, "整理本章");

  await page.generateChapterDraft.call(context, {
    currentTarget: { dataset: { id: "chapter-1" } },
  });

  assert.deepEqual(calls[0], {
    projectId: "project/1",
    chapterId: "chapter-1",
    data: { force_regenerate: true },
  });
  assert.equal(context.data.chapters[0].draftContent, "整理后的正文");
  assert.equal(context.data.chapters[0].draftChanged, false);
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
      if (key === "pawtale_ai_model_settings") {
        return { model: "doubao-seed-2-0-pro-260215" };
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
      if (
        options.url.endsWith("/draft/tts")
        || options.url.endsWith("/outline/tts")
        || options.url.endsWith("/book-output/tts")
      ) {
        options.success({ statusCode: 200, data: sampleAudioResponse(), cookies: [], header: {} });
        return;
      }
      if (options.url.endsWith("/book-voice-command/parse")) {
        options.success({ statusCode: 200, data: sampleBookVoiceCommandPreview(), cookies: [], header: {} });
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
    createMemoirBookPlanOutlineTTS,
    generateMemoirBookChapterDraft,
    generateMemoirBookChapterTitle,
    generateMemoirBookOutput,
    generateMemoirBookPlan,
    getMemoirBookChapterDraft,
    getMemoirBookOutput,
    getMemoirBookPlan,
    getMemoirBookSourceCoverage,
    parseMemoirBookVoiceCommand,
    updateMemoirBookChapterTitle,
    updateMemoirBookPlan,
    updateMemoirBookChapterDraft,
  } = require("../services/memoir-voice-api.ts");

  await getMemoirBookPlan("project/1");
  await generateMemoirBookPlan("project/1", { max_chapters: 6 });
  await confirmMemoirBookPlan("project/1", { title: "目录", chapters: [] });
  await updateMemoirBookPlan("project/1", { title: "目录", chapters: [] });
  await getMemoirBookSourceCoverage("project/1");
  await updateMemoirBookChapterTitle("project/1", "chapter/1", { title: "新章名" });
  await generateMemoirBookChapterTitle("project/1", "chapter/1", {});
  await getMemoirBookChapterDraft("project/1", "chapter/1");
  await generateMemoirBookChapterDraft("project/1", "chapter/1", { force_regenerate: true });
  await updateMemoirBookChapterDraft("project/1", "chapter/1", { content: "正文" });
  await confirmMemoirBookChapterDraft("project/1", "chapter/1");
  await createMemoirBookChapterDraftTTS("project/1", "chapter/1", {});
  await createMemoirBookPlanOutlineTTS("project/1", {});
  await parseMemoirBookVoiceCommand("project/1", { text: "把第一章改名为童年往事" });
  await getMemoirBookOutput("project/1");
  await generateMemoirBookOutput("project/1", { force_regenerate: true });
  await createMemoirBookOutputTTS("project/1", {});

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan");
  assert.equal(apiRequests[0].method, "GET");
  assert.equal(apiRequests[1].url, "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/generate");
  assert.equal(apiRequests[1].method, "POST");
  assert.deepEqual(apiRequests[1].data, { max_chapters: 6, ai_model: "doubao-seed-2-0-pro-260215" });
  assert.equal(apiRequests[2].method, "PUT");
  assert.equal(apiRequests[3].method, "PATCH");
  assert.deepEqual(apiRequests[3].data, { title: "目录", chapters: [] });
  assert.equal(
    apiRequests[4].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-source-coverage",
  );
  assert.equal(apiRequests[4].method, "GET");
  assert.equal(
    apiRequests[5].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/chapters/chapter%2F1/title",
  );
  assert.equal(apiRequests[5].method, "PATCH");
  assert.deepEqual(apiRequests[5].data, { title: "新章名" });
  assert.equal(
    apiRequests[6].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/chapters/chapter%2F1/title/generate",
  );
  assert.equal(apiRequests[6].method, "POST");
  assert.deepEqual(apiRequests[6].data, { ai_model: "doubao-seed-2-0-pro-260215" });
  assert.equal(
    apiRequests[7].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/chapters/chapter%2F1/draft",
  );
  assert.equal(apiRequests[7].method, "GET");
  assert.equal(apiRequests[8].method, "POST");
  assert.deepEqual(apiRequests[8].data, { force_regenerate: true, ai_model: "doubao-seed-2-0-pro-260215" });
  assert.equal(apiRequests[9].method, "PATCH");
  assert.deepEqual(apiRequests[9].data, { content: "正文" });
  assert.equal(
    apiRequests[10].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/chapters/chapter%2F1/draft/confirm",
  );
  assert.equal(apiRequests[10].method, "POST");
  assert.equal(
    apiRequests[11].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/chapters/chapter%2F1/draft/tts",
  );
  assert.equal(apiRequests[11].method, "POST");
  assert.equal(
    apiRequests[12].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/outline/tts",
  );
  assert.equal(apiRequests[12].method, "POST");
  assert.equal(
    apiRequests[13].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-voice-command/parse",
  );
  assert.equal(apiRequests[13].method, "POST");
  assert.deepEqual(apiRequests[13].data, {
    text: "把第一章改名为童年往事",
    ai_model: "doubao-seed-2-0-pro-260215",
  });
  assert.equal(
    apiRequests[14].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output",
  );
  assert.equal(apiRequests[14].method, "GET");
  assert.equal(
    apiRequests[15].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output/generate",
  );
  assert.equal(apiRequests[15].method, "POST");
  assert.deepEqual(apiRequests[15].data, { force_regenerate: true });
  assert.equal(
    apiRequests[16].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output/tts",
  );
  assert.equal(apiRequests[16].method, "POST");
  assert.equal(apiRequests[16].header.Cookie, "pawtale_session=abc");
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
    ai_model: "deepseek-v4-pro",
  });
  assert.equal(apiRequests[0].header.Cookie, "pawtale_session=abc");
  delete global.wx;
});

test("book source move API calls narrow backend route", async () => {
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
  const { moveMemoirBookSource } = require("../services/memoir-voice-api.ts");

  await moveMemoirBookSource("project/1", {
    source_chapter_id: "chapter/1",
    target_chapter_id: "chapter/2",
    utterance_id: "utterance/1",
  });

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-source-moves",
  );
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, {
    source_chapter_id: "chapter/1",
    target_chapter_id: "chapter/2",
    utterance_id: "utterance/1",
    ai_model: "deepseek-v4-pro",
  });
  assert.equal(apiRequests[0].header.Cookie, "pawtale_session=abc");
  delete global.wx;
});
