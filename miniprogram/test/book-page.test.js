require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { clearModule, loadPageModule, mockModule, pageContext } = require("./page-harness");

const {
  sampleAudioResponse,
  sampleAutomationEvent,
  sampleBookManuscript,
  sampleBookOutput,
  sampleBookVoiceCommandExecuteResponse,
  sampleBookVoiceCommandPreview,
  sampleDraft,
  samplePlan,
  sampleRef,
  sampleSourceCoverage,
  sampleStructuredBookContent,
} = require("./book-fixtures");

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

async function flushAsyncWork(rounds = 6) {
  for (let index = 0; index < rounds; index += 1) {
    await Promise.resolve();
  }
}

function streamingAsrClientForFinal(calls, finalText, partialText = finalText) {
  return class {
    constructor(options) {
      this.options = options;
      calls.push({ kind: "client", gatewayUrl: options.gatewayUrl });
    }

    async connect() {
      calls.push({ kind: "connect" });
      this.options.callbacks.onStatus("ready");
    }

    startRecording(options) {
      calls.push({ kind: "record", options });
      this.options.callbacks.onStatus("listening");
      if (partialText) {
        this.options.callbacks.onPartial(partialText);
      }
      void Promise.resolve(this.options.callbacks.onFinal({
        type: "asr.final",
        text: finalText,
        start_ms: 0,
        end_ms: 1200,
      })).then(() => {
        this.options.callbacks.onEvent({ type: "audio.ended", state: "ready" });
        this.options.callbacks.onStatus("ready");
      });
      return true;
    }

    stopRecording() {
      calls.push({ kind: "stop" });
      this.options.callbacks.onStatus("stopping");
    }

    close() {
      calls.push({ kind: "close" });
    }

    configureAutoStop() {}
  };
}

function streamingAsrClientForNoVoice(calls) {
  return class {
    constructor(options) {
      this.options = options;
    }

    async connect() {
      this.options.callbacks.onStatus("ready");
    }

    startRecording(options) {
      calls.push({ kind: "record", options });
      this.options.callbacks.onStatus("listening");
      this.options.callbacks.onCaptureStats({
        state: "recording",
        frameCount: 0,
        audioBytesSent: 0,
        recordingElapsedMs: 3500,
        audioLevel: 0,
        silentElapsedMs: 3500,
        autoStopped: true,
        autoStopReason: "no_voice",
      });
      this.options.callbacks.onEvent({ type: "audio.ended", state: "ready" });
      this.options.callbacks.onStatus("ready");
      return true;
    }

    stopRecording() {
      calls.push({ kind: "stop" });
    }

    close() {}

    configureAutoStop() {}
  };
}

function bookVoiceTurnRuntimeMock(calls, {
  preview = sampleBookVoiceCommandPreview(),
  text = preview.reply_text || "我理解了，准备把「外婆家的童年」改名为「童年往事」。 如果没问题，说确认执行，我再改。",
  executionResult = null,
  playbackError = null,
  interruptResult = null,
} = {}) {
  return class {
    constructor(voiceSessionId, callbacks, options) {
      this.voiceSessionId = voiceSessionId;
      this.callbacks = callbacks;
      this.options = options;
      calls.push({
        kind: "turn-runtime",
        voiceSessionId,
        streamUrl: options?.createStreamClient?.(voiceSessionId, {})?.options?.streamUrl,
      });
    }

    async start(options) {
      calls.push({ kind: "turn-start", options });
      if (interruptResult) {
        return interruptResult;
      }
      this.callbacks.onStarted?.({
        type: "turn.started",
        turn_id: "turn-1",
        voice_session_id: this.voiceSessionId,
        state: "streaming",
      });
      this.callbacks.onCommandPreview?.({
        type: "command.preview",
        turn_id: "turn-1",
        voice_session_id: this.voiceSessionId,
        project_id: "project/1",
        command_preview: preview,
      });
      this.callbacks.onTextDelta?.({
        type: "text.delta",
        turn_id: "turn-1",
        voice_session_id: this.voiceSessionId,
        text_delta: text,
        text,
      });
      this.callbacks.onAudioFrame?.({
        turnId: "turn-1",
        sequence: 1,
        audio: new Uint8Array([1, 2, 3]),
        mimeType: "audio/L16",
        sampleRate: 16000,
        channels: 1,
        bitsPerSample: 16,
      });
      this.callbacks.onAudioFrameScheduled?.({
        frameIndex: 0,
        startAtSeconds: 10,
        durationSeconds: 1,
        ended: Promise.resolve(),
      });
      if (playbackError) {
        this.callbacks.onPlaybackError?.(playbackError);
      } else {
        this.callbacks.onAudioDrained?.({
          type: "audio.done",
          turn_id: "turn-1",
          voice_session_id: this.voiceSessionId,
          frame_count: 1,
          duration_seconds: 1,
        });
      }
      const completed = {
        type: "turn.completed",
        turn_id: "turn-1",
        voice_session_id: this.voiceSessionId,
        project_id: "project/1",
        state: "completed",
        message_id: "turn-1",
        audio_file_id: null,
        text,
        primary_intent: preview.intent,
        intent_tags: [preview.intent],
        expects_answer: true,
        command_preview: preview,
        execution_result: executionResult,
      };
      this.callbacks.onCompleted?.(completed);
      return { completed, playbackError };
    }

    close() {
      calls.push({ kind: "turn-close" });
    }

    interrupt() {
      calls.push({ kind: "turn-interrupt" });
    }

    stats() {
      return {
        currentTimeSeconds: 10.5,
        segmentCount: 1,
        underrunCount: 0,
        nextPlayTimeSeconds: 11,
        queuedAheadSeconds: 0.5,
      };
    }
  };
}

function installConfirmingWx(extra = {}) {
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
    ...extra,
  };
}

