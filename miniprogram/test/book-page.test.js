require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { clearModule, loadPageModule, mockModule, pageContext } = require("./page-harness");

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
        pending_source_count: 0,
        removed_source_count: 0,
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
        pending_source_count: 0,
        removed_source_count: 0,
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
    pending_source_count: 0,
    removed_source_count: 0,
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

function sampleAutomationEvent(overrides = {}) {
  const eventType = overrides.event_type || "chapter_draft_generated";
  return {
    id: overrides.id || `event-${eventType}`,
    project_id: overrides.project_id || "project/1",
    event_type: eventType,
    status: overrides.status || "pending",
    chapter_id: Object.prototype.hasOwnProperty.call(overrides, "chapter_id") ? overrides.chapter_id : "chapter-1",
    utterance_ids: overrides.utterance_ids || [],
    summary_text: overrides.summary_text || "「外婆家的童年」已提交后台生成。",
    confidence: Object.prototype.hasOwnProperty.call(overrides, "confidence") ? overrides.confidence : null,
    metadata_json: overrides.metadata_json || {},
    utterance_details: overrides.utterance_details || [],
    created_at: overrides.created_at || "2026-06-17T00:00:00.000Z",
    updated_at: overrides.updated_at || "2026-06-17T00:00:00.000Z",
  };
}

function sampleBookOutput(
  version = 1,
  content = "外婆回忆录\n\n目录\n1. 外婆家的童年\n\n第1章 外婆家的童年\n\n正文",
  isStale = false,
  staleChapterTitles = [],
  structuredContent = null,
  versions = [],
  manuscript = null,
) {
  const latestVersion = {
    id: `book-output-version-${version}`,
    output_id: "book-output-1",
    project_id: "project/1",
    plan_id: "plan-1",
    version,
    content,
    chapter_snapshot_json: [],
    structured_content: structuredContent,
    manuscript,
    created_by: "system",
    created_at: "2026-06-17T00:00:00.000Z",
  };
  return {
    id: "book-output-1",
    project_id: "project/1",
    plan_id: "plan-1",
    title: "外婆回忆录",
    status: "draft",
    metadata_json: {},
    is_stale: isStale,
    stale_chapter_titles: staleChapterTitles,
    latest_version: latestVersion,
    versions,
    created_at: "2026-06-17T00:00:00.000Z",
    updated_at: "2026-06-17T00:00:00.000Z",
  };
}

function sampleBookOutputVersion(
  version,
  content,
  structuredContent = null,
  createdAt = "2026-06-17T00:00:00.000Z",
  manuscript = null,
) {
  return {
    id: `book-output-version-${version}`,
    output_id: "book-output-1",
    project_id: "project/1",
    plan_id: "plan-1",
    version,
    content,
    chapter_snapshot_json: [],
    structured_content: structuredContent,
    manuscript,
    created_by: "system",
    created_at: createdAt,
  };
}

function sampleBookManuscript(title = "外婆回忆录", coverStyle = "classic_plain") {
  return {
    schema_version: 1,
    profile: {
      template: "classic_family",
      title,
      subtitle: "根据口述访谈整理",
      narrator_name: "",
      prepared_by: "家人整理",
      interview_date_range: "",
      privacy_note: "",
      ai_disclosure_note: "",
    },
    front_matter: [],
    table_of_contents: [],
    chapters: [],
    back_matter: {},
    render_settings: {
      page_size: "a5",
      layout: "reading",
      font_scale: "standard",
      show_headers: true,
      show_source_appendix: true,
      photo_mode: "none",
      cover_style: coverStyle,
      back_cover_quote: "",
    },
  };
}

function sampleStructuredBookContent() {
  return {
    title: "外婆回忆录",
    chapter_count: 2,
    char_count: 24,
    table_of_contents: [
      {
        chapter_id: "chapter-1",
        index: 1,
        title: "外婆家的童年",
        char_count: 14,
        source_count: 2,
      },
      {
        chapter_id: "chapter-2",
        index: 2,
        title: "第一次离家",
        char_count: 10,
        source_count: 1,
      },
    ],
    chapters: [
      {
        chapter_id: "chapter-1",
        index: 1,
        title: "外婆家的童年",
        content: "我小时候常在院子里晒太阳。",
        char_count: 14,
        source_count: 2,
        draft_version_id: "draft-version-1",
        draft_version: 1,
      },
      {
        chapter_id: "chapter-2",
        index: 2,
        title: "第一次离家",
        content: "第一次离家去县城读书。",
        char_count: 10,
        source_count: 1,
        draft_version_id: "draft-version-2",
        draft_version: 1,
      },
    ],
  };
}

