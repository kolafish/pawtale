require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");
const { ApiError } = require("../services/request.ts");

function sampleTranscript(id, overrides = {}) {
  return {
    id,
    voice_session_id: "voice-1",
    project_id: "project-1",
    project_title: "我的回忆录",
    chat_message_id: `message-${id}`,
    chat_message_role: "user",
    audio_file_id: `audio-${id}`,
    audio_mime_type: "audio/pcm",
    audio_size_bytes: 1024,
    audio_duration_seconds: 32,
    transcript_version_id: `version-${id}`,
    question_message_id: `question-${id}`,
    question_text: "那时候家里谁照顾你？",
    question_audio_file_id: `question-audio-${id}`,
    transcript_raw: "爷爷退休后还在帮公司做事。",
    transcript_edited: "",
    display_text: "爷爷退休后还在帮公司做事。",
    summary_title: "爷爷退休后仍帮公司做事",
    summary_text: "讲述者提到爷爷退休后仍继续帮公司处理事务。",
    summary_source: "source_card",
    raw_char_count: 14,
    edited_char_count: 0,
    display_char_count: 14,
    start_ms: 0,
    end_ms: 32000,
    provider: "fake",
    session_status: "stopped",
    guidance_mode: "free_narration",
    interview_type: "ai_interview",
    current_guidance_module: null,
    is_included_in_book: false,
    included_chapters: [],
    created_at: "2026-07-01T07:38:00.000Z",
    session_created_at: "2026-07-01T07:00:00.000Z",
    ...overrides,
  };
}

function sampleSourceRef(id, quote = "爷爷退休后还在帮公司做事。") {
  return {
    voice_session_id: "voice-1",
    message_id: `message-${id}`,
    utterance_id: id,
    transcript_version_id: `version-${id}`,
    audio_file_id: `audio-${id}`,
    start_ms: 0,
    end_ms: 32000,
    quote,
  };
}

function sampleBookPlan() {
  return {
    id: "plan-1",
    project_id: "project-1",
    title: "章节规划",
    status: "confirmed",
    created_by: "ai",
    model: "fake",
    metadata_json: {},
    created_at: "2026-07-01T00:00:00.000Z",
    updated_at: "2026-07-01T00:00:00.000Z",
    chapters: [
      {
        id: "chapter-1",
        plan_id: "plan-1",
        project_id: "project-1",
        title: "退休后的日子",
        sort_order: 0,
        source_refs: [sampleSourceRef("utterance-included"), sampleSourceRef("utterance-other")],
        metadata_json: {},
        created_at: "2026-07-01T00:00:00.000Z",
        updated_at: "2026-07-01T00:00:00.000Z",
      },
      {
        id: "chapter-2",
        plan_id: "plan-1",
        project_id: "project-1",
        title: "家庭记忆",
        sort_order: 1,
        source_refs: [],
        metadata_json: {},
        created_at: "2026-07-01T00:00:00.000Z",
        updated_at: "2026-07-01T00:00:00.000Z",
      },
    ],
  };
}

function sampleShareVideoContext(utteranceId = "utterance-share", overrides = {}) {
  const firstText = "爷爷退休后还在帮公司做事。";
  const secondText = "后来大家都很信任他。";
  const transcriptText = `${firstText}${secondText}`;
  return {
    utterance_id: utteranceId,
    audio_file_id: `audio-${utteranceId}`,
    transcript_source: "edited",
    transcript_text: transcriptText,
    text_hash: "hash-1",
    duration_ms: 8800,
    alignment_strategy: "provider_words",
    alignment_confidence: 0.92,
    min_select_ms: 3000,
    max_select_ms: 90000,
    ai_background_enabled: true,
    default_background_source: "ai",
    default_background_style: "old_album",
    default_background_mode: "auto",
    background_sources: [
      {
        value: "ai",
        label: "AI生成",
        description: "根据文字自动生成回忆录背景图",
      },
      {
        value: "custom",
        label: "自定义图片",
        description: "上传自己的图片作为背景",
      },
      {
        value: "classic",
        label: "经典背景",
        description: "使用米白纸张质感背景",
      },
    ],
    background_styles: [
      {
        value: "old_album",
        label: "旧相册",
        description: "旧照片手工上色质感",
      },
      {
        value: "ink_paper",
        label: "淡水墨",
        description: "淡水墨旧纸质感",
      },
      {
        value: "storybook",
        label: "老故事书",
        description: "中国老连环画和旧书插图质感",
      },
    ],
    background_modes: [
      {
        value: "auto",
        label: "自动",
        description: "20 秒以上自动使用智能多图",
      },
      {
        value: "single",
        label: "单张背景",
        description: "整段视频使用一张背景图，生成更快",
      },
      {
        value: "smart_multi",
        label: "智能多图",
        description: "根据口述内容拆分多个画面",
      },
    ],
    segments: [
      {
        index: 0,
        text: firstText,
        start_char: 0,
        end_char: firstText.length,
        start_ms: 0,
        end_ms: 4200,
        confidence: 0.92,
        selectable: true,
      },
      {
        index: 1,
        text: secondText,
        start_char: firstText.length,
        end_char: transcriptText.length,
        start_ms: 4200,
        end_ms: 8800,
        confidence: 0.9,
        selectable: true,
      },
    ],
    ...overrides,
  };
}