function bookVoiceCommandExecutionHarness(responseFactory, voiceApiExports = {}) {
  const calls = [];
  const { page } = loadBookPage({
    voiceApiExports: {
      executeMemoirBookVoiceCommand: async (projectId, data) => {
        calls.push({ projectId, data });
        return responseFactory(projectId, data);
      },
      ...voiceApiExports,
    },
  });
  return {
    page,
    context: pageContext(page, { projectId: "project/1" }),
    calls,
  };
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

function planFromStructureCommand(status, command) {
  const base = samplePlan(status);
  if (command.type === "create_chapter_from_source") {
    return {
      ...base,
      chapters: [
        ...base.chapters,
        {
          id: "chapter-3",
          plan_id: "plan-1",
          project_id: "project/1",
          title: "补记往事",
          sort_order: 2,
          source_refs: [sampleRef(command.utterance_id, "新增转写")],
          metadata_json: {},
          created_at: "2026-06-17T00:00:00.000Z",
          updated_at: "2026-06-17T00:00:00.000Z",
        },
      ],
    };
  }
  if (command.type === "delete_chapter") {
    return {
      ...base,
      chapters: base.chapters
        .filter((chapter) => chapter.id !== command.chapter_id)
        .map((chapter, index) => ({ ...chapter, sort_order: index })),
    };
  }
  if (command.type === "move_chapter") {
    const chapters = [...base.chapters];
    const fromIndex = chapters.findIndex((chapter) => chapter.id === command.chapter_id);
    const moving = chapters[fromIndex];
    if (command.direction) {
      const toIndex = command.direction === "up" ? fromIndex - 1 : fromIndex + 1;
      [chapters[fromIndex], chapters[toIndex]] = [chapters[toIndex], chapters[fromIndex]];
      return { ...base, chapters: chapters.map((chapter, index) => ({ ...chapter, sort_order: index })) };
    }
    const remaining = chapters.filter((chapter) => chapter.id !== command.chapter_id);
    let toIndex = remaining.length;
    if (command.target_position === "first") {
      toIndex = 0;
    } else if (typeof command.target_index === "number") {
      toIndex = Math.min(command.target_index - 1, remaining.length);
    } else if (command.relative_chapter_id) {
      const relativeIndex = remaining.findIndex((chapter) => chapter.id === command.relative_chapter_id);
      toIndex = relativeIndex + (command.relative_position === "after" ? 1 : 0);
    }
    remaining.splice(toIndex, 0, moving);
    return { ...base, chapters: remaining.map((chapter, index) => ({ ...chapter, sort_order: index })) };
  }
  if (command.type === "merge_chapters") {
    const source = base.chapters.find((chapter) => chapter.id === command.chapter_id);
    return {
      ...base,
      chapters: base.chapters
        .filter((chapter) => chapter.id !== command.chapter_id)
        .map((chapter, index) => (
          chapter.id === command.target_chapter_id && source
            ? {
              ...chapter,
              sort_order: index,
              source_refs: [...chapter.source_refs, ...source.source_refs],
            }
            : { ...chapter, sort_order: index }
        )),
    };
  }
  if (command.type === "split_chapter") {
    const source = base.chapters.find((chapter) => chapter.id === command.chapter_id);
    if (!source) {
      return base;
    }
    const selected = new Set(command.utterance_ids || []);
    const keptRefs = source.source_refs.filter((ref) => !selected.has(ref.utterance_id));
    const splitRefs = source.source_refs.filter((ref) => selected.has(ref.utterance_id));
    return {
      ...base,
      chapters: [
        { ...source, source_refs: keptRefs, sort_order: 0 },
        {
          id: "chapter-3",
          plan_id: "plan-1",
          project_id: "project/1",
          title: command.title,
          sort_order: 1,
          source_refs: splitRefs,
          metadata_json: {},
          created_at: "2026-06-17T00:00:00.000Z",
          updated_at: "2026-06-17T00:00:00.000Z",
        },
        { ...base.chapters[1], sort_order: 2 },
      ],
    };
  }
  if (command.type === "rename_chapter") {
    return {
      ...base,
      chapters: base.chapters.map((chapter) => (
        chapter.id === command.chapter_id
          ? { ...chapter, title: command.new_title || command.title }
          : chapter
      )),
    };
  }
  return base;
}

function loadBookPage({
  authExports = {},
  voiceApiExports = {},
  voiceSearchExports = {},
  streamingAsrExports = {},
  voiceAssistantTurnRuntimeExports = {},
  voiceAssistantTurnStreamExports = {},
  ttsExports = {},
  voiceAuthorizationExports = {},
  wechatPrivacyExports = {},
} = {}) {
  mockModule("../services/auth.ts", {
    getMemoirIdentity: async () => {
      throw new Error("getMemoirIdentity was not mocked");
    },
    ...authExports,
  });
  mockModule("../services/memoir-voice-api.ts", {
    applyMemoirBookPlanStructureCommand: async () => {
      throw new Error("applyMemoirBookPlanStructureCommand was not mocked");
    },
    assignMemoirBookSources: async () => {
      throw new Error("assignMemoirBookSources was not mocked");
    },
    confirmMemoirBookPlan: async () => {
      throw new Error("confirmMemoirBookPlan was not mocked");
    },
    createMemoirBookChapterDraftTTS: async () => {
      throw new Error("createMemoirBookChapterDraftTTS was not mocked");
    },
    createMemoirBookPlanOutlineTTS: async () => {
      throw new Error("createMemoirBookPlanOutlineTTS was not mocked");
    },
    assembleMemoirBookDraftBlocks: async () => {
      throw new Error("assembleMemoirBookDraftBlocks was not mocked");
    },
    executeMemoirBookVoiceCommand: async () => {
      throw new Error("executeMemoirBookVoiceCommand was not mocked");
    },
    downloadMemoirVoiceAudio: async () => {
      throw new Error("downloadMemoirVoiceAudio was not mocked");
    },
    generateMemoirBookChapterTitle: async () => {
      throw new Error("generateMemoirBookChapterTitle was not mocked");
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
    listMemoirAutomationEvents: async () => ({ items: [], total: 0, limit: 8 }),
    acknowledgeMemoirAutomationEvent: async () => ({}),
    bookVoiceCommandGatewayUrl: (projectId) => `wss://api.example.test/book/${encodeURIComponent(projectId)}/gateway`,
    bookVoiceAssistantTurnStreamUrl: (projectId) => (
      `wss://api.example.test/book/${encodeURIComponent(projectId)}/assistant-turn`
    ),
    updateMemoirBookPlan: async () => {
      throw new Error("updateMemoirBookPlan was not mocked");
    },
    ...voiceApiExports,
  });
  mockModule("../services/voice-assistant-turn-runtime.ts", {
    VoiceAssistantTurnRuntime: class {
      constructor(voiceSessionId, callbacks, options) {
        this.voiceSessionId = voiceSessionId;
        this.callbacks = callbacks;
        this.options = options;
      }

      async start() {
        throw new Error("VoiceAssistantTurnRuntime was not mocked");
      }

      close() {}

      interrupt() {}

      stats() {
        return null;
      }
    },
    ...voiceAssistantTurnRuntimeExports,
  });
  mockModule("../services/voice-assistant-turn-stream.ts", {
    VoiceAssistantTurnStreamClient: class {
      constructor(voiceSessionId, callbacks, options) {
        this.voiceSessionId = voiceSessionId;
        this.callbacks = callbacks;
        this.options = options;
      }
    },
    ...voiceAssistantTurnStreamExports,
  });
  mockModule("../services/memoir-voice-search.ts", {
    recordMemoirVoiceSearchQuery: async () => {
      throw new Error("recordMemoirVoiceSearchQuery was not mocked");
    },
    stopMemoirVoiceSearchRecording: () => {},
    ...voiceSearchExports,
  });
  mockModule("../services/memoir-streaming-asr.ts", {
    MemoirStreamingAsrClient: class {
      constructor(options) {
        this.options = options;
      }

      async connect() {
        this.options.callbacks.onStatus("ready");
      }

      startRecording() {
        this.options.callbacks.onStatus("listening");
        return true;
      }

      stopRecording() {
        this.options.callbacks.onStatus("stopping");
      }

      close() {}

      configureAutoStop() {}
    },
    classifyVoiceClientConnectionError: (message) => ({ kind: "connection", message }),
    ...streamingAsrExports,
  });
  clearModule("../services/streaming-voice-controller.ts");
  mockModule("../services/voice-authorization.ts", {
    readVoiceAuthorizationConsent: () => true,
    syncVoiceAuthorizationConsent: async () => {},
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
    ...voiceAuthorizationExports,
  });
  mockModule("../services/wechat-privacy.ts", {
    ensureRecorderPrivacyAuthorization: async () => {},
    ...wechatPrivacyExports,
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
    "本章素材：3 条原始讲述",
  );
});

test("book page exposes finished-book share entry points", () => {
  const pageWxml = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");
  const { page } = loadBookPage();
  const context = pageContext(page, {
    projectId: "project/1",
    projectTitle: "外婆回忆录",
    bookPreviewTitle: "外婆回忆录",
    bookContent: "外婆回忆录正文",
  });

  assert.match(pageWxml, /open-type="share"/);
  assert.match(pageWxml, /wx:if="{{bookContent}}"/);
  assert.match(pageWxml, /分享成书/);
  assert.deepEqual(page.onShareAppMessage.call(context), {
    title: "送你一本《外婆回忆录》",
    path: "/pages/book-reader/index?projectId=project%2F1&from=share_book",
  });
  assert.equal(page.onShareTimeline, undefined);
});

test("book page routes chapter card actions to the chapter detail page", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { mod, page } = loadBookPage();
  const context = pageContext(page, { projectId: "project/1" });

  assert.equal(
    mod.bookChapterDetailUrl("project/1", "chapter/1", "sources"),
    "/pages/chapter-workspace/index?projectId=project%2F1&chapterId=chapter%2F1&tab=stories",
  );

  page.openChapterEditor.call(context, { currentTarget: { dataset: { id: "chapter/1" } } });

  assert.deepEqual(navigations, [
    "/pages/chapter-workspace/index?projectId=project%2F1&chapterId=chapter%2F1&tab=drafts",
  ]);
  delete global.wx;
});

test("book page exposes chapter reordering after the plan is confirmed", () => {
  const pageWxml = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");
  global.wx = { switchTab() {} };
  const { page } = loadBookPage();
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));

  assert.equal(context.data.stage, "drafting");
  assert.equal(context.data.chapterStructureEditing, false);
  assert.match(pageWxml, /bindtap="toggleChapterStructureEditing"/);
  assert.match(pageWxml, /wx:if="{{chapterStructureEditing}}"/);

  page.toggleChapterStructureEditing.call(context);
  assert.equal(context.data.chapterStructureEditing, true);

  page.toggleChapterStructureEditing.call(context);
  assert.equal(context.data.chapterStructureEditing, false);
  delete global.wx;
});