function sampleBookManuscript(title = "外婆回忆录", coverStyle = "classic_plain") {
  return {
    schema_version: 1,
    profile: {
      template: "classic_family",
      title,
      subtitle: "根据口述访谈整理",
      narrator_name: "",
      prepared_by: "家人整理",
      interview_date_range: "",
      privacy_note: "仅供家庭内部阅读。",
      ai_disclosure_note: "AI 辅助整理，不新增未经确认的事实。",
    },
    front_matter: [
      { kind: "cover", title, content: `${title}\n根据口述访谈整理`, enabled: true },
      { kind: "title_page", title: "扉页", content: "口述：外婆\n整理：家人", enabled: true },
      { kind: "generation_note", title: "生成说明", content: "本书根据口述访谈整理。", enabled: true },
      { kind: "preface", title: "前言", content: "这些故事来自家里的讲述。", enabled: true },
    ],
    table_of_contents: sampleStructuredBookContent().table_of_contents,
    chapters: [
      {
        chapter_id: "chapter-1",
        index: 1,
        title: "外婆家的童年",
        subtitle: "",
        intro: "这一章主要记录外婆小时候在院子里的生活。",
        time_hint: "童年",
        place_hint: "老家",
        pull_quotes: [{ text: "我小时候常在院子里晒太阳。", source_ref_ids: ["utterance-1"] }],
        people_notes: [],
        uncertain_facts: [],
        content: "我小时候常在院子里晒太阳。",
        source_refs: [{ utterance_id: "utterance-1", transcript_version_id: "version-1", voice_session_id: "voice-1", quote: "院子" }],
      },
      {
        chapter_id: "chapter-2",
        index: 2,
        title: "第一次离家",
        subtitle: "",
        intro: "这一章主要记录第一次离家的经历。",
        time_hint: "",
        place_hint: "县城",
        pull_quotes: [],
        people_notes: [],
        uncertain_facts: [],
        content: "第一次离家去县城读书。",
        source_refs: [],
      },
    ],
    back_matter: {
      afterword: { kind: "afterword", title: "后记", content: "这些故事还可以继续补充。", enabled: true },
      interview_records: { kind: "interview_records", title: "访谈记录", content: "当前收录两章。", enabled: true },
      version_note: { kind: "version_note", title: "版本说明", content: "默认展示故事书版。", enabled: true },
      unconfirmed_facts: { kind: "unconfirmed_facts", title: "未确认信息", content: "暂无。", enabled: true },
      source_appendix: { kind: "source_appendix", title: "章节来源", content: "第1章：1 条讲述来源", enabled: true },
    },
    render_settings: {
      page_size: "a5",
      layout: "reading",
      font_scale: "standard",
      show_headers: true,
      show_source_appendix: true,
      photo_mode: "none",
      cover_style: coverStyle,
      back_cover_quote: "我小时候常在院子里晒太阳。",
    },
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
    response_kind: "action_preview",
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

function sampleBookVoiceCommandExecuteResponse(intent, plan, coverage = sampleSourceCoverage(0)) {
  return {
    intent,
    summary: "已执行语音修改。",
    plan,
    source_coverage: coverage,
  };
}

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
          ? { ...chapter, title: command.title }
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
  bookVoiceTTSExports = {},
  streamingSpeechPlaybackExports = {},
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
    downloadMemoirBookOutputExport: async () => {
      throw new Error("downloadMemoirBookOutputExport was not mocked");
    },
    createMemoirBookPlanOutlineTTS: async () => {
      throw new Error("createMemoirBookPlanOutlineTTS was not mocked");
    },
    createMemoirBookVoiceAssistantTTS: async () => {
      throw new Error("createMemoirBookVoiceAssistantTTS was not mocked");
    },
    enqueueMemoirBookChapterDraftGeneration: async () => {
      throw new Error("enqueueMemoirBookChapterDraftGeneration was not mocked");
    },
    enqueueMemoirBookOutputGeneration: async () => {
      throw new Error("enqueueMemoirBookOutputGeneration was not mocked");
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
    listMemoirAutomationEvents: async () => ({ items: [], total: 0, limit: 8 }),
    acknowledgeMemoirAutomationEvent: async () => ({}),
    suggestMemoirBookOutputTitles: async () => {
      throw new Error("suggestMemoirBookOutputTitles was not mocked");
    },
    parseMemoirBookVoiceCommand: async () => {
      throw new Error("parseMemoirBookVoiceCommand was not mocked");
    },
    bookVoiceCommandGatewayUrl: (projectId) => `wss://api.example.test/book/${encodeURIComponent(projectId)}/gateway`,
    bookVoiceAssistantTTSStreamUrl: (projectId) => (
      `wss://api.example.test/book/${encodeURIComponent(projectId)}/assistant-tts`
    ),
    updateMemoirBookChapterTitle: async () => {
      throw new Error("updateMemoirBookChapterTitle was not mocked");
    },
    updateMemoirBookPlan: async () => {
      throw new Error("updateMemoirBookPlan was not mocked");
    },
    updateMemoirBookOutput: async () => {
      throw new Error("updateMemoirBookOutput was not mocked");
    },
    updateMemoirBookChapterDraft: async () => {
      throw new Error("updateMemoirBookChapterDraft was not mocked");
    },
    ...voiceApiExports,
  });
  mockModule("../services/book-voice-assistant-tts-stream.ts", {
    BookVoiceAssistantTTSStreamClient: class {
      async start() {
        throw new Error("BookVoiceAssistantTTSStreamClient was not mocked");
      }

      close() {}
    },
    ...bookVoiceTTSExports,
  });
  mockModule("../services/streaming-speech-playback.ts", {
    StreamingSpeechPlaybackController: streamingSpeechPlaybackExports.StreamingSpeechPlaybackController || class {
      async appendFrame() {}
      async waitForDrain() {}
      stop() {}
    },
    ...streamingSpeechPlaybackExports,
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
  assert.match(wxml, /bookVoiceCommandResponseKind/);
  assert.match(wxml, /bookVoiceCommandDetailText/);
  assert.match(wxml, /bookVoiceCommandPanelTitle/);
  assert.match(wxml, /bookVoiceAssistantSpeakingLineId/);
  assert.match(wxml, /assistant-speaking/);
  assert.match(wxml, /book-voice-command-preview/);
  assert.match(wxml, /bindtap="executeBookVoiceCommand"/);
  assert.match(wxml, /bindtap="undoBookVoiceCommand"/);
  assert.match(wxss, /\.book-voice-assistant/);
  assert.match(wxss, /\.book-voice-command-preview/);
  assert.match(wxss, /\.book-voice-command-preview\.answer/);
  assert.match(wxss, /\.book-voice-command-preview\.consultation/);
  assert.match(wxss, /#fff6f1/);
  assert.match(wxss, /#9f4a3c/);
});

test("book page exposes PDF layout and print export controls", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");

  assert.match(wxml, /PDF 排版/);
  assert.match(wxml, /bindchange="changeBookExportLayout"/);
  assert.match(wxml, /阅读版 PDF/);
  assert.match(wxml, /打印版 PDF/);
  assert.match(wxml, /data-layout="large_print"/);
  assert.match(wxml, /口述来源索引/);
  assert.match(wxml, /bindchange="toggleBookExportSourceAppendix"/);
});

test("book page exposes all PDF cover style options", () => {
  const { page } = loadBookPage();
  const context = pageContext(page);

  assert.deepEqual(
    context.data.bookCoverStyleOptions.map((option) => option.value),
    ["classic_plain", "warm_gray", "dark_album", "moonlit_courtyard", "navy_gilt"],
  );
  assert.equal(
    context.data.bookCoverStyleOptions.find((option) => option.value === "moonlit_courtyard").label,
    "月下庭院",
  );
  assert.equal(
    context.data.bookCoverStyleOptions.find((option) => option.value === "navy_gilt").label,
    "深蓝烫金",
  );
});

test("book page exposes manuscript structure sections", () => {
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");

  assert.match(wxml, /书稿设置/);
  assert.match(wxml, /成书信息/);
  assert.match(wxml, /bindinput="changeBookTitle"/);
  assert.match(wxml, /data-key="narrator_name"/);
  assert.match(wxml, /data-key="preface"/);
  assert.match(wxml, /data-key="afterword"/);
  assert.match(wxml, /data-key="back_cover_quote"/);
  assert.match(wxml, /bindinput="changeBookMetadataField"/);
  assert.match(wxml, /bindtap="suggestBookTitles"/);
  assert.match(wxml, /bindchange="changeBookCoverStyle"/);
  assert.match(wxml, /bookDesignSaveStatusText/);
  assert.doesNotMatch(wxml, /bindtap="saveBookDesignSettings"/);
  assert.match(wxml, /书籍结构/);
  assert.match(wxml, /章节书稿/);
  assert.match(wxml, /附录与说明/);
  assert.match(wxml, /预览与导出/);
  assert.match(wxml, /bookManuscriptStructureItems/);
  assert.match(wxml, /bookManuscriptChapterItems/);
  assert.match(wxml, /bookManuscriptBackMatterItems/);
  assert.match(wxml, /bindtap="toggleBookPreviewChapters"/);
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
  const { mod, page } = loadBookPage();
  const output = sampleBookOutput(3, "全书正文", false, [], sampleStructuredBookContent(), [], sampleBookManuscript());
  const context = pageContext(page);
  page.applyBookOutput.call(context, output);
  const data = context.data;

  assert.equal(mod.latestBookContent(output), "全书正文");
  assert.equal(mod.bookOutputVersionText(output), "全书第 3 版");
  assert.equal(mod.bookOutputStaleNotice(output), "");
  assert.equal(mod.bookOutputVersionText(null), "尚未生成全书");
  assert.equal(data.bookPreviewTitle, "外婆回忆录");
  assert.equal(data.bookPreviewTocItems.length, 2);
  assert.equal(data.bookPreviewTocItems[0].metaText, "14 字 · 2 条素材");
  assert.equal(data.bookPreviewChaptersExpanded, false);
  assert.equal(data.bookPreviewChapters[0].indexText, "第 1 章");
  assert.equal(data.bookPreviewChapters[0].content, "我小时候常在院子里晒太阳。");
  assert.equal(data.bookPreviewChapters[0].metaText, "14 字 · 2 条素材 · 第 1 版");
  assert.equal(data.bookManuscriptAvailable, true);
  assert.equal(data.bookManuscriptTemplateLabel, "素雅书籍版");
  assert.equal(data.bookManuscriptSettingsText, "A5 · 阅读排版 · 含页眉 · 含来源附录");
  assert.equal(data.bookSubtitleInput, "根据口述访谈整理");
  assert.equal(data.bookPrefaceInput, "这些故事来自家里的讲述。");
  assert.equal(data.bookAfterwordInput, "这些故事还可以继续补充。");
  assert.equal(data.bookBackCoverQuoteInput, "我小时候常在院子里晒太阳。");
  assert.equal(data.bookManuscriptStructureItems.some((item) => item.title === "目录"), true);
  assert.equal(data.bookManuscriptFrontMatterItems.length, 4);
  assert.equal(data.bookManuscriptChapterItems[0].intro, "这一章主要记录外婆小时候在院子里的生活。");
  assert.equal(data.bookManuscriptChapterItems[0].quoteText, "我小时候常在院子里晒太阳。");
  assert.equal(data.bookManuscriptBackMatterItems.some((item) => item.title === "章节来源"), true);
  assert.equal(
    mod.bookOutputStaleNotice(sampleBookOutput(3, "全书正文", true, ["外婆家的童年", "第一次离家", "县城生活"])),
    "章节「外婆家的童年、第一次离家」等章节已更新，请重新生成全书。",
  );
});

test("book page toggles full-book chapter body preview", () => {
  const { page } = loadBookPage();
  const context = pageContext(page);
  page.applyBookOutput.call(
    context,
    sampleBookOutput(3, "全书正文", false, [], sampleStructuredBookContent(), [], sampleBookManuscript()),
  );

  assert.equal(context.data.bookPreviewChaptersExpanded, false);
  page.toggleBookPreviewChapters.call(context);
  assert.equal(context.data.bookPreviewChaptersExpanded, true);
  page.toggleBookPreviewChapters.call(context);
  assert.equal(context.data.bookPreviewChaptersExpanded, false);
});

test("book page preserves moonlit courtyard cover style from latest manuscript", () => {
  const { page } = loadBookPage();
  const context = pageContext(page);
  page.applyBookOutput.call(
    context,
    sampleBookOutput(
      1,
      "全书正文",
      false,
      [],
      sampleStructuredBookContent(),
      [],
      sampleBookManuscript("外婆回忆录", "moonlit_courtyard"),
    ),
  );

  assert.equal(context.data.bookCoverStyle, "moonlit_courtyard");
  assert.equal(context.data.bookCoverStyleLabel, "月下庭院");
  assert.equal(context.data.bookCoverStyleChanged, false);
});

test("book page preserves navy gilt cover style from latest manuscript", () => {
  const { page } = loadBookPage();
  const context = pageContext(page);
  page.applyBookOutput.call(
    context,
    sampleBookOutput(
      1,
      "全书正文",
      false,
      [],
      sampleStructuredBookContent(),
      [],
      sampleBookManuscript("外婆回忆录", "navy_gilt"),
    ),
  );

  assert.equal(context.data.bookCoverStyle, "navy_gilt");
  assert.equal(context.data.bookCoverStyleLabel, "深蓝烫金");
  assert.equal(context.data.bookCoverStyleChanged, false);
});

test("book page switches full-book output versions without changing the latest output", () => {
  const { page } = loadBookPage();
  const latestStructured = sampleStructuredBookContent();
  const historicalStructured = {
    ...sampleStructuredBookContent(),
    chapters: [
      {
        ...sampleStructuredBookContent().chapters[0],
        content: "旧版第一章正文。",
        char_count: 8,
      },
    ],
    table_of_contents: [
      {
        ...sampleStructuredBookContent().table_of_contents[0],
        char_count: 8,
      },
    ],
  };
  const output = sampleBookOutput(2, "全书第二版正文", false, [], latestStructured, [
    sampleBookOutputVersion(1, "全书第一版正文", historicalStructured, "2026-06-16T00:00:00.000Z"),
    sampleBookOutputVersion(2, "全书第二版正文", latestStructured, "2026-06-17T00:00:00.000Z"),
  ]);
  const context = pageContext(page);

  page.applyBookOutput.call(context, output);

  assert.equal(context.data.bookSelectedVersionId, "book-output-version-2");
  assert.equal(context.data.bookVersionOptions.length, 2);
  assert.equal(context.data.bookVersionText, "全书第 2 版");
  assert.equal(context.data.bookContent, "全书第二版正文");
  assert.equal(context.data.bookViewingHistoricalVersion, false);
  page.toggleBookPreviewChapters.call(context);
  assert.equal(context.data.bookPreviewChaptersExpanded, true);

  page.changeBookOutputVersion.call(context, { currentTarget: { dataset: {} }, detail: { value: "1" } });

  assert.equal(context.data.bookSelectedVersionId, "book-output-version-1");
  assert.equal(context.data.bookVersionText, "全书第 1 版");
  assert.equal(context.data.bookContent, "全书第一版正文");
  assert.equal(context.data.bookPreviewChaptersExpanded, false);
  assert.equal(context.data.bookPreviewChapters[0].content, "旧版第一章正文。");
  assert.equal(context.data.bookViewingHistoricalVersion, true);
  assert.equal(context.data.bookVersionNotice, "正在查看历史版本。朗读前请切回最新版。");
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
  const wxml = fs.readFileSync(path.join(__dirname, "../pages/book/index.wxml"), "utf8");
  assert.match(wxml, /suggestedActionText/);
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
  assert.equal(context.data.automationEventItems[0].actionText, "知道了");
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
  assert.equal(item.actionText, "知道了");
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

test("book page keeps background generation busy when automation polling fails", async () => {
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
    sampleAutomationEvent({ id: "event-draft-pending", chapter_id: "chapter-1" }),
  ]);
  page.clearBookGenerationEventPoll.call(context);

  await page.refreshAutomationEventsAfterGeneration.call(context);

  assert.equal(context.data.automationEventError, "network unavailable");
  assert.equal(context.data.chapters[0].draftBusy, true);
  assert.equal(context.data.automationEventItems[0].titleText, "章节生成中");
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
            type: "revise_chapter",
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
          type: "revise_chapter",
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
  assert.equal(items[1].suggestionItems[0].labelText, "建议补写");
  assert.equal(items[1].suggestionItems[0].bodyText, "补写本章");
  assert.equal(items[1].suggestionItems[1].labelText, "待整理素材");
  assert.equal(items[1].suggestionItems[1].bodyText, "还有 2 条讲述需要归章或确认。");
  assert.equal(items[1].suggestionItems[2].labelText, "建议补问");
  assert.equal(items[1].suggestionItems[2].bodyText, "外婆在院子里最常做什么？");
  assert.equal(items[1].suggestionItems[2].chapterText, "《院子里的童年》");
  assert.equal(items[1].actionText, "知道了");
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
  assert.equal(chapters[0].draftActionText, "补写新增");
  assert.equal(chapters[0].sourceChangeNotice, "1 条新增讲述待补写");
  assert.equal(chapters[1].draftActionText, "生成本章");
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

  await page.addUncoveredSourceAsChapter.call(context, {
    currentTarget: { dataset: { id: "utterance-new-1" } },
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].projectId, "project/1");
  assert.deepEqual(calls[0].data, {
    type: "create_chapter_from_source",
    utterance_id: "utterance-new-1",
  });
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

test("book page deletes confirmed chapter through structure command", async () => {
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
      applyMemoirBookPlanStructureCommand: async (projectId, data) => {
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
  assert.deepEqual(calls[0].data, {
    type: "delete_chapter",
    chapter_id: "chapter-1",
  });
  assert.equal(context.data.chapters.length, 1);
  assert.equal(context.data.chapters[0].id, "chapter-2");
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
      enqueueMemoirBookChapterDraftGeneration: async (projectId, chapterId, data) => {
        calls.push({ kind: "draft", projectId, chapterId, data });
        return sampleAutomationEvent({
          id: "event-merged-draft",
          chapter_id: chapterId,
          summary_text: "「童年里的离别」已提交后台生成。",
        });
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
  assert.deepEqual(calls[0].data, {
    type: "merge_chapters",
    chapter_id: "chapter-2",
    target_chapter_id: "chapter-1",
  });
  assert.deepEqual(calls[2].data, { force_regenerate: false });
  assert.equal(context.data.chapters[0].title, "童年里的离别");
  assert.equal(context.data.chapters[0].draftBusy, true);
  assert.equal(context.data.automationEventItems[0].titleText, "章节生成中");
  page.clearBookGenerationEventPoll.call(context);
  delete global.wx;
});

test("book page suggests and autosaves full-book title plus cover style", async () => {
  const calls = [];
  global.wx = { showToast() {} };
  const { page } = loadBookPage({
    voiceApiExports: {
      suggestMemoirBookOutputTitles: async (projectId, data) => {
        calls.push({ kind: "suggest", projectId, data });
        return { suggestions: ["外婆慢慢说", "从前的日子"], provider: "ai", model: "fake" };
      },
      updateMemoirBookOutput: async (projectId, data) => {
        calls.push({ kind: "update", projectId, data });
        const output = sampleBookOutput(
          1,
          "外婆慢慢说\n\n目录\n1. 外婆家的童年",
          false,
          [],
          null,
          [],
          sampleBookManuscript(data.title, data.cover_style),
        );
        output.title = data.title;
        return output;
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "confirmed"),
    "chapter-2": sampleDraft(1, "confirmed"),
  });
  page.applyBookOutput.call(
    context,
    sampleBookOutput(1, "外婆回忆录", false, [], null, [], sampleBookManuscript("外婆回忆录")),
  );

  await page.suggestBookTitles.call(context);
  page.chooseBookTitleSuggestion.call(context, { currentTarget: { dataset: { title: "外婆慢慢说" } } });
  page.changeBookMetadataField.call(context, {
    currentTarget: { dataset: { key: "narrator_name" } },
    detail: { value: "外婆" },
  });
  page.changeBookMetadataField.call(context, {
    currentTarget: { dataset: { key: "prepared_by" } },
    detail: { value: "小林整理" },
  });
  page.changeBookMetadataField.call(context, {
    currentTarget: { dataset: { key: "preface" } },
    detail: { value: "这是写给家人的前言。" },
  });
  page.changeBookCoverStyle.call(context, {
    currentTarget: { dataset: {} },
    detail: { value: 1 },
  });
  await page.autosaveBookDesignSettings.call(context);

  assert.deepEqual(calls[0], { kind: "suggest", projectId: "project/1", data: { count: 6 } });
  assert.deepEqual(calls[1], {
    kind: "update",
    projectId: "project/1",
    data: {
      title: "外婆慢慢说",
      subtitle: "根据口述访谈整理",
      narrator_name: "外婆",
      prepared_by: "小林整理",
      preface: "这是写给家人的前言。",
      afterword: "这些故事还可以继续补充。",
      back_cover_quote: "我小时候常在院子里晒太阳。",
      cover_style: "warm_gray",
    },
  });
  assert.equal(context.data.bookTitleInput, "外婆慢慢说");
  assert.equal(context.data.bookTitleChanged, false);
  assert.equal(context.data.bookMetadataChanged, false);
  assert.equal(context.data.bookNarratorNameInput, "外婆");
  assert.equal(context.data.bookPreparedByInput, "小林整理");
  assert.equal(context.data.bookCoverStyle, "warm_gray");
  assert.equal(context.data.bookCoverStyleChanged, false);
  assert.equal(context.data.bookDesignSaveStatusText, "已自动保存");
  delete global.wx;
});

test("book page submits full book output generation as a background job", async () => {
  const calls = [];
  global.wx = { showToast() {} };
  const { page } = loadBookPage({
    voiceApiExports: {
      enqueueMemoirBookOutputGeneration: async (projectId, data) => {
        calls.push({ projectId, data });
        return sampleAutomationEvent({
          id: "event-book-output",
          event_type: "book_output_generated",
          status: "pending",
          chapter_id: null,
          summary_text: "全书已提交后台生成。",
        });
      },
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
    sampleBookOutput(1, "全书第一版正文", false, [], null, [], sampleBookManuscript()),
  );

  await page.generateBook.call(context);

  assert.deepEqual(calls[0], {
    projectId: "project/1",
    data: {
      force_regenerate: true,
      title: "外婆回忆录",
      subtitle: "根据口述访谈整理",
      prepared_by: "家人整理",
      preface: "这些故事来自家里的讲述。",
      afterword: "这些故事还可以继续补充。",
      back_cover_quote: "我小时候常在院子里晒太阳。",
      cover_style: "classic_plain",
    },
  });
  assert.equal(context.data.generatingBook, true);
  assert.equal(context.data.automationEventItems.length, 1);
  assert.equal(context.data.automationEventItems[0].titleText, "全书生成中");
  assert.equal(context.data.bookVersionText, "全书第 1 版");
  page.clearBookGenerationEventPoll.call(context);
  delete global.wx;
});

test("book page shows submit errors when full-book background generation cannot be queued", async () => {
  global.wx = { showToast() {} };
  const { page } = loadBookPage({
    voiceApiExports: {
      enqueueMemoirBookOutputGeneration: async () => {
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

test("book page links structured full-book preview back to chapter editing", () => {
  const scrollCalls = [];
  global.wx = {
    pageScrollTo(options) {
      scrollCalls.push(options);
    },
  };
  const { page } = loadBookPage();
  const context = pageContext(page, { projectId: "project/1", projectTitle: "外婆回忆录" });
  page.applyPlan.call(context, samplePlan("confirmed"), {
    "chapter-1": sampleDraft(1, "confirmed"),
    "chapter-2": sampleDraft(1, "confirmed"),
  });
  page.applyBookOutput.call(
    context,
    sampleBookOutput(1, "全书正文", false, [], sampleStructuredBookContent()),
  );

  assert.equal(context.data.bookPreviewChapters.length, 2);
  page.editBookOutputChapter.call(context, { currentTarget: { dataset: { id: "chapter-2" } } });

  assert.deepEqual(scrollCalls[0], {
    selector: "#chapter-editor-chapter-2",
    duration: 260,
    offsetTop: 12,
  });
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

test("book page blocks full-book audio while viewing a historical version", async () => {
  const calls = [];
  const { page } = loadBookPage({
    voiceApiExports: {
      createMemoirBookOutputTTS: async () => {
        calls.push("tts");
        return sampleAudioResponse();
      },
    },
  });
  const output = sampleBookOutput(2, "全书第二版正文", false, [], sampleStructuredBookContent(), [
    sampleBookOutputVersion(1, "全书第一版正文", sampleStructuredBookContent(), "2026-06-16T00:00:00.000Z"),
    sampleBookOutputVersion(2, "全书第二版正文", sampleStructuredBookContent(), "2026-06-17T00:00:00.000Z"),
  ]);
  const context = pageContext(page, {
    projectId: "project/1",
    allChaptersConfirmed: true,
    canGenerateBook: true,
    hasDraftChanges: false,
  });
  page.applyBookOutput.call(context, output);
  page.changeBookOutputVersion.call(context, { currentTarget: { dataset: {} }, detail: { value: "1" } });

  await page.playBookAudio.call(context);

  assert.equal(context.data.error, "正在查看历史版本，请先切回最新版再朗读");
  assert.equal(calls.length, 0);
});

test("book page downloads selected full-book output export and opens PDF", async () => {
  const calls = [];
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
  const { page } = loadBookPage({
    voiceApiExports: {
      downloadMemoirBookOutputExport: async (projectId, format, versionId, layout, showSourceAppendix) => {
        calls.push({ projectId, format, versionId, layout, showSourceAppendix });
        return "/tmp/book.pdf";
      },
    },
  });
  const output = sampleBookOutput(2, "全书第二版正文", false, [], sampleStructuredBookContent(), [
    sampleBookOutputVersion(1, "全书第一版正文", sampleStructuredBookContent(), "2026-06-16T00:00:00.000Z"),
    sampleBookOutputVersion(2, "全书第二版正文", sampleStructuredBookContent(), "2026-06-17T00:00:00.000Z"),
  ]);
  const context = pageContext(page, {
    projectId: "project/1",
    hasDraftChanges: false,
  });
  page.applyBookOutput.call(context, output);
  page.changeBookOutputVersion.call(context, { currentTarget: { dataset: {} }, detail: { value: "1" } });
  page.changeBookExportLayout.call(context, { currentTarget: { dataset: {} }, detail: { value: "1" } });
  page.toggleBookExportSourceAppendix.call(context, {
    currentTarget: { dataset: {} },
    detail: { value: false },
  });

  await page.downloadBookOutputExport.call(context, {
    currentTarget: { dataset: { format: "pdf" } },
  });

  assert.deepEqual(calls[0], {
    projectId: "project/1",
    format: "pdf",
    versionId: "book-output-version-1",
    layout: "large_print",
    showSourceAppendix: false,
  });
  assert.equal(context.data.bookExportLayoutLabel, "打印版");
  assert.equal(opened[0].filePath, "/tmp/book.pdf");
  assert.equal(opened[0].fileType, "pdf");
  assert.equal(opened[0].showMenu, true);
  assert.equal(toasts[0].title, "PDF 已打开");
  assert.equal(context.data.bookExportBusyTarget, "");
  delete global.wx;
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
    streamingAsrExports: {
      MemoirStreamingAsrClient: streamingAsrClientForFinal(calls, "把第一张改名为童年往事"),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applySourceCoverage.call(context, sampleSourceCoverage(1));
  context.playBookVoiceAssistantReply = async (text, options) => {
    calls.push({ kind: "reply", text, options });
    return true;
  };

  await page.startBookVoiceCommand.call(context);
  await flushAsyncWork();

  assert.deepEqual(calls[0], {
    kind: "client",
    gatewayUrl: "wss://api.example.test/book/project%2F1/gateway",
  });
  assert.deepEqual(calls[2], {
    kind: "record",
    options: { noVoiceActivityTimeoutMs: 3500 },
  });
  assert.deepEqual(calls[3], {
    kind: "parse",
    projectId: "project/1",
    data: {
      text: "把第一章改名为童年往事",
      book_context: {
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
      },
    },
  });
  assert.equal(context.data.bookVoiceCommandText, "把第一章改名为童年往事");
  assert.equal(context.data.bookVoiceCommandIntentLabel, "修改章名");
  assert.equal(context.data.bookVoiceCommandResponseKind, "action_preview");
  assert.equal(context.data.bookVoiceCommandPanelTitle, "操作预览");
  assert.equal(context.data.bookVoiceCommandStateText, "待确认");
  assert.equal(context.data.bookVoiceCommandDetailText, "准备把「外婆家的童年」改名为「童年往事」。");
  assert.equal(context.data.bookVoiceCommandSummary, "准备把「外婆家的童年」改名为「童年往事」。");
  assert.equal(context.data.bookVoiceCommandRequiresConfirmation, true);
  assert.equal(context.data.bookVoiceAssistantStatusText, "已理解，等待确认");
  assert.equal(context.data.bookVoiceAssistantListening, false);
  assert.equal(context.data.bookVoiceAssistantBusy, false);
  assert.equal(context.data.bookVoiceAssistantExpanded, true);
  assert.equal(context.data.bookVoiceTranscriptLines[0].role, "user");
  assert.equal(context.data.bookVoiceTranscriptLines[0].text, "把第一章改名为童年往事");
  assert.deepEqual(calls[4], { kind: "toast", title: "已识别" });
  assert.deepEqual(calls[5], {
    kind: "reply",
    text: "我理解了，准备把「外婆家的童年」改名为「童年往事」。 如果没问题，说确认执行，我再改。",
    options: { autoListenAfterPlayback: true },
  });
  delete global.wx;
});

test("book page sends recent voice editing context when parsing follow-up questions", async () => {
  const calls = [];
  global.wx = { showToast(options) { calls.push({ kind: "toast", title: options.title }); } };
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
  const { page } = loadBookPage({
    voiceApiExports: {
      parseMemoirBookVoiceCommand: async (projectId, data) => {
        calls.push({ kind: "parse", projectId, data });
        return answerPreview;
      },
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
  context.playBookVoiceAssistantReply = async (text, options) => {
    calls.push({ kind: "reply", text, options });
    return true;
  };

  await page.startBookVoiceCommand.call(context);
  await flushAsyncWork();

  assert.deepEqual(calls[3], {
    kind: "parse",
    projectId: "project/1",
    data: {
      text: "现在有几个章节啊",
      conversation_context: [
        { role: "user", text: "我可以修改些啥" },
        { role: "assistant", text: "可以改章名、调顺序、合并章节。" },
      ],
    },
  });
  assert.equal(context.data.bookVoiceAssistantStatusText, "已回答");
  assert.equal(context.data.bookVoiceCommandResponseKind, "answer");
  assert.equal(context.data.bookVoiceCommandPanelTitle, "回答");
  assert.equal(context.data.bookVoiceCommandStateText, "已回答");
  assert.equal(
    context.data.bookVoiceCommandDetailText,
    "现在目录有 2 章：第一章「外婆家的童年」，第二章「第一次离家」。",
  );
  assert.deepEqual(calls[5], {
    kind: "reply",
    text: "现在目录有 2 章：第一章「外婆家的童年」，第二章「第一次离家」。",
    options: { autoListenAfterPlayback: true },
  });
  delete global.wx;
});

test("book page sends source coverage context when parsing book voice questions", async () => {
  const calls = [];
  global.wx = { showToast(options) { calls.push({ kind: "toast", title: options.title }); } };
  const answerPreview = sampleBookVoiceCommandPreview({
    original_text: "现在是不是有记录还没归到章节呀",
    intent: "answer_book_question",
    intent_label: "成书问答",
    response_kind: "answer",
    summary: "回答当前素材收入章节状态。",
    reply_text: "这本书现在还有 7 条未收入章节。",
    operations: [],
    requires_confirmation: false,
  });
  const { page } = loadBookPage({
    voiceApiExports: {
      parseMemoirBookVoiceCommand: async (projectId, data) => {
        calls.push({ kind: "parse", projectId, data });
        return answerPreview;
      },
    },
    streamingAsrExports: {
      MemoirStreamingAsrClient: streamingAsrClientForFinal(calls, "现在是不是有记录还没归到章节呀"),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  context.setData({ sourceCoverage: sampleSourceCoverage(7) });
  context.playBookVoiceAssistantReply = async (text, options) => {
    calls.push({ kind: "reply", text, options });
  };

  await page.startBookVoiceCommand.call(context);
  await flushAsyncWork();

  assert.deepEqual(calls[3], {
    kind: "parse",
    projectId: "project/1",
    data: {
      text: "现在是不是有记录还没归到章节呀",
      book_context: {
        source_coverage: {
          total_source_count: 9,
          covered_source_count: 2,
          uncovered_source_count: 7,
        },
        uncovered_sources: Array.from({ length: 7 }, (_, index) => ({
          source_index: index + 3,
          utterance_id: `utterance-new-${index + 1}`,
          text: "新增转写",
        })),
      },
    },
  });
  assert.equal(context.data.bookVoiceCommandResponseKind, "answer");
  assert.deepEqual(calls[5], {
    kind: "reply",
    text: "这本书现在还有 7 条未收入章节。",
    options: { autoListenAfterPlayback: true },
  });
  delete global.wx;
});

test("book page treats reading an uncovered source as a spoken answer", async () => {
  const calls = [];
  global.wx = { showToast(options) { calls.push({ kind: "toast", title: options.title }); } };
  const answerPreview = sampleBookVoiceCommandPreview({
    original_text: "你给我念一下第一条没收入的记录吧",
    intent: "read_uncovered_source",
    intent_label: "朗读记录",
    response_kind: "answer",
    summary: "读出一条待收录讲述记录。",
    reply_text: "待收录第 1 条，语音记录 3：新增转写。你可以说，把这条归到第几章。",
    operations: [],
    requires_confirmation: false,
  });
  const { page } = loadBookPage({
    voiceApiExports: {
      parseMemoirBookVoiceCommand: async (projectId, data) => {
        calls.push({ kind: "parse", projectId, data });
        return answerPreview;
      },
    },
    streamingAsrExports: {
      MemoirStreamingAsrClient: streamingAsrClientForFinal(calls, "你给我念一下第一条没收入的记录吧"),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applySourceCoverage.call(context, sampleSourceCoverage(1));
  context.playBookVoiceAssistantReply = async (text, options) => {
    calls.push({ kind: "reply", text, options });
  };

  await page.startBookVoiceCommand.call(context);
  await flushAsyncWork();

  assert.equal(context.data.bookVoiceCommandResponseKind, "answer");
  assert.equal(context.data.bookVoiceCommandPanelTitle, "回答");
  assert.equal(context.data.bookVoiceCommandStateText, "已回答");
  assert.equal(context.data.bookVoiceCommandCanExecute, false);
  assert.deepEqual(calls[5], {
    kind: "reply",
    text: "待收录第 1 条，语音记录 3：新增转写。你可以说，把这条归到第几章。",
    options: { autoListenAfterPlayback: true },
  });
  delete global.wx;
});

test("book page treats voice editing advice as a spoken consultation", async () => {
  const calls = [];
  global.wx = { showToast(options) { calls.push({ kind: "toast", title: options.title }); } };
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
  const { page } = loadBookPage({
    voiceApiExports: {
      parseMemoirBookVoiceCommand: async (projectId, data) => {
        calls.push({ kind: "parse", projectId, data });
        return consultationPreview;
      },
    },
    streamingAsrExports: {
      MemoirStreamingAsrClient: streamingAsrClientForFinal(calls, "第一章是不是太短了"),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  context.playBookVoiceAssistantReply = async (text, options) => {
    calls.push({ kind: "reply", text, options });
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
  assert.equal(context.data.bookVoiceAssistantStatusText, "已给建议");
  assert.equal(context.data.bookVoiceTranscriptLines[0].role, "user");
  assert.equal(context.data.bookVoiceTranscriptLines[0].text, "第一章是不是太短了");
  assert.deepEqual(calls[5], {
    kind: "reply",
    text: "这一章现在明显偏短，建议补一个具体场景或人物对话。",
    options: { autoListenAfterPlayback: true },
  });
  delete global.wx;
});

test("book page plays assistant reply and auto-listens after playback", async () => {
  const calls = [];
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  global.setTimeout = (callback) => {
    callback();
    return 1;
  };
  global.clearTimeout = () => {};
  const { page } = loadBookPage({
    bookVoiceTTSExports: {
      BookVoiceAssistantTTSStreamClient: class {
        constructor(projectId, callbacks) {
          this.projectId = projectId;
          this.callbacks = callbacks;
        }

        async start(options) {
          calls.push({ kind: "tts", projectId: this.projectId, options });
          this.callbacks.onStarted?.({ type: "tts.started", project_id: this.projectId });
          this.callbacks.onAudioFrame?.({
            streamId: "tts-1",
            sequence: 1,
            audio: new Uint8Array([1, 2, 3]),
            mimeType: "audio/L16",
            sampleRate: 16000,
            channels: 1,
            bitsPerSample: 16,
          });
          this.callbacks.onAudioDone?.({
            type: "audio.done",
            stream_id: "tts-1",
            project_id: this.projectId,
            frame_count: 1,
            duration_seconds: 0.2,
          });
          this.callbacks.onCompleted?.({ type: "tts.completed", project_id: this.projectId });
          return { type: "tts.completed", project_id: this.projectId };
        }

        close() {
          calls.push({ kind: "close" });
        }
      },
    },
    streamingSpeechPlaybackExports: {
      StreamingSpeechPlaybackController: class {
        async appendFrame(frame) {
          calls.push({ kind: "frame", frame });
        }
        async waitForDrain() {
          calls.push({ kind: "drain" });
        }
        stop() {
          calls.push({ kind: "stop" });
        }
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  context.setData({ bookVoiceAssistantConversationActive: true });
  context.audioPlayback.playTempFile = async () => {
    throw new Error("book voice assistant reply should use PCM frame playback");
  };
  context.startBookVoiceCommand = async (options) => {
    calls.push({ kind: "auto-listen", options });
  };

  try {
    await page.playBookVoiceAssistantReply.call(context, "我理解了，请确认。", {
      autoListenAfterPlayback: true,
    });

    assert.equal(calls[0].kind, "tts");
    assert.deepEqual(calls[0].options, { text: "我理解了，请确认。" });
    assert.equal(calls[1].kind, "frame");
    assert.deepEqual(Array.from(calls[1].frame.audio), [1, 2, 3]);
    assert.equal(calls[1].frame.sampleRate, 16000);
    assert.equal(calls[2].kind, "drain");
    assert.equal(context.data.bookVoiceAssistantSpeakingLineId, "");
    assert.deepEqual(calls[3], { kind: "auto-listen", options: { autoTriggered: true } });
    assert.equal(calls[4].kind, "stop");
    assert.equal(context.data.bookVoiceAssistantBusy, false);
  } finally {
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
});

test("book page drives voice assistant reply highlight from streaming playback progress", async () => {
  let currentTimeSeconds = 10.5;
  const { page } = loadBookPage({
    streamingSpeechPlaybackExports: {
      StreamingSpeechPlaybackController: class {
        async appendFrame() {
          return {
            frameIndex: 0,
            startAtSeconds: 10,
            durationSeconds: 2,
            ended: Promise.resolve(),
          };
        }
        stats() {
          return {
            currentTimeSeconds,
            segmentCount: 1,
            underrunCount: 0,
            nextPlayTimeSeconds: 12,
            queuedAheadSeconds: 1.5,
          };
        }
        stop() {}
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  context.playToken = 7;

  try {
    await page.playBookVoiceAssistantAudioFrame.call(
      context,
      {
        audio: new Uint8Array([1, 2, 3]),
        mimeType: "audio/L16",
        sampleRate: 16000,
        channels: 1,
        bitsPerSample: 16,
      },
      7,
      "外婆回忆录",
    );

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
  context.bookVoiceTTSStreaming = {
    close() {
      calls.push({ kind: "tts-close" });
    },
  };
  context.bookVoiceTTSPlaybackController = {
    stop() {
      calls.push({ kind: "playback-stop" });
    },
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
  assert.deepEqual(calls.map((call) => call.kind), ["tts-close", "playback-stop", "client", "connect", "record"]);
});

test("book page keeps pending preview during auto-listen for voice confirmation", async () => {
  const calls = [];
  const { page } = loadBookPage({
    streamingAsrExports: {
      MemoirStreamingAsrClient: streamingAsrClientForFinal(calls, "确认执行。"),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applyBookVoiceCommandPreview.call(context, sampleBookVoiceCommandPreview());
  context.playBookVoiceAssistantReply = async (text, options) => {
    calls.push({ kind: "reply", text, options });
    return true;
  };
  context.executeBookVoiceCommand = async (options) => {
    calls.push({ kind: "execute", options });
  };

  await page.startBookVoiceCommand.call(context, { autoTriggered: true });
  await flushAsyncWork();

  assert.equal(context.data.bookVoiceCommandPreview.intent, "rename_chapter");
  assert.equal(context.data.bookVoiceCommandText, "把第一章改名为童年往事");
  assert.deepEqual(calls[3], {
    kind: "reply",
    text: "好的，我按刚才理解的修改。",
    options: { autoListenAfterPlayback: false },
  });
  assert.deepEqual(calls[4], {
    kind: "execute",
    options: { confirmedByVoice: true, autoListenAfterExecution: true },
  });
});

test("book page does not execute a voice confirmation when assistant reply is interrupted", async () => {
  const calls = [];
  const { page } = loadBookPage({
    streamingAsrExports: {
      MemoirStreamingAsrClient: streamingAsrClientForFinal(calls, "确认执行。"),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
  page.applyPlan.call(context, samplePlan("confirmed"));
  page.applyBookVoiceCommandPreview.call(context, sampleBookVoiceCommandPreview());
  context.playBookVoiceAssistantReply = async (text, options) => {
    calls.push({ kind: "reply", text, options });
    return false;
  };
  context.executeBookVoiceCommand = async (options) => {
    calls.push({ kind: "execute", options });
  };

  await page.startBookVoiceCommand.call(context, { autoTriggered: true });
  await flushAsyncWork();

  assert.deepEqual(calls[3], {
    kind: "reply",
    text: "好的，我按刚才理解的修改。",
    options: { autoListenAfterPlayback: false },
  });
  assert.equal(calls.some((call) => call.kind === "execute"), false);
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
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
  };
  const plan = samplePlan("confirmed");
  const executedPlan = planFromStructureCommand("confirmed", {
    type: "rename_chapter",
    chapter_id: "chapter-1",
    title: "童年往事",
  });
  const calls = [];
  const { page } = loadBookPage({
    voiceApiExports: {
      executeMemoirBookVoiceCommand: async (projectId, data) => {
        calls.push({ projectId, data });
        return sampleBookVoiceCommandExecuteResponse(data.intent, executedPlan);
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
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
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
  };
  const calls = [];
  const movedPlan = planFromStructureCommand("confirmed", {
    type: "move_chapter",
    chapter_id: "chapter-2",
    direction: "up",
  });
  const { page } = loadBookPage({
    voiceApiExports: {
      executeMemoirBookVoiceCommand: async (projectId, data) => {
        calls.push({ projectId, data });
        return sampleBookVoiceCommandExecuteResponse(data.intent, movedPlan);
      },
    },
  });
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
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
  };
  const calls = [];
  const movedPlan = planFromStructureCommand("confirmed", {
    type: "move_chapter",
    chapter_id: "chapter-1",
    target_position: "last",
  });
  const { page } = loadBookPage({
    voiceApiExports: {
      executeMemoirBookVoiceCommand: async (projectId, data) => {
        calls.push({ projectId, data });
        return sampleBookVoiceCommandExecuteResponse(data.intent, movedPlan);
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
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
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
    showToast() {},
  };
  const assignedPlan = samplePlan("confirmed");
  assignedPlan.chapters[1].source_refs = [sampleRef("utterance-new-1", "新增转写")];
  const calls = [];
  const { page } = loadBookPage({
    voiceApiExports: {
      executeMemoirBookVoiceCommand: async (projectId, data) => {
        calls.push({ projectId, data });
        return sampleBookVoiceCommandExecuteResponse(data.intent, assignedPlan, sampleSourceCoverage(0));
      },
      getMemoirBookChapterDraft: async () => sampleDraft(1, "draft"),
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
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
  global.wx = {
    showModal(options) {
      options.success({ confirm: true });
    },
  };
  const { page } = loadBookPage({
    voiceApiExports: {
      executeMemoirBookVoiceCommand: async () => {
        throw new Error("章节已经不存在");
      },
    },
  });
  const context = pageContext(page, { projectId: "project/1" });
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
      enqueueMemoirBookChapterDraftGeneration: async (projectId, chapterId, data) => {
        calls.push({ projectId, chapterId, data });
        return sampleAutomationEvent({ id: "event-draft-regenerate", chapter_id: chapterId });
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
  assert.equal(context.data.chapters[0].draftVersionText, "第 1 版");
  assert.equal(context.data.chapters[0].draftBusy, true);
  assert.equal(context.data.chapters[0].draftChanged, false);
  assert.equal(context.data.automationEventItems[0].titleText, "章节生成中");
  page.clearBookGenerationEventPoll.call(context);
  delete global.wx;
});

test("book page supplements pending sources without force regeneration", async () => {
  const calls = [];
  global.wx = { showToast() {} };
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      enqueueMemoirBookChapterDraftGeneration: async (projectId, chapterId, data) => {
        calls.push({ projectId, chapterId, data });
        return sampleAutomationEvent({ id: "event-draft-supplement", chapter_id: chapterId });
      },
    },
  });
  const plan = samplePlan("confirmed");
  plan.chapters[0].pending_source_count = 1;
  const context = pageContext(page, {
    projectId: "project/1",
    chapters: mod.buildChapterViews(plan, {
      "chapter-1": sampleDraft(1, "draft", "第一版正文"),
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
  assert.equal(context.data.chapters[0].pendingSourceCount, 1);
  assert.equal(context.data.chapters[0].draftBusy, true);
  assert.equal(context.data.automationEventItems[0].titleText, "章节生成中");
  page.clearBookGenerationEventPoll.call(context);
  delete global.wx;
});

test("book page force regenerates when source removals also need cleanup", async () => {
  const calls = [];
  global.wx = { showToast() {} };
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      enqueueMemoirBookChapterDraftGeneration: async (projectId, chapterId, data) => {
        calls.push({ projectId, chapterId, data });
        return sampleAutomationEvent({ id: "event-draft-cleanup", chapter_id: chapterId });
      },
    },
  });
  const plan = samplePlan("confirmed");
  plan.chapters[0].pending_source_count = 1;
  plan.chapters[0].removed_source_count = 1;
  const context = pageContext(page, {
    projectId: "project/1",
    chapters: mod.buildChapterViews(plan, {
      "chapter-1": sampleDraft(1, "draft", "第一版正文"),
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
  assert.equal(context.data.chapters[0].draftBusy, true);
  assert.equal(context.data.chapters[0].draftChanged, false);
  page.clearBookGenerationEventPoll.call(context);
  delete global.wx;
});

test("book page blocks regeneration while draft edits are unsaved", async () => {
  const calls = [];
  const { mod, page } = loadBookPage({
    voiceApiExports: {
      enqueueMemoirBookChapterDraftGeneration: async () => {
        calls.push("called");
        return sampleAutomationEvent();
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
        || options.url.endsWith("/book-voice-assistant/tts")
      ) {
        options.success({ statusCode: 200, data: sampleAudioResponse(), cookies: [], header: {} });
        return;
      }
      if (options.url.endsWith("/book-voice-command/parse")) {
        options.success({ statusCode: 200, data: sampleBookVoiceCommandPreview(), cookies: [], header: {} });
        return;
      }
      if (options.url.endsWith("/book-voice-command/execute")) {
        options.success({
          statusCode: 200,
          data: sampleBookVoiceCommandExecuteResponse("rename_chapter", samplePlan("confirmed")),
          cookies: [],
          header: {},
        });
        return;
      }
      if (options.url.endsWith("/draft/jobs")) {
        options.success({ statusCode: 200, data: sampleAutomationEvent(), cookies: [], header: {} });
        return;
      }
      if (options.url.endsWith("/draft") || options.url.endsWith("/draft/confirm")) {
        options.success({ statusCode: 200, data: sampleDraft(), cookies: [], header: {} });
        return;
      }
      if (options.url.endsWith("/book-output/title-suggestions")) {
        options.success({ statusCode: 200, data: { suggestions: ["外婆慢慢说"] }, cookies: [], header: {} });
        return;
      }
      if (options.url.endsWith("/book-output") || options.url.endsWith("/book-output/generate")) {
        options.success({ statusCode: 200, data: sampleBookOutput(), cookies: [], header: {} });
        return;
      }
      if (options.url.endsWith("/book-output/generate/jobs")) {
        options.success({
          statusCode: 200,
          data: sampleAutomationEvent({
            event_type: "book_output_generated",
            chapter_id: null,
            summary_text: "全书已提交后台生成。",
          }),
          cookies: [],
          header: {},
        });
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
    createMemoirBookVoiceAssistantTTS,
    enqueueMemoirBookChapterDraftGeneration,
    enqueueMemoirBookOutputGeneration,
    generateMemoirBookChapterDraft,
    generateMemoirBookChapterTitle,
    generateMemoirBookOutput,
    generateMemoirBookPlan,
    getMemoirBookChapterDraft,
    getMemoirBookOutput,
    getMemoirBookPlan,
    getMemoirBookSourceCoverage,
    executeMemoirBookVoiceCommand,
    parseMemoirBookVoiceCommand,
    suggestMemoirBookOutputTitles,
    updateMemoirBookChapterTitle,
    updateMemoirBookOutput,
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
  await executeMemoirBookVoiceCommand("project/1", {
    intent: "rename_chapter",
    operation: { type: "rename_chapter", chapter_id: "chapter/1", new_title: "童年往事" },
  });
  await createMemoirBookVoiceAssistantTTS("project/1", { text: "我理解了，请确认。" });
  await getMemoirBookOutput("project/1");
  await updateMemoirBookOutput("project/1", { title: "外婆慢慢说", cover_style: "warm_gray" });
  await suggestMemoirBookOutputTitles("project/1", { count: 4 });
  await generateMemoirBookOutput("project/1", { force_regenerate: true });
  await createMemoirBookOutputTTS("project/1", {});
  await enqueueMemoirBookChapterDraftGeneration("project/1", "chapter/1", { force_regenerate: true });
  await enqueueMemoirBookOutputGeneration("project/1", { force_regenerate: true });

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
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-voice-command/execute",
  );
  assert.equal(apiRequests[14].method, "POST");
  assert.deepEqual(apiRequests[14].data, {
    intent: "rename_chapter",
    operation: { type: "rename_chapter", chapter_id: "chapter/1", new_title: "童年往事" },
  });
  assert.equal(
    apiRequests[15].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-voice-assistant/tts",
  );
  assert.equal(apiRequests[15].method, "POST");
  assert.deepEqual(apiRequests[15].data, { text: "我理解了，请确认。" });
  assert.equal(
    apiRequests[16].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output",
  );
  assert.equal(apiRequests[16].method, "GET");
  assert.equal(
    apiRequests[17].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output",
  );
  assert.equal(apiRequests[17].method, "PATCH");
  assert.deepEqual(apiRequests[17].data, { title: "外婆慢慢说", cover_style: "warm_gray" });
  assert.equal(
    apiRequests[18].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output/title-suggestions",
  );
  assert.equal(apiRequests[18].method, "POST");
  assert.deepEqual(apiRequests[18].data, { count: 4, ai_model: "doubao-seed-2-0-pro-260215" });
  assert.equal(
    apiRequests[19].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output/generate",
  );
  assert.equal(apiRequests[19].method, "POST");
  assert.deepEqual(apiRequests[19].data, {
    force_regenerate: true,
    ai_model: "doubao-seed-2-0-pro-260215",
  });
  assert.equal(apiRequests[19].timeout, 60000);
  assert.equal(
    apiRequests[20].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output/tts",
  );
  assert.equal(apiRequests[20].method, "POST");
  assert.equal(apiRequests[20].header.Cookie, "pawtale_session=abc");
  assert.equal(
    apiRequests[21].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/chapters/chapter%2F1/draft/jobs",
  );
  assert.equal(apiRequests[21].method, "POST");
  assert.deepEqual(apiRequests[21].data, {
    force_regenerate: true,
    ai_model: "doubao-seed-2-0-pro-260215",
  });
  assert.equal(
    apiRequests[22].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output/generate/jobs",
  );
  assert.equal(apiRequests[22].method, "POST");
  assert.deepEqual(apiRequests[22].data, {
    force_regenerate: true,
    ai_model: "doubao-seed-2-0-pro-260215",
  });
  delete global.wx;
});

test("book output export download uses encoded backend route and session cookie", async () => {
  const downloads = [];
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
    request(options) {
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
      }
    },
    downloadFile(options) {
      downloads.push(options);
      options.success({
        statusCode: 200,
        tempFilePath: "/tmp/book.pdf",
        header: {},
        errMsg: "downloadFile:ok",
      });
    },
  };
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
  const { downloadMemoirBookOutputExport } = require("../services/memoir-voice-api.ts");

  const path = await downloadMemoirBookOutputExport("project/1", "pdf", "version/1");

  assert.equal(path, "/tmp/book.pdf");
  const baseExportUrl = "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output/export";
  assert.equal(
    downloads[0].url,
    `${baseExportUrl}?format=pdf&layout=standard&show_source_appendix=true&version_id=version%2F1`,
  );
  assert.equal(downloads[0].header.Cookie, "pawtale_session=abc");

  await downloadMemoirBookOutputExport("project/1", "pdf", "version/1", "standard", false);

  assert.equal(
    downloads[1].url,
    `${baseExportUrl}?format=pdf&layout=standard&show_source_appendix=false&version_id=version%2F1`,
  );
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

test("book structure command API calls backend command route", async () => {
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
  const { applyMemoirBookPlanStructureCommand } = require("../services/memoir-voice-api.ts");

  await applyMemoirBookPlanStructureCommand("project/1", {
    type: "merge_chapters",
    chapter_id: "chapter/2",
    target_chapter_id: "chapter/1",
  });

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-plan/structure-commands",
  );
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, {
    type: "merge_chapters",
    chapter_id: "chapter/2",
    target_chapter_id: "chapter/1",
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