function sampleShareVideoJob(overrides = {}) {
  return {
    id: "job-1",
    project_id: "project-1",
    utterance_id: "utterance-share",
    audio_file_id: "audio-utterance-share",
    status: "succeeded",
    selected_text: "爷爷退休后还在帮公司做事。后来大家都很信任他。",
    start_char: 0,
    end_char: 23,
    start_ms: 0,
    end_ms: 8800,
    duration_ms: 8800,
    background_source: "ai",
    background_style: "old_album",
    background_mode: "auto",
    background_effective_mode: "single",
    custom_background_asset_id: null,
    background_scene_count: 1,
    background_status: "ready",
    video_mime_type: "video/mp4",
    video_size_bytes: 65536,
    download_url: "/api/mobile/memoir/share-video/jobs/job-1/download",
    error_message: "",
    created_at: "2026-07-01T08:00:00.000Z",
    updated_at: "2026-07-01T08:00:02.000Z",
    completed_at: "2026-07-01T08:00:02.000Z",
    ...overrides,
  };
}

function loadTranscriptDetailPage({ voiceApiExports = {}, voiceSearchExports = {} } = {}) {
  mockModule("../services/memoir-voice-api.ts", {
    assignMemoirBookSources: async () => {
      throw new Error("assignMemoirBookSources was not mocked");
    },
    convertMemoirInterviewQuestionToAnswer: async () => {
      throw new Error("convertMemoirInterviewQuestionToAnswer was not mocked");
    },
    createMemoirTranscriptShareVideoJob: async () => {
      throw new Error("createMemoirTranscriptShareVideoJob was not mocked");
    },
    deleteMemoirVoiceTranscript: async () => {
      throw new Error("deleteMemoirVoiceTranscript was not mocked");
    },
    downloadMemoirShareVideoCustomBackgroundPreview: async () => {
      throw new Error("downloadMemoirShareVideoCustomBackgroundPreview was not mocked");
    },
    downloadMemoirShareVideoJob: async () => {
      throw new Error("downloadMemoirShareVideoJob was not mocked");
    },
    getMemoirBookPlan: async () => null,
    getMemoirShareVideoJob: async () => {
      throw new Error("getMemoirShareVideoJob was not mocked");
    },
    getMemoirTranscriptShareVideoContext: async () => {
      throw new Error("getMemoirTranscriptShareVideoContext was not mocked");
    },
    getMemoirVoiceTranscript: async () => {
      throw new Error("getMemoirVoiceTranscript was not mocked");
    },
    moveMemoirBookSource: async () => {
      throw new Error("moveMemoirBookSource was not mocked");
    },
    previewMemoirVoiceTranscriptVoiceEdit: async () => {
      throw new Error("previewMemoirVoiceTranscriptVoiceEdit was not mocked");
    },
    recoverMemoirVoiceTranscript: async () => {
      throw new Error("recoverMemoirVoiceTranscript was not mocked");
    },
    updateMemoirVoiceTranscript: async () => {
      throw new Error("updateMemoirVoiceTranscript was not mocked");
    },
    uploadMemoirShareVideoCustomBackground: async () => {
      throw new Error("uploadMemoirShareVideoCustomBackground was not mocked");
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
  return loadPageModule("../pages/transcript-detail/index.ts");
}

test("transcript detail page loads a single transcript and chapter options", async () => {
  const calls = [];
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => {
        calls.push(id);
        return sampleTranscript(id);
      },
      getMemoirBookPlan: async () => sampleBookPlan(),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-new" });

  assert.deepEqual(calls, ["utterance-new"]);
  assert.equal(context.data.item.titleText, "爷爷退休后仍帮公司做事");
  assert.equal(context.data.item.createdAtText, "07-01 15:38");
  assert.equal(context.data.item.audioText, "32 秒");
  assert.equal(context.data.item.recordMetaText, "07-01 15:38 · 32 秒");
  assert.equal(context.data.item.bookInclusionText, "尚未入章");
  assert.equal(context.data.item.questionTitleText, "小余刚才问了");
  assert.equal(context.data.item.questionCanExpand, false);
  assert.equal(context.data.questionExpanded, false);
  assert.equal(context.data.moreActionsExpanded, false);
  assert.deepEqual(context.data.item.chapterAssignOptions, [
    { label: "第 1 章 退休后的日子", value: "chapter-1" },
    { label: "第 2 章 家庭记忆", value: "chapter-2" },
  ]);
});

test("transcript detail page routes draft plan assignments to book confirmation", async () => {
  const assignments = [];
  const navigations = [];
  global.wx = {
    switchTab(options) {
      navigations.push(options);
    },
  };
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id),
      getMemoirBookPlan: async () => ({ ...sampleBookPlan(), status: "draft" }),
      assignMemoirBookSources: async (projectId, data) => {
        assignments.push({ projectId, data });
        return sampleBookPlan();
      },
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-new" });

  assert.equal(context.data.item.canAssignChapterSource, false);
  assert.equal(context.data.item.needsBookPlanConfirmation, true);
  assert.equal(context.data.item.bookInclusionText, "先确认章节结构，再归入章节");
  assert.deepEqual(context.data.item.chapterAssignOptions, []);

  await page.assignTranscriptChapter.call(context, { detail: { value: 0 } });
  page.openBook.call(context);

  assert.deepEqual(assignments, []);
  assert.deepEqual(navigations, [{ url: "/pages/book/index" }]);
  delete global.wx;
});

test("transcript detail page shows saved audio awaiting transcription", async () => {
  const recoverCalls = [];
  const toasts = [];
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id, {
        transcript_raw: "",
        transcript_edited: "",
        display_text: "",
        summary_title: "音频已保存，等待转写",
        summary_text: "这段录音已经保存，但还没有识别成文字。",
        summary_source: "pending_transcription",
        raw_char_count: 0,
        edited_char_count: 0,
        display_char_count: 0,
        transcription_status: "pending_transcription",
        can_recover_transcription: true,
      }),
      recoverMemoirVoiceTranscript: async (id) => {
        recoverCalls.push(id);
        return sampleTranscript(id, {
          transcript_raw: "爷爷退休后还在帮公司做事。",
          display_text: "爷爷退休后还在帮公司做事。",
          summary_title: "爷爷退休后仍帮公司做事",
          summary_text: "讲述者提到爷爷退休后还在帮公司做事。",
          summary_source: "source_card",
          raw_char_count: 14,
          display_char_count: 14,
          transcription_status: "ready",
          can_recover_transcription: false,
        });
      },
      getMemoirBookPlan: async () => sampleBookPlan(),
    },
  });
  const context = pageContext(page);
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };

  await page.onLoad.call(context, { id: "utterance-pending" });

  assert.equal(context.data.item.titleText, "音频已保存，等待转写");
  assert.equal(context.data.item.transcriptionNoticeText, "音频已保存，等待转写");
  assert.equal(context.data.item.displayCountText, "待转写");
  assert.equal(context.data.item.rawCountText, "待转写");
  assert.equal(context.data.item.bookInclusionText, "等待转写后再入章");
  assert.equal(context.data.item.bookStatusClass, "pending");
  assert.equal(context.data.item.canAssignChapterSource, false);
  assert.equal(context.data.item.canMoveChapterSource, false);
  assert.equal(context.data.item.canPlayAnswerAudio, true);
  assert.equal(context.data.item.canRecoverTranscription, true);
  assert.deepEqual(context.data.item.displaySegments, [
    { text: "这段录音已经保存，但还没有识别成文字。", highlight: false },
  ]);
  assert.deepEqual(context.data.item.rawSegments, [
    { text: "等待恢复转写后显示原始识别文字。", highlight: false },
  ]);

  await page.onLoad.call(context, { id: "utterance-pending", mode: "edit" });
  assert.equal(context.data.item.isEditing, false);

  await page.recoverTranscript.call(context);

  assert.deepEqual(recoverCalls, ["utterance-pending"]);
  assert.equal(context.data.item.canRecoverTranscription, false);
  assert.equal(context.data.item.titleText, "爷爷退休后仍帮公司做事");
  assert.equal(context.data.item.displayCountText, "14 字");
  assert.deepEqual(toasts, [{ title: "已恢复转写", icon: "success" }]);
  delete global.wx;
});