test("book page opens secondary chapter actions from the more sheet", () => {
  const navigations = [];
  const actionSheets = [];
  const audioCalls = [];
  const tapIndexes = [0, 1, 2, 99];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
    showActionSheet(options) {
      actionSheets.push(options.itemList);
      const tapIndex = tapIndexes[actionSheets.length - 1];
      if (tapIndex < options.itemList.length) {
        options.success({ tapIndex });
      }
    },
    showToast() {},
  };
  const { mod, page } = loadBookPage();
  const chapters = mod.buildChapterViews(samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "draft"),
  });
  const context = pageContext(page, { projectId: "project/1", chapters });
  context.playChapterAudioById = (chapterId) => {
    audioCalls.push(chapterId);
  };

  page.openChapterMoreActions.call(context, { currentTarget: { dataset: { id: "chapter-1" } } });
  page.openChapterMoreActions.call(context, { currentTarget: { dataset: { id: "chapter-1" } } });
  page.openChapterMoreActions.call(context, { currentTarget: { dataset: { id: "chapter-1" } } });
  context.data.ttsBusyTarget = "chapter:chapter-2";
  page.openChapterMoreActions.call(context, { currentTarget: { dataset: { id: "chapter-1" } } });

  assert.deepEqual(actionSheets, [
    ["调整章节", "看素材", "朗读"],
    ["调整章节", "看素材", "朗读"],
    ["调整章节", "看素材", "朗读"],
    ["调整章节", "看素材"],
  ]);
  assert.deepEqual(navigations, [
    "/pages/chapter-workspace/index?projectId=project%2F1&chapterId=chapter-1&tab=structure",
    "/pages/chapter-workspace/index?projectId=project%2F1&chapterId=chapter-1&tab=stories",
  ]);
  assert.deepEqual(audioCalls, ["chapter-1"]);
  delete global.wx;
});

test("book page routes progress export actions to the full-book export page", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { mod, page } = loadBookPage();
  const context = pageContext(page, {
    projectId: "project/1",
    bookContent: "全书正文",
  });

  assert.equal(
    mod.bookExportPageUrl("project/1", "print_pdf"),
    "/pages/book-export/index?projectId=project%2F1&action=print_pdf",
  );

  page.openBookExportPage.call(context, { currentTarget: { dataset: { action: "preview" } } });
  page.openBookExportPage.call(context, { currentTarget: { dataset: { action: "reading_pdf" } } });

  assert.deepEqual(navigations, [
    "/pages/book-export/index?projectId=project%2F1&action=preview",
    "/pages/book-export/index?projectId=project%2F1&action=reading_pdf",
  ]);
  delete global.wx;
});

test("book page switches between chapter work and secondary tools", () => {
  const { page } = loadBookPage();
  const context = pageContext(page, { bookWorkspaceView: "chapters" });

  page.changeBookWorkspaceView.call(context, {
    currentTarget: { dataset: { view: "tools" } },
  });
  assert.equal(context.data.bookWorkspaceView, "tools");

  page.changeBookWorkspaceView.call(context, {
    currentTarget: { dataset: { view: "unsupported" } },
  });
  assert.equal(context.data.bookWorkspaceView, "tools");
});

test("book page keeps pending source details collapsed until requested", () => {
  const { page } = loadBookPage();
  const context = pageContext(page, { sourceCoverageExpanded: false });

  page.toggleSourceCoverageExpanded.call(context);
  assert.equal(context.data.sourceCoverageExpanded, true);

  page.toggleSourceCoverageExpanded.call(context);
  assert.equal(context.data.sourceCoverageExpanded, false);
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

test("book page stores only the full-book summary needed by the home view", () => {
  const { page } = loadBookPage();
  const output = sampleBookOutput(
    3,
    "全书正文",
    true,
    ["外婆家的童年", "第一次离家", "县城生活"],
    sampleStructuredBookContent(),
    [],
    sampleBookManuscript(),
  );
  const context = pageContext(page);
  page.applyBookOutput.call(context, output);
  const data = context.data;

  assert.equal(data.bookOutput, output);
  assert.equal(data.bookContent, "全书正文");
  assert.equal(data.bookCharCount, 4);
  assert.equal(data.bookPreviewTitle, "外婆回忆录");
  assert.equal(data.bookStaleNotice, "章节「外婆家的童年、第一次离家」等章节已更新，请重新整理一版。");
  assert.equal(data.bookActionText, "更新书稿");
  assert.equal("bookPreviewChapters" in data, false);
  assert.equal("bookTitleInput" in data, false);
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

test("book page shows and acknowledges automation events", async () => {
  const acknowledged = [];
  const { page } = loadBookPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        email: "memoir-author@example.com",
        password_login_enabled: true,
        display_name: "作者",
        created_at: "2026-06-15T00:00:00",
        active_project_id: "project/1",
        active_project: { id: "project/1", title: "外婆回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      getMemoirBookPlan: async () => samplePlan("confirmed"),
      getMemoirBookChapterDraft: async () => null,
      getMemoirBookOutput: async () => null,
      getMemoirBookSourceCoverage: async () => sampleSourceCoverage(0),
      listMemoirAutomationEvents: async () => ({
        items: [
          {
            id: "event-1",
            project_id: "project/1",
            event_type: "chapter_auto_revised",
            status: "completed",
            chapter_id: "chapter-1",
            utterance_ids: ["utterance-2"],
            summary_text: "「外婆家的童年」已自动补写 1 条新增讲述。",
            confidence: null,
            metadata_json: {},
            created_at: "2026-06-23T12:00:00",
            updated_at: "2026-06-23T12:00:00",
          },
        ],
        total: 1,
        limit: 8,
      }),
      acknowledgeMemoirAutomationEvent: async (projectId, eventId) => {
        acknowledged.push({ projectId, eventId });
        return {};
      },
    },
  });
  const context = pageContext(page);

  await page.loadBookHome.call(context);

  assert.equal(context.data.automationEventItems.length, 1);
  assert.equal(context.data.automationEventItems[0].titleText, "已自动补写");
  assert.equal(context.data.automationEventItems[0].summaryText, "「外婆家的童年」已自动补写 1 条新增讲述。");
  assert.equal(context.data.automationEventItems[0].metaText.includes("补写"), true);
  assert.equal(context.data.automationEventItems[0].actionText, "收起");
  assert.equal(context.data.automationEventItems[0].canAct, true);

  await page.acknowledgeAutomationEvent.call(context, { currentTarget: { dataset: { id: "event-1" } } });

  assert.deepEqual(acknowledged, [{ projectId: "project/1", eventId: "event-1" }]);
  assert.deepEqual(context.data.automationEventItems, []);
});

test("book page groups source assignment automation events with expandable details", async () => {
  const acknowledged = [];
  const { page } = loadBookPage({
    authExports: {
      getMemoirIdentity: async () => ({
        id: "user-1",
        email: "memoir-author@example.com",
        password_login_enabled: true,
        display_name: "作者",
        created_at: "2026-06-15T00:00:00",
        active_project_id: "project/1",
        active_project: { id: "project/1", title: "外婆回忆录" },
        projects: [],
      }),
    },
    voiceApiExports: {
      getMemoirBookPlan: async () => samplePlan("confirmed"),
      getMemoirBookChapterDraft: async () => null,
      getMemoirBookOutput: async () => null,
      getMemoirBookSourceCoverage: async () => sampleSourceCoverage(0),
      listMemoirAutomationEvents: async () => ({
        items: [
          {
            id: "event-1",
            project_id: "project/1",
            event_type: "source_assigned",
            status: "completed",
            chapter_id: "chapter-1",
            utterance_ids: ["utterance-1"],
            summary_text: "1 条讲述已自动整理进「外婆家的童年」。",
            confidence: 0.91,
            metadata_json: {},
            utterance_details: [
              {
                utterance_id: "utterance-1",
                source_index: 1,
                record_title: "语音记录 1",
                preview_text: "我小时候常在外婆家的院子里摘菜。",
                chapter_id: "chapter-1",
                chapter_title: "外婆家的童年",
                created_at: "2026-06-23T12:00:00",
              },
            ],
            created_at: "2026-06-23T12:00:00",
            updated_at: "2026-06-23T12:00:00",
          },
          {
            id: "event-2",
            project_id: "project/1",
            event_type: "source_assigned",
            status: "completed",
            chapter_id: "chapter-2",
            utterance_ids: ["utterance-2"],
            summary_text: "1 条讲述已自动整理进「第一次离家」。",
            confidence: 0.88,
            metadata_json: {},
            utterance_details: [
              {
                utterance_id: "utterance-2",
                source_index: 2,
                record_title: "语音记录 2",
                preview_text: "第一次离家去县城的时候，我心里很紧张。",
                chapter_id: "chapter-2",
                chapter_title: "第一次离家",
                created_at: "2026-06-23T12:03:00",
              },
            ],
            created_at: "2026-06-23T12:03:00",
            updated_at: "2026-06-23T12:03:00",
          },
        ],
        total: 2,
        limit: 8,
      }),
      acknowledgeMemoirAutomationEvent: async (projectId, eventId) => {
        acknowledged.push({ projectId, eventId });
        return {};
      },
    },
  });
  const context = pageContext(page);

  await page.loadBookHome.call(context);

  assert.equal(context.data.automationEventItems.length, 1);
  const item = context.data.automationEventItems[0];
  assert.equal(item.titleText, "已自动归章");
  assert.equal(item.summaryText, "2 条讲述已自动整理进 2 个章节：「外婆家的童年」、「第一次离家」。");
  assert.equal(item.actionKind, "acknowledge_group");
  assert.equal(item.actionText, "收起");
  assert.equal(item.hasDetails, true);
  assert.equal(item.detailExpanded, false);
  assert.equal(item.detailToggleText, "查看 2 条详情");
  assert.deepEqual(
    item.detailItems.map((detail) => [detail.titleText, detail.chapterText, detail.previewText]),
    [
      ["语音记录 1", "归入「外婆家的童年」", "我小时候常在外婆家的院子里摘菜。"],
      ["语音记录 2", "归入「第一次离家」", "第一次离家去县城的时候，我心里很紧张。"],
    ],
  );

  page.toggleAutomationEventDetails.call(context, { currentTarget: { dataset: { id: item.id } } });
  assert.equal(context.data.automationEventItems[0].detailExpanded, true);
  assert.equal(context.data.automationEventItems[0].detailToggleText, "收起详情");

  await page.acknowledgeAutomationEvent.call(context, {
    currentTarget: {
      dataset: {
        id: item.id,
        eventIds: item.eventIdsText,
        action: "acknowledge_group",
      },
    },
  });

  assert.deepEqual(acknowledged, [
    { projectId: "project/1", eventId: "event-1" },
    { projectId: "project/1", eventId: "event-2" },
  ]);
  assert.deepEqual(context.data.automationEventItems, []);
});

test("book page keeps editorial-copy polling active when automation refresh fails", async () => {
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      listMemoirAutomationEvents: async () => {
        throw new Error("network unavailable");
      },
    },
  });
  const plan = samplePlan("confirmed");
  const context = pageContext(page, {
    projectId: "project/1",
    chapters: mod.buildChapterViews(plan, { "chapter-1": sampleDraft(1, "draft", "第一版正文") }),
  });
  page.applyAutomationEvents.call(context, [
    sampleAutomationEvent({
      id: "event-editorial-pending",
      event_type: "book_editorial_copy_generated",
      chapter_id: null,
    }),
  ]);
  page.clearBookGenerationEventPoll.call(context);

  await page.refreshAutomationEventsAfterGeneration.call(context);

  assert.equal(context.data.automationEventError, "network unavailable");
  assert.equal(context.data.chapters[0].draftBusy, false);
  assert.equal(context.data.automationEventItems[0].titleText, "文案生成中");
  page.clearBookGenerationEventPoll.call(context);
});