test("transcript detail page labels human interviewer questions", async () => {
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id, {
        interview_type: "human_interview",
        question_text: "你小时候家里有几口人？",
      }),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-human" });

  assert.equal(context.data.item.questionTitleText, "采访者刚才问了");
});

test("transcript detail page converts a human interviewer material item to an answer", async () => {
  const loadCalls = [];
  const convertCalls = [];
  const modals = [];
  const toasts = [];
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => {
        loadCalls.push(id);
        const converted = loadCalls.length > 1;
        return sampleTranscript(id, {
          interview_type: "human_interview",
          chat_message_id: "message-question",
          chat_message_role: converted ? "user" : "interviewer",
          question_message_id: null,
          question_text: "",
          question_audio_file_id: null,
          transcript_raw: "我们家那时候有六口人。",
          display_text: "我们家那时候有六口人。",
          summary_title: "我们家那时候有六口人",
          summary_text: "我们家那时候有六口人。",
          raw_char_count: 11,
          display_char_count: 11,
        });
      },
      convertMemoirInterviewQuestionToAnswer: async (projectId, messageId) => {
        convertCalls.push({ projectId, messageId });
      },
    },
  });
  const context = pageContext(page);
  global.wx = {
    showModal(options) {
      modals.push(options);
      options.success({ confirm: true });
    },
    showToast(options) {
      toasts.push(options);
    },
  };

  await page.onLoad.call(context, { id: "utterance-question" });

  assert.equal(context.data.item.canConvertQuestionToAnswer, true);
  page.toggleMoreActionsExpanded.call(context);
  assert.equal(context.data.moreActionsExpanded, true);

  await page.convertCurrentQuestionToAnswer.call(context);

  assert.deepEqual(convertCalls, [{ projectId: "project-1", messageId: "message-question" }]);
  assert.deepEqual(loadCalls, ["utterance-question", "utterance-question"]);
  assert.equal(modals[0].title, "改成回答？");
  assert.equal(modals[0].confirmText, "改成回答");
  assert.deepEqual(toasts, [{ title: "已改成回答", icon: "success" }]);
  assert.equal(context.data.item.chat_message_role, "user");
  assert.equal(context.data.item.canConvertQuestionToAnswer, false);
  assert.equal(context.data.moreActionsExpanded, false);
  delete global.wx;
});

test("transcript detail page uses content-first inclusion labels", async () => {
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id, {
        is_included_in_book: true,
        included_chapters: [
          { chapter_id: "chapter-2", title: "家庭记忆", sort_order: 1 },
          { chapter_id: "chapter-1", title: "退休后的日子", sort_order: 0 },
        ],
      }),
      getMemoirBookPlan: async () => sampleBookPlan(),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-included" });

  assert.equal(context.data.item.bookInclusionText, "已放入第 1 章：退休后的日子等 2 章");
  assert.equal(context.data.item.isIncludedInBook, true);
  assert.deepEqual(context.data.item.chapterMoveOptions, [
    { label: "第 2 章 家庭记忆", value: "chapter-2" },
  ]);
});

test("transcript detail page collapses long question context by default", async () => {
  const longQuestion = "刚才讲到爷爷八十岁还骑车上班，下雨天摔了一跤，那后来家里人是怎么照顾他的，他自己又怎么说这件事？当时有没有影响他继续上班，家里人有没有劝过他少骑车？";
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id, {
        question_text: longQuestion,
      }),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-long-question" });

  assert.equal(context.data.item.questionCanExpand, true);
  assert.equal(context.data.questionExpanded, false);

  page.toggleQuestionExpanded.call(context);

  assert.equal(context.data.questionExpanded, true);

  page.toggleQuestionExpanded.call(context);

  assert.equal(context.data.questionExpanded, false);
});

test("transcript detail page folds low-frequency actions", async () => {
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-more-actions" });

  assert.equal(context.data.moreActionsExpanded, false);

  page.toggleMoreActionsExpanded.call(context);

  assert.equal(context.data.moreActionsExpanded, true);
});