test("book page builds detailed automation event views", () => {
  const { mod } = loadBookPage();

  const items = mod.buildAutomationEventViews([
    {
      id: "event-held",
      project_id: "project/1",
      event_type: "source_held",
      status: "needs_review",
      chapter_id: null,
      utterance_ids: ["utterance-1"],
      summary_text: "1 条讲述可能是新话题，需要确认。",
      confidence: 0.48,
      metadata_json: {
        route: {
          utterance_id: "utterance-1",
          action: "suggest_new_chapter",
          suggested_title: "外婆家的厨房",
          reason: "内容集中在厨房和做饭，和现有章节不完全一致",
        },
      },
      created_at: "2026-06-23T12:00:00",
      updated_at: "2026-06-23T12:00:00",
    },
    {
      id: "event-audit",
      project_id: "project/1",
      event_type: "audit_completed",
      status: "completed",
      chapter_id: null,
      utterance_ids: [],
      summary_text: "当前有 1 个章节需要优先处理。",
      confidence: null,
      metadata_json: {
        audit_snapshot: {
          id: "snapshot-1",
          engine: "llm_semantic_v1",
          overall_status: "needs_updates",
          display: {
            title: "「院子里的童年」有新内容待写入",
            summary: "有 1 条新讲述还没有补进正文。",
            detail: "建议先补写本章，再继续补充人物、地点和细节。",
          },
          primary_action: {
            type: "open_chapter_workspace",
            cta_text: "补写本章",
            chapter_id: "chapter-1",
            utterance_count: 1,
          },
          chapter_cards: [
            {
              id: "chapter-card-1",
              chapter_id: "chapter-1",
              chapter_title: "院子里的童年",
              summary: "本章围绕院子里的童年生活。",
              gaps: [
                { type: "event_sequence", severity: "medium", message: "剥青豆的前后经过还不连贯" },
              ],
              next_questions: ["外婆在院子里最常做什么？"],
            },
          ],
          chapter_audits: [
            {
              chapter_id: "chapter-1",
              chapter_title: "院子里的童年",
              summary: "新增剥青豆素材待写入。",
              gaps: [
                { type: "event_sequence", severity: "medium", message: "剥青豆事件缺少起因和经过" },
              ],
              next_questions: ["那天为什么会一起剥青豆？"],
            },
          ],
        },
        display: {
          title: "旧展示标题",
          summary: "旧展示摘要。",
          detail: "旧展示详情。",
        },
        primary_action: {
          type: "open_chapter_workspace",
          cta_text: "旧操作",
          chapter_id: "chapter-1",
          utterance_count: 1,
        },
        primary_chapter: {
          chapter_id: "chapter-1",
          title: "院子里的童年",
        },
        pending_utterance_count: 2,
        next_actions: ["整理 2 条尚未归章的讲述记录", "可以追问外婆在厨房里的细节"],
      },
      created_at: "2026-06-23T12:00:00",
      updated_at: "2026-06-23T12:00:00",
    },
    {
      id: "event-audit-legacy",
      project_id: "project/1",
      event_type: "audit_completed",
      status: "completed",
      chapter_id: null,
      utterance_ids: [],
      summary_text: "当前有 1 个章节需要优先处理。",
      confidence: null,
      metadata_json: {
        next_actions: ["整理 2 条尚未归章的讲述记录", "可以追问外婆在厨房里的细节"],
      },
      created_at: "2026-06-23T12:00:00",
      updated_at: "2026-06-23T12:00:00",
    },
  ]);

  assert.equal(items[0].titleText, "待整理讲述");
  assert.equal(items[0].detailText, "建议新建「外婆家的厨房」：内容集中在厨房和做饭，和现有章节不完全一致");
  assert.equal(items[0].canAcknowledge, false);
  assert.equal(items[0].canAct, true);
  assert.equal(items[0].actionText, "去整理");
  assert.equal(items[0].actionKind, "review_source");
  assert.equal(items[0].primaryUtteranceId, "utterance-1");
  assert.equal(items[1].titleText, "「院子里的童年」有新内容待写入");
  assert.equal(items[1].summaryText, "有 1 条新讲述还没有补进正文。");
  assert.equal(items[1].detailText, "建议先补写本章，再继续补充人物、地点和细节。");
  assert.equal(items[1].suggestedActionText, "补写本章");
  assert.equal(items[1].hasSuggestions, true);
  assert.equal(items[1].suggestionItems[0].labelText, "建议整理");
  assert.equal(items[1].suggestionItems[0].bodyText, "补写本章");
  assert.equal(items[1].suggestionItems[1].labelText, "待整理素材");
  assert.equal(items[1].suggestionItems[1].bodyText, "还有 2 条讲述需要归章或确认。");
  assert.equal(items[1].suggestionItems[2].labelText, "建议补问");
  assert.equal(items[1].suggestionItems[2].bodyText, "外婆在院子里最常做什么？");
  assert.equal(items[1].suggestionItems[2].chapterText, "《院子里的童年》");
  assert.equal(items[1].actionText, "收起");
  assert.equal(items[2].titleText, "成书整理建议");
  assert.equal(items[2].summaryText, "当前有 1 个章节需要优先处理。");
  assert.equal(items[2].detailText, "整理 2 条尚未归章的讲述记录；可以追问外婆在厨房里的细节");
  assert.equal(items[2].suggestedActionText, "");
  assert.equal(items[2].hasSuggestions, false);
});