test("transcript detail page creates share video from selected corrected segments", async () => {
  const contextCalls = [];
  const createCalls = [];
  const pollCalls = [];
  const downloads = [];
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id, {
        transcript_edited: "爷爷退休后还在帮公司做事。后来大家都很信任他。",
        display_text: "爷爷退休后还在帮公司做事。后来大家都很信任他。",
        display_char_count: 23,
      }),
      getMemoirTranscriptShareVideoContext: async (id) => {
        contextCalls.push(id);
        return sampleShareVideoContext(id);
      },
      createMemoirTranscriptShareVideoJob: async (id, startChar, endChar, options) => {
        createCalls.push({ id, startChar, endChar, options });
        return sampleShareVideoJob({
          utterance_id: id,
          audio_file_id: `audio-${id}`,
          status: "pending",
          start_char: startChar,
          end_char: endChar,
          video_size_bytes: null,
          download_url: null,
          completed_at: null,
        });
      },
      getMemoirShareVideoJob: async (jobId) => {
        pollCalls.push(jobId);
        return sampleShareVideoJob();
      },
      downloadMemoirShareVideoJob: async (job) => {
        downloads.push(job.id);
        return "/tmp/share-video.mp4";
      },
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-share" });
  await page.toggleShareVideoPanel.call(context);

  assert.deepEqual(contextCalls, ["utterance-share"]);
  assert.equal(context.data.shareVideoPanelOpen, true);
  assert.equal(context.data.shareVideoSegments.length, 2);
  assert.equal(context.data.shareVideoSelectedCanGenerate, false);
  assert.equal(context.data.shareVideoBackgroundSource, "ai");
  assert.equal(context.data.shareVideoBackgroundSourceOptions.length, 3);
  assert.equal(context.data.shareVideoBackgroundUsesAi, true);
  assert.equal(context.data.shareVideoBackgroundStyle, "old_album");
  assert.equal(context.data.shareVideoBackgroundStyleOptions.length, 3);
  assert.equal(context.data.shareVideoBackgroundMode, "auto");
  assert.equal(context.data.shareVideoBackgroundModeOptions.length, 3);

  page.selectShareVideoSegment.call(context, { currentTarget: { dataset: { index: 0 } } });
  assert.equal(context.data.shareVideoSelectedText, "爷爷退休后还在帮公司做事。");
  assert.equal(context.data.shareVideoSelectedCanGenerate, true);

  page.selectShareVideoSegment.call(context, { currentTarget: { dataset: { index: 1 } } });
  assert.equal(context.data.shareVideoSelectedText, "爷爷退休后还在帮公司做事。后来大家都很信任他。");
  assert.equal(context.data.shareVideoSelectedStartChar, 0);
  assert.equal(context.data.shareVideoSelectedEndChar, 23);
  assert.equal(context.data.shareVideoSelectedMetaText, "已选 9 秒 · 23 字");

  const storageWrites = [];
  global.wx = {
    setStorageSync(key, value) {
      storageWrites.push({ key, value });
    },
  };
  page.selectShareVideoBackgroundStyle.call(context, {
    currentTarget: { dataset: { backgroundStyle: "storybook" } },
  });
  delete global.wx;
  assert.equal(context.data.shareVideoBackgroundStyle, "storybook");
  assert.equal(context.data.shareVideoBackgroundStyleLabel, "老故事书");
  assert.deepEqual(storageWrites, [
    {
      key: "pawtale_share_video_background_settings",
      value: { backgroundSource: "ai", backgroundStyle: "storybook", backgroundMode: "auto" },
    },
  ]);
  page.selectShareVideoBackgroundMode.call(context, {
    currentTarget: { dataset: { backgroundMode: "smart_multi" } },
  });
  assert.equal(context.data.shareVideoBackgroundMode, "smart_multi");
  assert.equal(context.data.shareVideoBackgroundModeLabel, "智能多图");

  await page.createShareVideo.call(context);

  assert.deepEqual(createCalls, [
    {
      id: "utterance-share",
      startChar: 0,
      endChar: 23,
      options: { backgroundSource: "ai", backgroundStyle: "storybook", backgroundMode: "smart_multi" },
    },
  ]);
  assert.equal(context.data.shareVideoJob.status, "pending");
  assert.equal(context.data.shareVideoPolling, true);
  assert.deepEqual(downloads, []);

  page.clearShareVideoPollTimer.call(context);
  await page.refreshShareVideoJob.call(context, "job-1", context.shareVideoPollToken);

  assert.deepEqual(pollCalls, ["job-1"]);
  assert.deepEqual(downloads, ["job-1"]);
  assert.equal(context.data.shareVideoJob.status, "succeeded");
  assert.equal(context.data.shareVideoStatusText, "视频已生成，可以预览");
  assert.equal(context.data.shareVideoPreviewPath, "/tmp/share-video.mp4");

  const shareCalls = [];
  global.wx = {
    shareFileMessage(options) {
      shareCalls.push(options);
      options.success({ errMsg: "shareFileMessage:ok" });
    },
  };
  await page.shareGeneratedVideo.call(context);
  delete global.wx;

  assert.deepEqual(downloads, ["job-1"]);
  assert.equal(shareCalls.length, 1);
  assert.equal(shareCalls[0].filePath, "/tmp/share-video.mp4");
  assert.equal(shareCalls[0].fileName, "爷爷退休后仍帮公司做事.mp4");
  assert.equal(context.data.shareVideoStatusText, "已打开微信分享");
});

test("transcript detail page uses backend background options as the configuration source", async () => {
  const baseContext = sampleShareVideoContext("utterance-share");
  const backendContext = {
    ...baseContext,
    background_styles: [
      ...baseContext.background_styles,
      {
        value: "documentary",
        label: "纪实影像",
        description: "由后端新增的纪实风格",
      },
    ],
    background_modes: [
      ...baseContext.background_modes,
      {
        value: "story_beats",
        label: "故事节拍",
        description: "由后端新增的分镜模式",
      },
    ],
  };
  const storageWrites = [];
  global.wx = {
    getStorageSync(key) {
      assert.equal(key, "pawtale_share_video_background_settings");
      return {
        backgroundSource: "ai",
        backgroundStyle: "documentary",
        backgroundMode: "story_beats",
      };
    },
    setStorageSync(key, value) {
      storageWrites.push({ key, value });
    },
  };
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id),
      getMemoirTranscriptShareVideoContext: async () => backendContext,
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-share" });
  await page.toggleShareVideoPanel.call(context);

  assert.equal(context.data.shareVideoBackgroundStyle, "documentary");
  assert.equal(context.data.shareVideoBackgroundStyleLabel, "纪实影像");
  assert.equal(context.data.shareVideoBackgroundMode, "story_beats");
  assert.equal(context.data.shareVideoBackgroundModeLabel, "故事节拍");
  assert.equal(context.data.shareVideoBackgroundStyleOptions.length, 4);
  assert.equal(context.data.shareVideoBackgroundModeOptions.length, 4);

  page.selectShareVideoBackgroundStyle.call(context, {
    currentTarget: { dataset: { backgroundStyle: "documentary" } },
  });

  assert.deepEqual(storageWrites, [
    {
      key: "pawtale_share_video_background_settings",
      value: {
        backgroundSource: "ai",
        backgroundStyle: "documentary",
        backgroundMode: "story_beats",
      },
    },
  ]);
  delete global.wx;
});

test("transcript detail page requires a custom background asset before creating video", async () => {
  const createCalls = [];
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id, {
        transcript_edited: "爷爷退休后还在帮公司做事。后来大家都很信任他。",
        display_text: "爷爷退休后还在帮公司做事。后来大家都很信任他。",
        display_char_count: 23,
      }),
      getMemoirTranscriptShareVideoContext: async (id) => sampleShareVideoContext(id),
      createMemoirTranscriptShareVideoJob: async (...args) => {
        createCalls.push(args);
        return sampleShareVideoJob();
      },
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-share" });
  await page.toggleShareVideoPanel.call(context);
  page.selectShareVideoSegment.call(context, { currentTarget: { dataset: { index: 0 } } });
  page.selectShareVideoBackgroundSource.call(context, {
    currentTarget: { dataset: { backgroundSource: "custom" } },
  });
  await page.createShareVideo.call(context);

  assert.deepEqual(createCalls, []);
  assert.equal(context.data.shareVideoBackgroundSource, "custom");
  assert.equal(context.data.shareVideoBackgroundUsesCustom, true);
  assert.equal(context.data.shareVideoError, "请先选择一张自定义背景图片");
  assert.equal(context.data.shareVideoStatusText, "请先选择背景图片");
});

test("transcript detail page uploads a custom background before creating share video", async () => {
  const uploadCalls = [];
  const previewCalls = [];
  const createCalls = [];
  const storageWrites = [];
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id, {
        transcript_edited: "爷爷退休后还在帮公司做事。后来大家都很信任他。",
        display_text: "爷爷退休后还在帮公司做事。后来大家都很信任他。",
        display_char_count: 23,
      }),
      getMemoirTranscriptShareVideoContext: async (id) => sampleShareVideoContext(id),
      uploadMemoirShareVideoCustomBackground: async (projectId, filePath) => {
        uploadCalls.push({ projectId, filePath });
        return {
          id: "asset-custom-1",
          project_id: projectId,
          status: "ready",
          preview_url: `/api/mobile/memoir/projects/${projectId}/image-assets/asset-custom-1/file`,
          mime_type: "image/jpeg",
          size_bytes: 4096,
          width: 1280,
          height: 720,
          processing_preset: "memoir_soft",
          original_mime_type: "image/png",
          original_size_bytes: 2048,
        };
      },
      downloadMemoirShareVideoCustomBackgroundPreview: async (asset) => {
        previewCalls.push(asset.id);
        return "/tmp/custom-background-processed.jpg";
      },
      createMemoirTranscriptShareVideoJob: async (id, startChar, endChar, options) => {
        createCalls.push({ id, startChar, endChar, options });
        return sampleShareVideoJob({
          id: "job-custom",
          utterance_id: id,
          audio_file_id: `audio-${id}`,
          status: "pending",
          background_source: "custom",
          custom_background_asset_id: "asset-custom-1",
          start_char: startChar,
          end_char: endChar,
          video_size_bytes: null,
          download_url: null,
          completed_at: null,
        });
      },
    },
  });
  const context = pageContext(page);
  global.wx = {
    chooseMedia(options) {
      options.success({
        errMsg: "chooseMedia:ok",
        type: "image",
        tempFiles: [{ tempFilePath: "/tmp/family-photo.png", size: 2048, fileType: "image" }],
      });
    },
    setStorageSync(key, value) {
      storageWrites.push({ key, value });
    },
  };

  await page.onLoad.call(context, { id: "utterance-share" });
  await page.toggleShareVideoPanel.call(context);
  await page.chooseShareVideoCustomBackground.call(context);

  assert.deepEqual(uploadCalls, [{ projectId: "project-1", filePath: "/tmp/family-photo.png" }]);
  assert.deepEqual(previewCalls, ["asset-custom-1"]);
  assert.equal(context.data.shareVideoBackgroundSource, "custom");
  assert.equal(context.data.shareVideoBackgroundUsesCustom, true);
  assert.equal(context.data.shareVideoCustomBackgroundUploading, false);
  assert.equal(context.data.shareVideoCustomBackgroundAssetId, "asset-custom-1");
  assert.equal(context.data.shareVideoCustomBackgroundPreviewPath, "/tmp/custom-background-processed.jpg");
  assert.equal(context.data.shareVideoCustomBackgroundStatusText, "背景图片已处理，会用于本次视频");

  page.selectShareVideoSegment.call(context, { currentTarget: { dataset: { index: 0 } } });
  await page.createShareVideo.call(context);
  page.clearShareVideoPollTimer.call(context);
  delete global.wx;

  assert.deepEqual(createCalls, [
    {
      id: "utterance-share",
      startChar: 0,
      endChar: 13,
      options: { backgroundSource: "custom", customBackgroundAssetId: "asset-custom-1" },
    },
  ]);
  assert.deepEqual(storageWrites[storageWrites.length - 1], {
    key: "pawtale_share_video_background_settings",
    value: { backgroundSource: "custom", backgroundStyle: "old_album", backgroundMode: "auto" },
  });
});