test("book page falls back to audit snapshot questions when chapter card has none", () => {
  const { mod } = loadBookPage();

  const items = mod.buildAutomationEventViews([
    {
      id: "event-audit",
      project_id: "project/1",
      event_type: "audit_completed",
      status: "completed",
      chapter_id: null,
      utterance_ids: [],
      summary_text: "当前有 1 个章节需要继续补问。",
      confidence: null,
      metadata_json: {
        audit_snapshot: {
          id: "snapshot-1",
          display: {
            title: "院子里的童年可以继续补问",
            summary: "章节已有素材，但还缺少细节。",
            detail: "顺着院子和外婆继续追问。",
          },
          primary_action: {
            type: "continue_interview",
            cta_text: "继续补问",
            chapter_id: "chapter-1",
          },
          chapter_cards: [
            {
              id: "chapter-card-1",
              chapter_id: "chapter-1",
              chapter_title: "院子里的童年",
              next_questions: [],
            },
          ],
          chapter_audits: [
            {
              chapter_id: "chapter-1",
              chapter_title: "院子里的童年",
              next_questions: ["那时外婆在院子里最常做什么？"],
            },
          ],
        },
      },
      created_at: "2026-06-23T12:00:00",
      updated_at: "2026-06-23T12:00:00",
    },
  ]);

  assert.equal(items[0].hasSuggestions, true);
  assert.equal(items[0].suggestionItems[1].labelText, "建议补问");
  assert.equal(items[0].suggestionItems[1].bodyText, "那时外婆在院子里最常做什么？");
});