test("transcript detail page creates classic background share video without image asset", async () => {
  const createCalls = [];
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id, {
        transcript_edited: "爷爷退休后还在帮公司做事。",
        display_text: "爷爷退休后还在帮公司做事。",
        display_char_count: 14,
      }),
      getMemoirTranscriptShareVideoContext: async (id) => sampleShareVideoContext(id),
      createMemoirTranscriptShareVideoJob: async (id, startChar, endChar, options) => {
        createCalls.push({ id, startChar, endChar, options });
        return sampleShareVideoJob({
          utterance_id: id,
          audio_file_id: `audio-${id}`,
          status: "pending",
          background_source: "classic",
          background_style: "old_album",
          background_mode: "single",
          start_char: startChar,
          end_char: endChar,
          video_size_bytes: null,
          download_url: null,
          completed_at: null,
        });
      },
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-share" });
  await page.toggleShareVideoPanel.call(context);
  page.selectShareVideoSegment.call(context, { currentTarget: { dataset: { index: 0 } } });
  page.selectShareVideoBackgroundSource.call(context, {
    currentTarget: { dataset: { backgroundSource: "classic" } },
  });
  await page.createShareVideo.call(context);
  page.clearShareVideoPollTimer.call(context);

  assert.equal(context.data.shareVideoBackgroundSource, "classic");
  assert.equal(context.data.shareVideoBackgroundUsesAi, false);
  assert.equal(context.data.shareVideoBackgroundUsesCustom, false);
  assert.deepEqual(createCalls, [
    {
      id: "utterance-share",
      startChar: 0,
      endChar: 13,
      options: { backgroundSource: "classic" },
    },
  ]);
});

test("transcript detail page retries transient share video status refresh", async () => {
  const pollCalls = [];
  const downloads = [];
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id),
      getMemoirShareVideoJob: async (jobId) => {
        pollCalls.push(jobId);
        if (pollCalls.length === 1) {
          throw new ApiError(0, "request:fail timeout", {});
        }
        return sampleShareVideoJob();
      },
      downloadMemoirShareVideoJob: async (job) => {
        downloads.push(job.id);
        return "/tmp/share-video.mp4";
      },
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-share-poll" });
  context.shareVideoPollToken = 1;

  await page.refreshShareVideoJob.call(context, "job-1", 1);

  assert.deepEqual(pollCalls, ["job-1"]);
  assert.equal(context.data.shareVideoPolling, true);
  assert.equal(context.data.shareVideoStatusText, "视频状态刷新中断，正在重试");
  assert.deepEqual(downloads, []);

  page.clearShareVideoPollTimer.call(context);
  await page.refreshShareVideoJob.call(context, "job-1", 1);

  assert.deepEqual(pollCalls, ["job-1", "job-1"]);
  assert.deepEqual(downloads, ["job-1"]);
  assert.equal(context.data.shareVideoJob.status, "succeeded");
  assert.equal(context.data.shareVideoStatusText, "视频已生成，可以预览");
  assert.equal(context.data.shareVideoPreviewPath, "/tmp/share-video.mp4");
});

test("transcript detail page plays question and answer audio separately", async () => {
  const played = [];
  let activeToken = 0;
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id),
    },
  });
  const context = pageContext(page, { utteranceId: "utterance-audio" });
  context.audioPlayback = {
    nextToken() {
      activeToken += 1;
      return activeToken;
    },
    isCurrentToken(token) {
      return token === activeToken;
    },
    stop() {
      activeToken += 1;
    },
    async playFile(audioFileId) {
      played.push(audioFileId);
    },
  };

  await page.loadTranscript.call(context);

  assert.equal(context.data.item.canPlayAnswerAudio, true);
  assert.equal(context.data.item.canPlayQuestionAudio, true);

  await page.playAnswerAudio.call(context);
  assert.deepEqual(played, ["audio-utterance-audio"]);
  assert.equal(context.data.playing, false);
  assert.equal(context.data.playingAudioKind, "");

  await page.playQuestionAudio.call(context);
  assert.deepEqual(played, ["audio-utterance-audio", "question-audio-utterance-audio"]);
  assert.equal(context.data.playing, false);
  assert.equal(context.data.playingAudioKind, "");
});