test("book page opens transcripts for pending source automation events", async () => {
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

  await page.acknowledgeAutomationEvent.call(context, {
    currentTarget: {
      dataset: {
        id: "event-held",
        action: "review_source",
        utteranceId: "utterance-new-1",
      },
    },
  });

  assert.equal(storageWrites[0].key, "pawtale_transcript_navigation_target");
  assert.equal(storageWrites[0].value.utterance_id, "utterance-new-1");
  assert.deepEqual(switches, ["/pages/transcripts/index"]);
  delete global.wx;
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

test("book page marks assigned sources as supplemental chapter work", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  plan.chapters[0].pending_source_count = 1;

  const chapters = mod.buildChapterViews(plan, {
    "chapter-1": sampleDraft(1, "draft", "第一版正文"),
  });

  assert.equal(chapters[0].pendingSourceCount, 1);
  assert.equal(chapters[0].sourceChangeNotice, "1 条新增讲述待补写");
});

test("book page marks deleted chapter sources for review", () => {
  const { mod } = loadBookPage();
  const plan = samplePlan("confirmed");
  plan.chapters[0].source_refs = [];
  plan.chapters[0].removed_source_count = 1;

  const chapters = mod.buildChapterViews(plan, {
    "chapter-1": sampleDraft(1, "draft", "第一版正文"),
  });

  assert.equal(chapters[0].sourceCount, 0);
  assert.equal(chapters[0].removedSourceCount, 1);
  assert.equal(chapters[0].sourceChangeNotice, "1 条原始讲述已删除，章节稿需要更新");
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

test("book page hides source assignment options until the plan is confirmed", () => {
  const { page } = loadBookPage();
  const context = pageContext(page, {
    projectId: "project-1",
    projectTitle: "外婆回忆录",
  });

  page.applyPlan.call(context, samplePlan("draft"));
  assert.deepEqual(context.data.chapterPickerOptions, []);

  page.applyPlan.call(context, samplePlan("confirmed"));
  assert.deepEqual(context.data.chapterPickerOptions.map((option) => option.label), [
    "第 1 章 外婆家的童年",
    "第 2 章 第一次离家",
    "作为新章",
  ]);
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
      applyMemoirBookPlanStructureCommand: async (projectId, data) => {
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
  assert.deepEqual(calls[0].data, {
    type: "create_chapter_from_source",
    utterance_id: "utterance-new-1",
  });
  assert.equal(context.data.assigningSourceId, "");
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

test("book page reorders chapters through structure command", async () => {
  const calls = [];
  global.wx = { showToast() {}, switchTab() {} };
  const { page } = loadBookPage({
    voiceApiExports: {
      applyMemoirBookPlanStructureCommand: async (projectId, data) => {
        calls.push({ projectId, data });
        return planFromStructureCommand("confirmed", data);
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
  assert.deepEqual(calls[0].data, {
    type: "move_chapter",
    chapter_id: "chapter-1",
    direction: "down",
  });
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
      applyMemoirBookPlanStructureCommand: async (projectId, data) => {
        calls.push({ projectId, data });
        return planFromStructureCommand("confirmed", data);
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
  assert.deepEqual(calls[0].data, {
    type: "split_chapter",
    chapter_id: "chapter-1",
    title: "外婆家的童年（拆出）",
    utterance_ids: ["utterance-3"],
  });
  assert.equal(context.data.splitSourceSelections["chapter-1"], undefined);
  delete global.wx;
});

test("book page merges chapters and directs the changed chapter to its workspace", async () => {
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
      applyMemoirBookPlanStructureCommand: async (projectId, data) => {
        calls.push({ kind: "update", projectId, data });
        return planFromStructureCommand("confirmed", data);
      },
      generateMemoirBookChapterTitle: async (projectId, chapterId, data) => {
        calls.push({ kind: "title", projectId, chapterId, data });
        const updated = planFromStructureCommand("confirmed", calls[0].data);
        updated.chapters[0].title = "童年里的离别";
        return updated;
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

  assert.deepEqual(calls.map((call) => call.kind), ["update", "title"]);
  assert.deepEqual(calls[0].data, {
    type: "merge_chapters",
    chapter_id: "chapter-2",
    target_chapter_id: "chapter-1",
  });
  assert.equal(context.data.chapters[0].title, "童年里的离别");
  assert.equal(
    context.data.chapters[0].draftError,
    "章节已合并，请进入章节工作台检查结构并重新整理正文",
  );
  delete global.wx;
});

test("book page assembles the full book from confirmed chapter blocks", async () => {
  const calls = [];
  global.wx = { showToast() {} };
  const { page } = loadBookPage({
    voiceApiExports: {
      assembleMemoirBookDraftBlocks: async (projectId) => {
        calls.push({ projectId });
        return { version: 2 };
      },
      getMemoirBookOutput: async () => sampleBookOutput(2, "全书第二版正文"),
    },
  });
  const plan = samplePlan("confirmed");
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, plan, {
    "chapter-1": sampleDraft(1, "confirmed"),
    "chapter-2": sampleDraft(1, "confirmed"),
  });
  page.applyBookOutput.call(
    context,
    sampleBookOutput(1, "全书第一版正文", false, [], null, [], sampleBookManuscript("外婆回忆录", "moonlit_courtyard")),
  );

  await page.generateBook.call(context);

  assert.deepEqual(calls[0], { projectId: "project/1" });
  assert.equal(context.data.generatingBook, false);
  assert.equal(context.data.bookContent, "全书第二版正文");
  delete global.wx;
});

test("book page shows errors when structured full-book assembly fails", async () => {
  global.wx = { showToast() {} };
  const { page } = loadBookPage({
    voiceApiExports: {
      assembleMemoirBookDraftBlocks: async () => {
        throw new Error("request:fail network");
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "confirmed"),
    "chapter-2": sampleDraft(1, "confirmed"),
  });

  await page.generateBook.call(context);

  assert.equal(context.data.error, "request:fail network");
  assert.equal(context.data.generatingBook, false);
  delete global.wx;
});

test("book page sends TTS voice and speed for chapter and outline audio", async () => {
  const calls = [];
  const { page } = loadBookPage({
    voiceApiExports: {
      createMemoirBookChapterDraftTTS: async (projectId, chapterId, data) => {
        calls.push({ kind: "chapter", projectId, chapterId, data });
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

  await page.requestBookAudio.call(context, "outline");
  await page.requestBookAudio.call(context, "chapter:chapter-1");

  assert.deepEqual(calls, [
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

test("book page shows chapter narration toast after preflight passes", async () => {
  const toasts = [];
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      createMemoirBookChapterDraftTTS: async () => sampleAudioResponse(),
    },
  });
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const chapters = mod.buildChapterViews(samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "draft"),
  });
  const context = pageContext(page, { projectId: "project/1", chapters });
  context.playAudioFiles = async () => {};

  await page.playChapterAudioById.call(context, "chapter-1", { showPreparingToast: true });

  assert.deepEqual(toasts, ["正在准备朗读"]);
  delete global.wx;
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

test("book page records and previews a voice edit command through book turn stream", async () => {
  const calls = [];
  global.wx = { showToast(options) { calls.push({ kind: "toast", title: options.title }); } };
  const { page } = loadBookPage({
    voiceAssistantTurnRuntimeExports: {
      VoiceAssistantTurnRuntime: bookVoiceTurnRuntimeMock(calls),
    },
    streamingAsrExports: {
      MemoirStreamingAsrClient: streamingAsrClientForFinal(calls, "把第一张改名为童年往事"),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applySourceCoverage.call(context, sampleSourceCoverage(1));
  context.scheduleBookVoiceAssistantAutoListen = (statusText) => {
    calls.push({ kind: "schedule", statusText });
    context.setData({ bookVoiceAssistantStatusText: statusText });
  };

  await page.startBookVoiceCommand.call(context);
  await flushAsyncWork();

  const turnStart = calls.find((call) => call.kind === "turn-start");
  assert.equal(turnStart.options.extraPayload.text, "把第一章改名为童年往事");
  assert.deepEqual(turnStart.options.extraPayload.book_context, {
    source_coverage: {
      total_source_count: 3,
      covered_source_count: 2,
      uncovered_source_count: 1,
    },
    uncovered_sources: [
      {
        source_index: 3,
        utterance_id: "utterance-new-1",
        text: "新增转写",
      },
    ],
  });
  assert.equal(calls.find((call) => call.kind === "turn-runtime").streamUrl, "wss://api.example.test/book/project%2F1/assistant-turn");
  assert.equal(context.data.bookVoiceCommandText, "把第一章改名为童年往事");
  assert.equal(context.data.bookVoiceCommandIntentLabel, "修改章名");
  assert.equal(context.data.bookVoiceCommandStateText, "待确认");
  assert.equal(context.data.bookVoiceCommandDetailText, "准备把「外婆家的童年」改名为「童年往事」。");
  assert.equal(context.data.bookVoiceCommandRequiresConfirmation, true);
  assert.equal(context.data.bookVoiceAssistantBusy, false);
  assert.equal(context.data.bookVoiceTranscriptLines[0].role, "user");
  assert.equal(context.data.bookVoiceTranscriptLines[0].text, "把第一章改名为童年往事");
  assert.equal(context.data.bookVoiceTranscriptLines[1].role, "assistant");
  assert.match(context.data.bookVoiceTranscriptLines[1].text, /准备把/);
  assert.deepEqual(calls.find((call) => call.kind === "toast"), { kind: "toast", title: "已识别" });
  assert.deepEqual(calls.find((call) => call.kind === "schedule"), {
    kind: "schedule",
    statusText: "等你确认，或继续说",
  });
  delete global.wx;
});

test("book page sends recent voice editing context in book turn payload", async () => {
  const calls = [];
  const answerPreview = sampleBookVoiceCommandPreview({
    original_text: "现在有几个章节啊",
    intent: "answer_book_question",
    intent_label: "成书问答",
    response_kind: "answer",
    summary: "回答当前成书章节状态。",
    reply_text: "现在目录有 2 章：第一章「外婆家的童年」，第二章「第一次离家」。",
    operations: [],
    requires_confirmation: false,
  });
  global.wx = { showToast(options) { calls.push({ kind: "toast", title: options.title }); } };
  const { page } = loadBookPage({
    voiceAssistantTurnRuntimeExports: {
      VoiceAssistantTurnRuntime: bookVoiceTurnRuntimeMock(calls, {
        preview: answerPreview,
        text: answerPreview.reply_text,
      }),
    },
    streamingAsrExports: {
      MemoirStreamingAsrClient: streamingAsrClientForFinal(calls, "现在有几个章节啊"),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  context.setData({
    bookVoiceTranscriptLines: [
      { id: "book-user-1", role: "user", text: "我可以修改些啥" },
      { id: "book-ai-1", role: "assistant", text: "可以改章名、调顺序、合并章节。" },
    ],
  });
  context.scheduleBookVoiceAssistantAutoListen = (statusText) => {
    calls.push({ kind: "schedule", statusText });
  };

  await page.startBookVoiceCommand.call(context);
  await flushAsyncWork();

  const turnStart = calls.find((call) => call.kind === "turn-start");
  assert.deepEqual(turnStart.options.extraPayload.conversation_context, [
    { role: "user", text: "我可以修改些啥" },
    { role: "assistant", text: "可以改章名、调顺序、合并章节。" },
  ]);
  assert.equal(context.data.bookVoiceCommandResponseKind, "answer");
  assert.equal(context.data.bookVoiceCommandPanelTitle, "回答");
  assert.equal(context.data.bookVoiceCommandStateText, "已回答");
  assert.equal(
    context.data.bookVoiceCommandDetailText,
    "现在目录有 2 章：第一章「外婆家的童年」，第二章「第一次离家」。",
  );
  assert.deepEqual(calls.find((call) => call.kind === "schedule"), {
    kind: "schedule",
    statusText: "我继续听",
  });
  delete global.wx;
});

test("book page treats voice editing advice as a spoken consultation from book turn", async () => {
  const calls = [];
  const consultationPreview = sampleBookVoiceCommandPreview({
    original_text: "第一章是不是太短了",
    intent: "review_chapter_length",
    intent_label: "篇幅建议",
    response_kind: "consultation",
    summary: "正在判断「外婆家的童年」的篇幅是否合适。",
    reply_text: "这一章现在明显偏短，建议补一个具体场景或人物对话。",
    operations: [],
    requires_confirmation: false,
  });
  global.wx = { showToast(options) { calls.push({ kind: "toast", title: options.title }); } };
  const { page } = loadBookPage({
    voiceAssistantTurnRuntimeExports: {
      VoiceAssistantTurnRuntime: bookVoiceTurnRuntimeMock(calls, {
        preview: consultationPreview,
        text: consultationPreview.reply_text,
      }),
    },
    streamingAsrExports: {
      MemoirStreamingAsrClient: streamingAsrClientForFinal(calls, "第一章是不是太短了"),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  context.scheduleBookVoiceAssistantAutoListen = (statusText) => {
    calls.push({ kind: "schedule", statusText });
  };

  await page.startBookVoiceCommand.call(context);
  await flushAsyncWork();

  assert.equal(context.data.bookVoiceCommandText, "第一章是不是太短了");
  assert.equal(context.data.bookVoiceCommandIntentLabel, "篇幅建议");
  assert.equal(context.data.bookVoiceCommandResponseKind, "consultation");
  assert.equal(context.data.bookVoiceCommandPanelTitle, "建议");
  assert.equal(context.data.bookVoiceCommandStateText, "建议");
  assert.equal(context.data.bookVoiceCommandDetailText, "这一章现在明显偏短，建议补一个具体场景或人物对话。");
  assert.equal(context.data.bookVoiceCommandRequiresConfirmation, false);
  assert.equal(context.data.bookVoiceCommandCanExecute, false);
  assert.equal(context.data.bookVoiceTranscriptLines[0].text, "第一章是不是太短了");
  assert.equal(context.data.bookVoiceTranscriptLines[1].text, "这一章现在明显偏短，建议补一个具体场景或人物对话。");
  assert.deepEqual(calls.find((call) => call.kind === "schedule"), {
    kind: "schedule",
    statusText: "我继续听",
  });
  delete global.wx;
});

test("book page drives voice assistant reply highlight from runtime playback progress", () => {
  let currentTimeSeconds = 10.5;
  const { page } = loadBookPage();
  const context = pageContext(page, { projectId: "project/1" });
  context.playToken = 7;
  context.bookVoiceAssistantTurnRuntime = {
    stats() {
      return {
        currentTimeSeconds,
        segmentCount: 1,
        underrunCount: 0,
        nextPlayTimeSeconds: 12,
        queuedAheadSeconds: 1.5,
      };
    },
  };
  context.setData({
    bookTTSActiveTarget: "book-voice-assistant",
    bookTTSActiveText: "外婆回忆录",
  });

  try {
    page.markBookVoiceStreamingAudioFrameScheduled.call(context, {
      frameIndex: 0,
      startAtSeconds: 10,
      durationSeconds: 2,
      ended: Promise.resolve(),
    });
    page.updateBookVoiceStreamingTTSProgress.call(context);

    assert.equal(context.data.bookTTSSpokenText, "外");
    assert.equal(context.data.bookTTSCurrentText, "婆");
    assert.equal(context.data.bookTTSRemainingText, "回忆录");
    assert.equal(context.data.bookTTSProgressPercent, 25);

    currentTimeSeconds = 11;
    page.updateBookVoiceStreamingTTSProgress.call(context);
    assert.notEqual(context.data.bookTTSCurrentText, "外婆回忆录");
    assert.ok(context.data.bookTTSProgressPercent > 25);
  } finally {
    page.stopBookTTSProgressTimer.call(context);
  }
});

test("book page interrupts active assistant reply and starts voice editing capture", async () => {
  const calls = [];
  const { page } = loadBookPage({
    streamingAsrExports: {
      MemoirStreamingAsrClient: class {
        constructor(options) {
          this.options = options;
          calls.push({ kind: "client", gatewayUrl: options.gatewayUrl });
        }

        async connect() {
          calls.push({ kind: "connect" });
          this.options.callbacks.onStatus("ready");
        }

        startRecording(options) {
          calls.push({ kind: "record", options });
          this.options.callbacks.onStatus("listening");
          return true;
        }

        stopRecording() {}
        close() {}
        configureAutoStop() {}
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  context.bookVoiceAssistantTurnRuntime = {
    interrupt() {
      calls.push({ kind: "turn-interrupt" });
    },
    close() {},
  };
  context.setData({
    bookVoiceAssistantExpanded: true,
    bookVoiceAssistantConversationActive: true,
    bookVoiceAssistantBusy: true,
    bookVoiceAssistantSpeakingLineId: "assistant-1",
    bookTTSActiveTarget: "book-voice-assistant",
    bookVoiceTranscriptLines: [
      { id: "user-1", role: "user", text: "第一章是不是太短了" },
      { id: "assistant-1", role: "assistant", text: "这一章还可以补一个具体场景。" },
    ],
  });

  await page.startBookVoiceCommand.call(context);
  await flushAsyncWork();

  assert.deepEqual(
    context.data.bookVoiceTranscriptLines.map((line) => line.id),
    ["user-1"],
  );
  assert.equal(context.data.bookVoiceAssistantListening, true);
  assert.equal(context.data.bookVoiceAssistantBusy, false);
  assert.equal(context.data.bookVoiceAssistantSpeakingLineId, "");
  assert.equal(context.data.bookVoiceAssistantStatusText, "我在听，请说想怎么改");
  assert.deepEqual(calls.map((call) => call.kind), ["turn-interrupt", "client", "connect", "record"]);
});

test("book page keeps pending preview during auto-listen for voice confirmation", async () => {
  const calls = [];
  global.wx = { showToast(options) { calls.push({ kind: "toast", title: options.title }); } };
  const plan = samplePlan("confirmed");
  const executedPlan = planFromStructureCommand("confirmed", {
    type: "rename_chapter",
    chapter_id: "chapter-1",
    title: "童年往事",
  });
  const preview = sampleBookVoiceCommandPreview();
  const { page } = loadBookPage({
    voiceAssistantTurnRuntimeExports: {
      VoiceAssistantTurnRuntime: bookVoiceTurnRuntimeMock(calls, {
        preview,
        text: "好的，我按刚才理解的修改。",
        executionResult: sampleBookVoiceCommandExecuteResponse("rename_chapter", executedPlan),
      }),
    },
    streamingAsrExports: {
      MemoirStreamingAsrClient: streamingAsrClientForFinal(calls, "确认执行。"),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, plan);
  page.applyBookVoiceCommandPreview.call(context, preview);
  context.scheduleBookVoiceAssistantAutoListen = (statusText) => {
    calls.push({ kind: "schedule", statusText });
  };

  await page.startBookVoiceCommand.call(context, { autoTriggered: true });
  await flushAsyncWork();

  const turnStart = calls.find((call) => call.kind === "turn-start");
  assert.equal(turnStart.options.extraPayload.text, "确认执行。");
  assert.equal(turnStart.options.extraPayload.pending_command.intent, "rename_chapter");
  assert.equal(context.data.bookVoiceCommandPreview.intent, "rename_chapter");
  assert.equal(context.data.bookVoiceCommandText, "把第一章改名为童年往事");
  assert.equal(context.data.chapters[0].title, "童年往事");
  assert.equal(context.data.bookVoiceCommandStateText, "已执行");
  assert.equal(context.data.bookVoiceCommandUndoText, "撤回语音改名");
  assert.equal(context.data.bookVoiceCommandUndoSnapshot.chapters[0].title, "外婆家的童年");
  assert.deepEqual(calls.find((call) => call.kind === "schedule"), {
    kind: "schedule",
    statusText: "已执行，可撤回",
  });
  delete global.wx;
});

test("book page applies voice confirmation result without auto-listen when playback fails", async () => {
  const calls = [];
  global.wx = { showToast(options) { calls.push({ kind: "toast", title: options.title }); } };
  const executedPlan = planFromStructureCommand("confirmed", {
    type: "rename_chapter",
    chapter_id: "chapter-1",
    title: "童年往事",
  });
  const preview = sampleBookVoiceCommandPreview();
  const { page } = loadBookPage({
    voiceAssistantTurnRuntimeExports: {
      VoiceAssistantTurnRuntime: bookVoiceTurnRuntimeMock(calls, {
        preview,
        text: "好的，我按刚才理解的修改。",
        executionResult: sampleBookVoiceCommandExecuteResponse("rename_chapter", executedPlan),
        playbackError: new Error("扬声器不可用"),
      }),
    },
    streamingAsrExports: {
      MemoirStreamingAsrClient: streamingAsrClientForFinal(calls, "确认执行。"),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applyBookVoiceCommandPreview.call(context, preview);
  context.scheduleBookVoiceAssistantAutoListen = (statusText) => {
    calls.push({ kind: "schedule", statusText });
  };

  await page.startBookVoiceCommand.call(context, { autoTriggered: true });
  await flushAsyncWork(20);

  assert.equal(context.data.chapters[0].title, "童年往事");
  assert.equal(context.data.error, "扬声器不可用");
  assert.equal(context.data.bookVoiceCommandStateText, "已执行");
  assert.equal(calls.some((call) => call.kind === "schedule"), false);
  delete global.wx;
});

test("book page pauses voice conversation when no speech is detected", async () => {
  const calls = [];
  const { page } = loadBookPage({
    streamingAsrExports: {
      MemoirStreamingAsrClient: streamingAsrClientForNoVoice(calls),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));

  await page.startBookVoiceCommand.call(context);

  assert.equal(context.data.bookVoiceAssistantConversationActive, false);
  assert.equal(context.data.bookVoiceAssistantStatusText, "语音对话已暂停");
  assert.equal(context.data.error, "");
  assert.equal(context.data.bookVoiceAssistantListening, false);
});

test("book page executes a confirmed voice rename with undo snapshot", async () => {
  installConfirmingWx();
  const plan = samplePlan("confirmed");
  const executedPlan = planFromStructureCommand("confirmed", {
    type: "rename_chapter",
    chapter_id: "chapter-1",
    title: "童年往事",
  });
  const { page, context, calls } = bookVoiceCommandExecutionHarness((_, data) => (
    sampleBookVoiceCommandExecuteResponse(data.intent, executedPlan)
  ));
  page.applyPlan.call(context, plan);
  page.applyBookVoiceCommandPreview.call(context, sampleBookVoiceCommandPreview());

  await page.executeBookVoiceCommand.call(context);

  assert.equal(calls[0].projectId, "project/1");
  assert.deepEqual(calls[0].data, {
    intent: "rename_chapter",
    operation: {
      type: "rename_chapter",
      chapter_id: "chapter-1",
      chapter_title: "外婆家的童年",
      new_title: "童年往事",
    },
  });
  assert.equal(context.data.bookVoiceCommandCanExecute, false);
  assert.equal(context.data.bookVoiceCommandRequiresConfirmation, false);
  assert.equal(context.data.bookVoiceCommandStateText, "已执行");
  assert.equal(context.data.bookVoiceCommandUndoText, "撤回语音改名");
  assert.equal(context.data.bookVoiceCommandUndoSnapshot.chapters[0].title, "外婆家的童年");
  assert.equal(context.data.bookVoiceAssistantStatusText, "已执行，可撤回");
  delete global.wx;
});

test("book page executes and undoes a voice chapter move", async () => {
  installConfirmingWx();
  const movedPlan = planFromStructureCommand("confirmed", {
    type: "move_chapter",
    chapter_id: "chapter-2",
    direction: "up",
  });
  const { page, context, calls } = bookVoiceCommandExecutionHarness((_, data) => (
    sampleBookVoiceCommandExecuteResponse(data.intent, movedPlan)
  ));
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

  assert.equal(calls[0].projectId, "project/1");
  assert.deepEqual(calls[0].data, {
    intent: "move_chapter",
    operation: {
      type: "move_chapter",
      chapter_id: "chapter-2",
      chapter_title: "第一次离家",
      direction: "up",
    },
  });
  assert.equal(calls[1].toastTitle, "已撤回语音修改");
  assert.deepEqual(calls[1].chapters.map((chapter) => chapter.id), ["chapter-1", "chapter-2"]);
  assert.equal(context.data.bookVoiceCommandUndoText, "");
  assert.equal(context.data.bookVoiceCommandUndoSnapshot, null);
  assert.equal(context.data.bookVoiceAssistantStatusText, "已撤回");
  delete global.wx;
});

test("book page executes a voice chapter move to an explicit position", async () => {
  installConfirmingWx();
  const movedPlan = planFromStructureCommand("confirmed", {
    type: "move_chapter",
    chapter_id: "chapter-1",
    target_position: "last",
  });
  const { page, context, calls } = bookVoiceCommandExecutionHarness((_, data) => (
    sampleBookVoiceCommandExecuteResponse(data.intent, movedPlan)
  ));
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applyBookVoiceCommandPreview.call(context, sampleBookVoiceCommandPreview({
    original_text: "把外婆家的童年移到最后一章",
    intent: "move_chapter",
    intent_label: "移动章节",
    summary: "准备把「外婆家的童年」移到最后一章。",
    operations: [
      {
        type: "move_chapter",
        chapter_id: "chapter-1",
        chapter_title: "外婆家的童年",
        target_position: "last",
      },
    ],
  }));

  await page.executeBookVoiceCommand.call(context);

  assert.deepEqual(calls[0], {
    projectId: "project/1",
    data: {
      intent: "move_chapter",
      operation: {
        type: "move_chapter",
        chapter_id: "chapter-1",
        chapter_title: "外婆家的童年",
        target_position: "last",
      },
    },
  });
  assert.deepEqual(context.data.chapters.map((chapter) => chapter.id), ["chapter-2", "chapter-1"]);
  assert.equal(context.data.bookVoiceCommandUndoText, "撤回语音排序");
  delete global.wx;
});

test("book page executes a voice assignment for an uncovered source", async () => {
  installConfirmingWx({ showToast() {} });
  const assignedPlan = samplePlan("confirmed");
  assignedPlan.chapters[1].source_refs = [sampleRef("utterance-new-1", "新增转写")];
  const { page, context, calls } = bookVoiceCommandExecutionHarness(
    (_, data) => sampleBookVoiceCommandExecuteResponse(data.intent, assignedPlan, sampleSourceCoverage(0)),
    { getMemoirBookChapterDraft: async () => sampleDraft(1, "draft") },
  );
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applySourceCoverage.call(context, sampleSourceCoverage(1));
  page.applyBookOutput.call(context, sampleBookOutput(1, "旧全书"));
  page.applyBookVoiceCommandPreview.call(context, sampleBookVoiceCommandPreview({
    original_text: "把刚才这条归到第二章",
    intent: "assign_source",
    intent_label: "归入记录",
    summary: "准备把待收录第 1 条归入「第一次离家」。",
    operations: [
      {
        type: "assign_source",
        chapter_id: "chapter-2",
        chapter_title: "第一次离家",
        source_position: 1,
        source_index: 3,
        utterance_id: "utterance-new-1",
      },
    ],
  }));

  await page.executeBookVoiceCommand.call(context);

  assert.deepEqual(calls[0], {
    projectId: "project/1",
    data: {
      intent: "assign_source",
      operation: {
        type: "assign_source",
        chapter_id: "chapter-2",
        chapter_title: "第一次离家",
        source_position: 1,
        source_index: 3,
        utterance_id: "utterance-new-1",
      },
    },
  });
  assert.equal(context.data.bookVoiceCommandCanExecute, false);
  assert.equal(context.data.bookVoiceCommandStateText, "已执行");
  assert.equal(context.data.bookVoiceAssistantStatusText, "已归入章节");
  assert.equal(context.data.bookOutput, null);
  assert.equal(context.data.assigningSourceId, "");
  delete global.wx;
});

test("book page shows backend voice execution errors", async () => {
  installConfirmingWx();
  const { page, context } = bookVoiceCommandExecutionHarness(() => {
    throw new Error("章节已经不存在");
  });
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applyBookVoiceCommandPreview.call(context, sampleBookVoiceCommandPreview());

  await page.executeBookVoiceCommand.call(context);

  assert.equal(context.data.error, "章节已经不存在");
  assert.equal(context.data.bookVoiceAssistantStatusText, "执行失败");
  assert.equal(context.data.bookVoiceCommandExecuting, false);
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
  assert.equal(context.data.bookVoiceCommandResponseKind, "action_preview");
  assert.equal(context.data.bookVoiceCommandStateText, "仅预览");
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
    intent: "move_chapter",
    operations: [{ type: "move_chapter", chapter_id: "chapter-1", target_position: "last" }],
  })), true);
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
    streamingAsrExports: {
      MemoirStreamingAsrClient: class {
        constructor(options) {
          this.options = options;
        }

        async connect() {
          this.options.callbacks.onStatus("ready");
        }

        startRecording() {
          this.options.callbacks.onStatus("listening");
          return true;
        }

        stopRecording() {
          stops.push("stop");
        }

        close() {}

        configureAutoStop() {}
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));

  await page.startBookVoiceCommand.call(context);

  await page.startBookVoiceCommand.call(context);

  assert.deepEqual(stops, ["stop"]);
  assert.equal(context.data.bookVoiceAssistantStatusText, "正在保存录音");
});

test("book page releases transient voice gateway when hidden", async () => {
  const closes = [];
  const { page } = loadBookPage({
    streamingAsrExports: {
      MemoirStreamingAsrClient: class {
        constructor(options) {
          this.options = options;
        }

        async connect() {
          this.options.callbacks.onStatus("ready");
        }

        startRecording() {
          this.options.callbacks.onStatus("listening");
          this.options.callbacks.onPartial("正在说修改要求");
          return true;
        }

        stopRecording() {}

        close(options) {
          closes.push(options);
        }

        configureAutoStop() {}
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));

  await page.startBookVoiceCommand.call(context);
  page.onHide.call(context);

  assert.deepEqual(closes, [{ force: true }]);
  assert.equal(context.data.bookVoiceAssistantListening, false);
  assert.equal(context.data.bookVoiceAssistantBusy, false);
  assert.equal(context.data.bookVoiceAssistantConversationActive, false);
  assert.equal(context.data.bookVoiceLiveText, "");
  assert.equal(context.data.bookVoiceCaptureStateText, "麦克风未开始");
});