test("transcript detail answer playback shows progress and supports pause", async () => {
  const played = [];
  let activeToken = 0;
  let activeAudio = null;
  let finishPlayback = null;
  const audio = {
    currentTime: 0,
    duration: 32,
    pauseCalls: 0,
    playCalls: 0,
    play() {
      this.playCalls += 1;
    },
    pause() {
      this.pauseCalls += 1;
    },
  };
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id, {
        audio_duration_seconds: 32,
      }),
    },
  });
  const context = pageContext(page, { utteranceId: "utterance-progress" });
  context.audioPlayback = {
    nextToken() {
      activeToken += 1;
      return activeToken;
    },
    isCurrentToken(token) {
      return token === activeToken;
    },
    currentAudio() {
      return activeAudio;
    },
    stop() {
      activeToken += 1;
      if (finishPlayback) {
        finishPlayback();
      }
    },
    async playFile(audioFileId, _token, options) {
      played.push(audioFileId);
      activeAudio = audio;
      options.onPlay?.(audio);
      await new Promise((resolve) => {
        finishPlayback = () => {
          options.onEnded?.(audio);
          activeAudio = null;
          resolve();
        };
      });
    },
  };

  await page.loadTranscript.call(context);
  const playbackPromise = page.playAnswerAudio.call(context);
  await Promise.resolve();

  assert.deepEqual(played, ["audio-utterance-progress"]);
  assert.equal(context.data.answerPlaybackVisible, true);
  assert.equal(context.data.answerPlaybackStatusText, "正在播放");
  assert.equal(context.data.answerPlaybackCurrentText, "0:00");
  assert.equal(context.data.answerPlaybackDurationText, "0:32");
  assert.equal(context.data.answerPlaybackProgressStyle, "width: 0%;");
  assert.equal(context.data.answerPlaybackCanTogglePause, true);

  audio.currentTime = 8;
  context.updateAnswerPlaybackProgress(audio.currentTime, audio.duration, "正在播放");
  assert.equal(context.data.answerPlaybackCurrentText, "0:08");
  assert.equal(context.data.answerPlaybackProgressStyle, "width: 25%;");

  page.toggleAnswerPlaybackPause.call(context);
  assert.equal(audio.pauseCalls, 1);
  assert.equal(context.data.answerPlaybackPaused, true);
  assert.equal(context.data.answerPlaybackStatusText, "已暂停");

  page.toggleAnswerPlaybackPause.call(context);
  assert.equal(audio.playCalls, 1);
  assert.equal(context.data.answerPlaybackPaused, false);
  assert.equal(context.data.answerPlaybackStatusText, "正在播放");

  finishPlayback();
  await playbackPromise;
  assert.equal(context.data.answerPlaybackStatusText, "播放完成");
  assert.equal(context.data.answerPlaybackProgressStyle, "width: 100%;");
  assert.equal(context.data.answerPlaybackCanTogglePause, false);
  assert.equal(context.data.answerPlaybackCanStop, true);
  assert.equal(context.data.answerPlaybackStopText, "收起");

  page.stopPlayback.call(context);
  assert.equal(context.data.answerPlaybackVisible, false);
});

test("transcript detail page can open directly in edit mode", async () => {
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id),
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { id: "utterance-edit", mode: "edit" });

  assert.equal(context.data.item.isEditing, true);
  assert.equal(context.data.item.editingText, "爷爷退休后还在帮公司做事。");
});

test("transcript detail page saves edited display text", async () => {
  const updates = [];
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id),
      updateMemoirVoiceTranscript: async (id, content) => {
        updates.push({ id, content });
        return sampleTranscript(id, {
          transcript_edited: content,
          display_text: content,
          display_char_count: content.length,
          summary_title: "爷爷退休后帮公司处理事务",
        });
      },
    },
  });
  const context = pageContext(page, { utteranceId: "utterance-edit" });

  await page.loadTranscript.call(context);
  page.beginEdit.call(context);
  page.changeText.call(context, { detail: { value: "爷爷退休后还帮公司处理事情。" } });
  await page.saveTranscript.call(context);

  assert.deepEqual(updates, [
    { id: "utterance-edit", content: "爷爷退休后还帮公司处理事情。" },
  ]);
  assert.equal(context.data.item.display_text, "爷爷退休后还帮公司处理事情。");
  assert.equal(context.data.item.isEditing, false);
  assert.deepEqual(toasts, [{ title: "已保存", icon: "success" }]);
  delete global.wx;
});

test("transcript detail page deletes after confirmation and navigates back", async () => {
  const deleted = [];
  const toasts = [];
  const navigations = [];
  const modals = [];
  global.wx = {
    showModal(options) {
      modals.push(options);
      options.success({ confirm: true });
    },
    showToast(options) {
      toasts.push(options);
    },
    navigateBack(options) {
      navigations.push(options);
    },
  };
  const { page } = loadTranscriptDetailPage({
    voiceApiExports: {
      getMemoirVoiceTranscript: async (id) => sampleTranscript(id),
      deleteMemoirVoiceTranscript: async (id) => {
        deleted.push(id);
      },
    },
  });
  const context = pageContext(page, { utteranceId: "utterance-delete" });

  await page.loadTranscript.call(context);
  await page.deleteTranscript.call(context);

  assert.deepEqual(deleted, ["utterance-delete"]);
  assert.match(modals[0].content, /这条原始讲述会从素材库移除/);
  assert.match(modals[0].content, /相关章节会标记为需重新确认/);
  assert.match(modals[0].content, /已生成正文不会自动改写/);
  assert.deepEqual(toasts, [{ title: "已删除", icon: "success" }]);
  assert.deepEqual(navigations, [{ delta: 1 }]);
  delete global.wx;
});
