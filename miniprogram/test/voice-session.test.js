require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  flushAsyncWork,
  mockMemoirApiModules,
  clearModule,
  loadComponentModule,
  componentContext,
  mockModule,
  unmockedAsync,
} = require("./page-harness");

function loadVoiceChatPage({
  streamingExports = {},
  voiceApiExports = {},
  memoirCacheExports = {},
  voiceAuthorizationExports = {},
  wechatPrivacyExports = {},
  voiceChatAutoListenExports = {},
  assistantTurnStreamExports = {},
  streamingSpeechPlaybackExports = {},
  ttsTextProgressExports = {},
  accessibilityExports = {},
} = {}) {
  global.wx = { setKeepScreenOn() {}, ...global.wx };
  mockModule("../services/accessibility-settings.ts", {
    readAccessibilitySettings: () => ({
      largeTextEnabled: false,
      autoBargeInEnabled: true,
    }),
    ...accessibilityExports,
  });
  mockModule("../services/memoir-streaming-asr.ts", {
    classifyVoiceClientConnectionError: () => ({ kind: "unknown" }),
    MemoirStreamingAsrClient: streamingExports.MemoirStreamingAsrClient || class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}

      async connect() {}
      close() {}
      startRecording() {
        throw new Error("startRecording should not be called without authorization");
      }
      stopRecording() {}
      configureAutoStop() {}
      async finishSession() {}
    },
    ...streamingExports,
  });
  clearModule("../services/streaming-voice-controller.ts");
  mockMemoirApiModules({
    createMemoirAssistantTTS: unmockedAsync("createMemoirAssistantTTS"),
    createMemoirVoiceNoAnswerPrompt: unmockedAsync("createMemoirVoiceNoAnswerPrompt"),
    downloadMemoirVoiceAudio: async () => "",
    getMemoirVoiceSession: async () => ({
      session: {
        status: "created",
        guidance_mode: "free_narration",
        current_guidance_module: "",
        error_message: "",
      },
      messages: [],
      utterances: [],
    }),
    listMemoirCompletionIssues: unmockedAsync("listMemoirCompletionIssues"),
    closeMemoirCompletionIssue: unmockedAsync("closeMemoirCompletionIssue"),
    markMemoirAutomationEventsVoicePlayed: async () => ({ updated_count: 0 }),
    listMemoirAutomationEvents: async () => ({ items: [], total: 0, limit: 8 }),
    getMemoirDraftProgress: async () => ({ status: "ready", title: "可读草稿已更新", detail: "全书检查已完成" }),
    stopMemoirVoiceSession: unmockedAsync("stopMemoirVoiceSession"),
    ...voiceApiExports,
  });
  clearModule("../services/story-growth-notice.ts");
  clearModule("../components/voice-session/voice-chat-completion-issue-runtime.ts");
  clearModule("../services/audio-playback.ts");
  mockModule("../services/tts-text-progress.ts", {
    splitTextByAudioPlaybackProgress: () => ({
      spokenText: "",
      currentText: "",
      remainingText: "",
      progressPercent: 0,
    }),
    ...ttsTextProgressExports,
  });
  clearModule("../services/voice-assistant-tts-progress.ts");
  mockModule("../services/tts-settings.ts", {
    readChatTTSSettings: () => ({ voice: "", speedRatio: 1 }),
  });
  mockModule("../services/voice-chat-auto-listen.ts", {
    canAutoStartListeningAfterTTS: () => false,
    ...voiceChatAutoListenExports,
  });
  mockModule("../services/voice-chat-state.ts", {
    assistantPanelCopy: () => ({
      assistantLoadingText: "小余正在准备第一句话",
      assistantEmptyText: "小余会先问一句，帮您开始讲。",
    }),
    assistantTranscriptState: (lines, options = {}) => ({
      transcriptLines: lines,
      latestTranscriptText: lines.length ? lines[lines.length - 1].text : "",
      assistantPromptText: options.assistantPromptText || "",
      assistantLoadingText: "小余正在准备第一句话",
      assistantEmptyText: "小余会先问一句，帮您开始讲。",
      assistantExpectsAnswer: true,
      followUpMessageId: options.followUpMessageId || "",
      followUpAudioFileId: options.followUpAudioFileId || "",
      followUpTTSStateText: options.followUpTTSStateText || "",
      ttsAlignment: options.ttsAlignment || [],
      totalTranscriptCharCount: 0,
      canRequestFollowUp: options.canRequestFollowUp ?? false,
    }),
    canRequestFollowUp: () => false,
    countTextChars: (value) => value.length,
    coveredFollowUpSourceMessageId: () => "",
    errorKindText: () => "",
    followUpSourceMessageId: () => "",
    formatDuration: () => "00:00",
    gatewayStateText: () => "识别未同步",
    latestAssistantMessageId: () => "",
    latestPersistedAssistantMessageId: (lines) => {
      const latest = lines[lines.length - 1];
      return latest?.role === "assistant" ? latest.message_id || "" : "";
    },
    latestTranscriptText: () => "",
    statusText: (status) => status,
    totalTranscriptChars: () => 0,
    ttsAlignmentFromAssistantLine: () => [],
    ttsAlignmentFromMetadata: () => [],
    upsertTranscriptLine: (lines, nextLine) => {
      const matchIndex = lines.findIndex((line) => Boolean(
        (nextLine.message_id && line.message_id === nextLine.message_id)
        || (nextLine.utterance_id && line.utterance_id === nextLine.utterance_id)
        || (nextLine.id && line.id === nextLine.id),
      ));
      if (matchIndex < 0) {
        return [...lines, nextLine];
      }
      return lines.map((line, index) => (index === matchIndex ? nextLine : line));
    },
    visibleAssistantLine: () => null,
  });
  mockModule("../stores/memoir-cache.ts", {
    clearMemoirVoiceSessionPendingClose: () => {},
    readMemoirVoiceSessionSnapshot: () => ({ hasSession: false }),
    transcriptLinesFromVoiceSessionDetail: () => [],
    writeMemoirVoiceSessionPendingClose: () => {},
    writeMemoirVoiceSessionSnapshot: () => {},
    ...memoirCacheExports,
  });
  mockModule("../services/voice-authorization.ts", {
    ensureVoiceRecordingAuthorization: async () => true,
    isRecorderPermissionDeniedError: () => false,
    openRecorderPermissionSettings: async () => true,
    readVoiceAuthorizationConsent: () => false,
    saveVoiceAuthorizationConsent: (agreed = true) => ({ agreed, version: "test", agreedAt: "now" }),
    syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
    ...voiceAuthorizationExports,
  });
  mockModule("../services/wechat-privacy.ts", {
    ensureRecorderPrivacyAuthorization: async () => {},
    isRecorderPermissionDeniedMessage: (message = "") => message.includes("麦克风权限"),
    wechatPrivacyErrorMessage: (message) => message || "微信隐私授权失败",
    ...wechatPrivacyExports,
  });
  mockModule("../services/voice-assistant-turn-stream.ts", {
    VoiceAssistantTurnStreamClient: assistantTurnStreamExports.VoiceAssistantTurnStreamClient || class {
      async start() {
        throw new Error("VoiceAssistantTurnStreamClient.start was not mocked");
      }
      cancel() {}
      close() {}
    },
    ...assistantTurnStreamExports,
  });
  mockModule("../services/streaming-speech-playback.ts", {
    StreamingSpeechPlaybackController: streamingSpeechPlaybackExports.StreamingSpeechPlaybackController || class {
      async appendFrame() {}
      async waitForDrain() {}
      stop() {}
      async destroy() {}
    },
    ...streamingSpeechPlaybackExports,
  });
  clearModule("../services/voice-assistant-turn-runtime.ts");
  clearModule("../services/voice-assistant-turn-queue.ts");
  clearModule("../components/voice-session/voice-chat-assistant-playback.ts");
  clearModule("../components/voice-session/voice-chat-recording-runtime.ts");

  clearModule("../components/voice-session/voice-session-runtime.ts");
  const { createVoiceSessionRuntime } = require("../components/voice-session/voice-session-runtime.ts");
  const component = loadComponentModule("../components/voice-session/index.ts").component;
  return { ...component.methods, ...createVoiceSessionRuntime(), data: component.data,
    definition: component, properties: { entry: null, embedded: false }, events: [], runtime() { return this; },
    triggerEvent(name, detail) { this.events.push({ name, detail }); } };
}

function installAutoEndingAudioContext() {
  let endedCallback = () => {};
  global.wx = {
    createInnerAudioContext() {
      return {
        src: "",
        duration: 1,
        currentTime: 1,
        onPlay() {},
        onTimeUpdate() {},
        onEnded(callback) {
          endedCallback = callback;
        },
        onStop() {},
        onError() {},
        play() {
          setImmediate(() => endedCallback());
        },
        stop() {},
        destroy() {},
      };
    },
  };
}

test("microphone settings recovers authorization without replacing a healthy connection", async () => {
  let authorized = true;
  let settingsError = null;
  const page = loadVoiceChatPage({
    voiceAuthorizationExports: {
      openRecorderPermissionSettings: async () => {
        if (settingsError) throw settingsError;
        return authorized;
      },
    },
  });
  for (const hasClient of [true, false]) {
    let connectCalls = 0;
    const context = {
      ...page,
      data: { ...page.data, status: "error", error: "没有麦克风权限" },
      voiceStreaming: { hasClient: () => hasClient },
      async connectGateway() { connectCalls += 1; },
      setData(patch) { this.data = { ...this.data, ...patch }; },
    };
    await page.openRecorderSettings.call(context);
    assert.equal(connectCalls, hasClient ? 0 : 1);
    assert.equal(context.data.error, "");
    assert.equal(context.data.recorderSettingsRequired, false);
    assert.equal(context.data.status, hasClient ? "ready" : "idle");

    context.data.error = "没有麦克风权限";
    authorized = false;
    await page.openRecorderSettings.call(context);
    assert.equal(context.data.recorderSettingsRequired, true);
    assert.equal(connectCalls, hasClient ? 0 : 1);

    settingsError = new Error("openSetting:fail");
    await page.openRecorderSettings.call(context);
    assert.equal(context.data.recorderSettingsRequired, true, "设置打开失败仍允许重试");
    assert.equal(context.data.recoveryNotice, "openSetting:fail");
    settingsError = null;
    authorized = true;
    context.disposed = true;
    const before = context.data;
    await page.openRecorderSettings.call(context);
    assert.equal(context.data, before, "已离开的页面不更新或重连");
  }
});

test("voice chat blocks recording before voice authorization consent", () => {
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options);
    },
  };
  const page = loadVoiceChatPage();
  let connectCalls = 0;
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: false,
      status: "idle",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    connectGateway() {
      connectCalls += 1;
    },
  };

  const started = page.startRecording.call(context, true);

  assert.equal(started, false);
  assert.equal(connectCalls, 0);
  assert.match(context.data.error, /语音采集与处理授权/);
  assert.equal(context.data.autoListenStateText, "自动收音已暂停，等待语音授权");
  assert.equal(toasts[0].title, "请先同意语音授权");
});

test("voice chat ignores the microphone while the saved narration is being organized", () => {
  const page = loadVoiceChatPage();
  let authorizationChecks = 0;
  let recordingStarts = 0;
  const context = {
    ...page,
    data: {
      ...page.data,
      micButtonProcessing: true,
      voiceAuthorizationAgreed: true,
    },
    ensureVoiceAuthorization() {
      authorizationChecks += 1;
      return true;
    },
    startRecording() {
      recordingStarts += 1;
    },
  };

  page.toggleRecording.call(context);

  assert.equal(authorizationChecks, 0);
  assert.equal(recordingStarts, 0);
});

test("closing a completion issue keeps save controls available while it persists", async () => {
  let releaseClose;
  const closeStarted = new Promise((resolve) => {
    releaseClose = resolve;
  });
  const page = loadVoiceChatPage({
    voiceApiExports: {
      closeMemoirCompletionIssue: async () => {
        await closeStarted;
        return {
          id: "issue-1",
          project_id: "project-1",
          question: "第一次离家后发生了什么？",
          status: "closed",
          outcome: "deferred",
        };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      projectId: "project-1",
      sessionGoal: "book_completion",
      currentCompletionIssueId: "issue-1",
      voiceAuthorizationAgreed: true,
      completionIssue: {
        id: "issue-1",
        question: "第一次离家后发生了什么？",
        status: "in_progress",
      },
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  const request = page.closeCompletionIssue.call(context, "deferred");
  assert.equal(context.data.completionIssueAction, "deferred");
  assert.equal(context.data.completionIssueControlsDisabled, true);
  assert.equal(context.data.micButtonDisabled, false);
  assert.equal(context.data.finishButtonText, "保存离开");

  releaseClose();
  await request;
  assert.equal(context.data.completionIssue.status, "closed");
  assert.equal(context.data.completionIssue.outcome, "deferred");
  assert.equal(context.data.completionIssueAction, "");
});

test("book completion return stops the backend session when websocket is absent", async () => {
  const stoppedSessions = [];
  const navigations = [];
  global.wx = {
    navigateBack(options) {
      navigations.push(options);
    },
  };
  const page = loadVoiceChatPage({
    voiceApiExports: {
      stopMemoirVoiceSession: async (voiceSessionId) => {
        stoppedSessions.push(voiceSessionId);
        return { session: { id: voiceSessionId, status: "stopped" } };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      sessionGoal: "book_completion",
      completionIssue: {
        id: "issue-1",
        question: "请按顺序讲讲这段经历。",
        status: "closed",
        outcome: "answered",
      },
    },
    refreshVoiceSessionSnapshot: async () => ({ session: { status: "stopped" } }),
    recordingStartGeneration: 0,
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},
      hasClient: () => false,
    },
    stopCompletionIssueRefresh() {},
    cancelFollowUpTTS() {},
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.finishCompletionRound.call(context);

  assert.deepEqual(stoppedSessions, ["voice-1"]);
  assert.deepEqual(navigations, []);
  assert.deepEqual(context.events, [{ name: "exit", detail: { destination: "completion", voiceSessionId: "voice-1" } }]);
  assert.equal(context.data.finishingSession, false);
});

test("book completion return stays inactive while issue controls are disabled", async () => {
  let stopCalls = 0;
  const page = loadVoiceChatPage({
    voiceApiExports: {
      stopMemoirVoiceSession: async () => {
        stopCalls += 1;
        return { session: { id: "voice-1", status: "stopped" } };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      completionIssueControlsDisabled: true,
      voiceSessionId: "voice-1",
    },
  };

  await page.finishCompletionRound.call(context);

  assert.equal(stopCalls, 0);
});

test("human interview record action starts the selected speaker in one tap", async () => {
  const startOptions = [];
  const page = loadVoiceChatPage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
      syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      interviewType: "human_interview",
      voiceAuthorizationAgreed: true,
      status: "ready",
      speakerRole: "narrator",
    },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},
      hasClient: () => true,
      startRecording(options) {
        startOptions.push(options);
        return true;
      },
      stopRecording() {},
      close() {},
    },
    async syncVoiceAuthorizationForSession() {
      return true;
    },
    async stopAssistantBargeInMonitor() {},
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.startRecordingAsSpeaker.call(context, {
    currentTarget: { dataset: { role: "interviewer" } },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(context.data.speakerRole, "interviewer");
  assert.equal(context.data.recording, true);
  assert.equal(context.data.primaryActionText, "暂停收音");
  assert.deepEqual(startOptions, [{ noVoiceActivityTimeoutMs: 0, speakerRole: "interviewer" }]);
});

test("human interview record action reconnects and starts the selected speaker without a second tap", async () => {
  let connected = false;
  let connectCalls = 0;
  const startOptions = [];
  const page = loadVoiceChatPage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
      syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      interviewType: "human_interview",
      voiceAuthorizationAgreed: true,
      status: "closed",
      speakerRole: "narrator",
    },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},
      hasClient: () => connected,
      async connect(options) {
        connectCalls += 1;
        connected = true;
        options.callbacks.onStatus("ready");
      },
      startRecording(options) {
        startOptions.push(options);
        return true;
      },
      stopRecording() {},
      close() {},
    },
    async syncVoiceAuthorizationForSession() {
      return true;
    },
    async stopAssistantBargeInMonitor() {},
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.startRecordingAsSpeaker.call(context, {
    currentTarget: { dataset: { role: "interviewer" } },
  });
  assert.equal(context.data.recordingStarting, true);
  assert.equal(context.data.showHumanRecordActions, false);
  await context.recordingStartPromise;

  assert.equal(connectCalls, 1);
  assert.equal(context.data.speakerRole, "interviewer");
  assert.equal(context.data.recording, true);
  assert.equal(context.data.recordingStarting, false);
  assert.equal(context.data.status, "ready");
  assert.deepEqual(startOptions, [{ noVoiceActivityTimeoutMs: 0, speakerRole: "interviewer" }]);
});

test("voice chat does not connect a stopped voice session", async () => {
  let connectCalls = 0;
  const screenRequests = [];
  global.wx = { setKeepScreenOn: ({ keepScreenOn }) => screenRequests.push(keepScreenOn) };
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}

        async connect() {
          connectCalls += 1;
        }
        close() {}
      },
    },
    voiceApiExports: {
      getMemoirVoiceSession: async () => ({
        session: {
          status: "stopped",
          guidance_mode: "guided_interview",
          current_guidance_module: "person",
          error_message: "",
        },
        messages: [],
        utterances: [],
      }),
    },
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
    },
  });
  const context = {
    ...page,
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.onLoad.call(context, {
    voiceSessionId: "voice-stopped",
    mode: "guided_interview",
    module: "person",
  });

  assert.equal(connectCalls, 0);
  assert.equal(context.data.status, "closed");
  assert.equal(context.data.statusText, "closed");
  assert.equal(context.data.primaryActionText, "已保存");
  assert.equal(context.data.persistenceStateText, "已保存");
  assert.equal(screenRequests.at(-1), false);
  page.onShow.call(context);
  assert.equal(screenRequests.at(-1), false, "returning to a stopped session cannot keep the screen awake");
});

test("voice chat starts the opening assistant turn after a normal session load", async () => {
  let connectCalls = 0;
  const followUpCalls = [];
  const page = loadVoiceChatPage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
    },
  });
  const context = {
    ...page,
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    async connectGateway() {
      connectCalls += 1;
      this.data = { ...this.data, status: "ready" };
    },
    generateFollowUp(regenerate) {
      followUpCalls.push(regenerate);
      this.data = { ...this.data, followUpLoading: true };
      return Promise.resolve();
    },
  };

  await page.onLoad.call(context, {
    voiceSessionId: "voice-1",
    mode: "free_narration",
  });

  assert.deepEqual(followUpCalls, [false]);
  assert.equal(connectCalls, 1);
  assert.equal(context.data.status, "ready");
});

test("book completion shows the persisted issue without generating another opening question", () => {
  const page = loadVoiceChatPage();
  let followUpCalls = 0;
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      sessionGoal: "book_completion",
      currentCompletionIssueId: "issue-1",
      completionIssue: {
        id: "issue-1",
        status: "in_progress",
        question: "您希望保留现在的书名，还是调整全书定位？",
      },
    },
    generateFollowUp() {
      followUpCalls += 1;
    },
  };

  page.maybeGenerateOpeningFollowUp.call(context);

  assert.equal(followUpCalls, 0);
});

test("book completion never sends its persisted question through the assistant model", async () => {
  let streamStarts = 0;
  const page = loadVoiceChatPage({
    assistantTurnRuntimeExports: {
      VoiceAssistantTurnRuntime: class {
        async start() {
          streamStarts += 1;
          throw new Error("不应调用模型");
        }
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      sessionGoal: "book_completion",
      currentCompletionIssueId: "issue-1",
      completionIssue: {
        id: "issue-1",
        status: "in_progress",
        question: "请补充这一段经历。",
      },
    },
  };

  await page.generateFollowUp.call(context, false);

  assert.equal(streamStarts, 0);
});

test("voice chat shows an error instead of continuing when session load fails", async () => {
  let connectCalls = 0;
  let followUpCalls = 0;
  const originalWarn = console.warn;
  console.warn = () => {};
  try {
    const page = loadVoiceChatPage({
      voiceApiExports: {
        getMemoirVoiceSession: async () => {
          throw new Error("语音记录不存在");
        },
      },
      voiceAuthorizationExports: {
        readVoiceAuthorizationConsent: () => true,
      },
    });
    const context = {
      ...page,
      data: { ...page.data },
      setData(patch) {
        this.data = { ...this.data, ...patch };
      },
      async connectGateway() {
        connectCalls += 1;
      },
      generateFollowUp() {
        followUpCalls += 1;
        return Promise.resolve();
      },
    };

    await page.onLoad.call(context, {
      voiceSessionId: "missing-voice",
      mode: "free_narration",
    });

    assert.equal(connectCalls, 0);
    assert.equal(followUpCalls, 0);
    assert.equal(context.data.status, "error");
    assert.equal(context.data.persistenceStateText, "会话加载失败");
    assert.equal(context.data.primaryActionText, "重新进入");
    assert.equal(context.data.error, "语音记录不存在");
  } finally {
    console.warn = originalWarn;
  }
});

test("voice chat does not show microphone connecting while session refresh is pending", async () => {
  let resolveDetail;
  const detailPromise = new Promise((resolve) => {
    resolveDetail = resolve;
  });
  let connectCalls = 0;
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}

        constructor(options) {
          this.options = options;
        }
        async connect() {
          connectCalls += 1;
          this.options.callbacks.onStatus("ready");
        }
        close() {}
        configureAutoStop() {}
      },
    },
    voiceApiExports: {
      getMemoirVoiceSession: async () => detailPromise,
    },
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
    },
  });
  const context = {
    ...page,
    data: { ...page.data },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  const loadPromise = page.onLoad.call(context, {
    voiceSessionId: "voice-1",
    mode: "guided_interview",
    module: "person",
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(context.data.status, "idle");

  resolveDetail({
    session: {
      status: "created",
      guidance_mode: "guided_interview",
      current_guidance_module: "person",
      error_message: "",
    },
    messages: [
      {
        id: "assistant-1",
        role: "assistant",
        content_text: "咱们接着聊。",
        turn_index: 1,
        created_at: "2026-06-23T00:00:00.000Z",
        metadata_json: {},
      },
    ],
    utterances: [],
  });
  await loadPromise;

  assert.equal(connectCalls, 1);
  assert.equal(context.data.status, "ready");
});

test("voice chat reconnects on user start after a failed connect", async () => {
  let connectCalls = 0;
  let startRecordingCalls = 0;
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}

        constructor(options) {
          this.options = options;
        }
        async connect() {
          connectCalls += 1;
          if (connectCalls === 1) {
            throw new Error("语音连接超时");
          }
          this.options.callbacks.onStatus("ready");
        }
        close() {}
        configureAutoStop() {}
        startRecording() {
          startRecordingCalls += 1;
          return true;
        }
      },
    },
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
      syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      status: "idle",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.connectGateway.call(context);
  assert.equal(connectCalls, 1);
  assert.equal(context.data.status, "error");

  const started = page.startRecording.call(context, false);
  await context.recordingStartPromise;

  assert.equal(started, true);
  assert.equal(connectCalls, 2);
  assert.equal(startRecordingCalls, 1);
  assert.equal(context.data.status, "ready");
});

test("voice chat does not auto reconnect when recorder start is not ready", async () => {
  let connectCalls = 0;
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}

        constructor(options) {
          this.options = options;
        }
        async connect() {
          connectCalls += 1;
          this.options.callbacks.onStatus("ready");
        }
        close() {}
        configureAutoStop() {}
        startRecording() {
          return false;
        }
      },
    },
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
      syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      status: "idle",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.connectGateway.call(context);
  const started = await page.startRecordingAfterPrivacy.call(context, false);

  assert.equal(started, false);
  assert.equal(connectCalls, 1);
  assert.equal(context.data.status, "error");
  assert.equal(context.data.primaryActionText, "重新连接");
  assert.equal(context.data.canReconnect, true);
  assert.match(context.data.error, /语音未就绪/);
});

test("voice chat finishSession ignores duplicate finish taps", async () => {
  let finishCalls = 0;
  let snapshotWrites = 0;
  let pendingCloseWrites = 0;
  let resolveFinish = () => {};
  const storyTabs = [];
  global.wx = {
    showTabBarRedDot() {},
    reLaunch(options) {
      storyTabs.push(options.url);
    },
  };
  const page = loadVoiceChatPage({
    memoirCacheExports: {
      writeMemoirVoiceSessionSnapshot: () => { snapshotWrites += 1; },
      writeMemoirVoiceSessionPendingClose: () => { pendingCloseWrites += 1; },
    },
    streamingExports: {
      MemoirStreamingAsrClient: class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}

        async connect() {}
        async finishSession() {
          finishCalls += 1;
          await new Promise((resolve) => {
            resolveFinish = resolve;
          });
        }
        close() {}
      },
    },
    voiceApiExports: {
      getMemoirVoiceSession: async () => ({
        session: {
          id: "voice-1",
          project_id: "project-1",
          status: "stopped",
          guidance_mode: "free_narration",
          current_guidance_module: "",
          error_message: "",
        },
        messages: [],
        utterances: [{id:"utterance-1",transcript_raw:"小时候我住在外婆家。",transcript_edited:""}],
      }),
      listMemoirAutomationEvents: async () => ({
        items: [{
          id: "growth-1",
          project_id: "project-1",
          event_type: "story_structure_updated",
          status: "completed",
          utterance_ids: [],
          summary_text: "故事树新长出了 1 个新小节",
          metadata_json: { voice_session_id: "voice-1" },
          created_at: "2026-09-01T00:00:00Z",
          updated_at: "2026-09-01T00:00:00Z",
        }],
        total: 1,
        limit: 8,
      }),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      status: "ready",
      voiceAuthorizationAgreed: true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.connectGateway.call(context);
  const firstFinish = page.finishSession.call(context);
  const secondFinish = page.finishSession.call(context);
  assert.equal(finishCalls, 1);
  assert.equal(context.data.finishingSession, true);
  resolveFinish();
  await Promise.all([firstFinish, secondFinish]);

  assert.equal(finishCalls, 1);
  assert.equal(context.data.status, "closed");
  assert.equal(context.data.finishingSession, false);
  assert.equal(context.data.persistenceStateText, "已保存");
  const writesAfterFinish = snapshotWrites;
  page.onUnload.call(context);
  assert.equal(snapshotWrites, writesAfterFinish);
  assert.equal(pendingCloseWrites, 0);
  await flushAsyncWork();
  assert.deepEqual(context.events, [{ name: "exit", detail: { destination: "story", projectId: "project-1", narrationSaved: true, finished: true, voiceSessionId: "voice-1" } }]);
  assert.deepEqual(storyTabs, []);
});

test("book completion exits only after the saved session is confirmed", async () => {
  const modals = [];
  global.wx = {
    showModal(options) {
      modals.push(options);
    },
  };
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}

        async connect() {}
        async finishSession() {}
        close() {}
      },
    },
    voiceApiExports: {
      getMemoirVoiceSession: async () => ({
        session: {
          id: "voice-completion-1",
          project_id: "project-1",
          status: "stopped",
          guidance_mode: "guided_interview",
          current_guidance_module: "",
          interview_type: "ai_interview",
          session_goal: "book_completion",
          current_completion_issue_id: null,
          error_message: "",
        },
        messages: [],
        utterances: [],
      }),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-completion-1",
      projectId: "project-1",
      projectTitle: "观鸟与晚餐",
      sessionGoal: "book_completion",
      status: "ready",
      voiceAuthorizationAgreed: true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.connectGateway.call(context);
  await page.finishSession.call(context);

  assert.deepEqual(context.events, [{ name: "exit", detail: { destination: "completion", voiceSessionId: "voice-completion-1" } }]);
  assert.deepEqual(modals, []);
});

test("voice chat save action cancels a recording start that is still authorizing", async () => {
  let resolveAuthorization;
  let recorderStartCalls = 0;
  let connectCalls = 0;
  const page = loadVoiceChatPage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      status: "closed",
    },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},
      hasClient: () => false,
      startRecording() {
        recorderStartCalls += 1;
        return true;
      },
      stopRecording() {},
      close() {},
    },
    syncVoiceAuthorizationForSession() {
      return new Promise((resolve) => {
        resolveAuthorization = resolve;
      });
    },
    async stopAssistantBargeInMonitor() {},
    async connectGateway() {
      connectCalls += 1;
      return true;
    },
    async refreshVoiceSessionSnapshot() {
      return null;
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  assert.equal(page.startRecording.call(context, false), true);
  const pendingStart = context.recordingStartPromise;
  await new Promise((resolve) => setImmediate(resolve));
  await page.finishSession.call(context);
  resolveAuthorization(true);

  assert.equal(await pendingStart, false);
  assert.equal(recorderStartCalls, 0);
  assert.equal(connectCalls, 0);
  assert.equal(context.data.recording, false);
  assert.equal(context.data.recordingStarting, false);
});

test("voice chat stores pending close state for connection errors", () => {
  const pendingWrites = [];
  const page = loadVoiceChatPage({
    memoirCacheExports: {
      writeMemoirVoiceSessionPendingClose: (voiceSessionId, pendingClose) => {
        pendingWrites.push({ voiceSessionId, pendingClose });
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      recording: true,
      liveText: "最后一句还在识别",
      transcriptLines: [{ id: "line-1", text: "已经保存的一句" }],
      persistenceStateText: "识别中",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.handleError.call(context, "语音连接已关闭", {
    kind: "connection",
    code: "voice_gateway_connection_closed",
    message: "语音连接已关闭",
  });

  assert.equal(pendingWrites.length, 1);
  assert.equal(pendingWrites[0].voiceSessionId, "voice-1");
  assert.equal(pendingWrites[0].pendingClose.reason, "connection_error");
  assert.equal(pendingWrites[0].pendingClose.live_text, "最后一句还在识别");
  assert.equal(pendingWrites[0].pendingClose.recording, true);
  assert.equal(context.data.status, "error");
  assert.equal(context.data.canReconnect, true);
  assert.equal(context.data.canFinishAfterDisconnect, true);
  assert.match(context.data.recoveryNotice, /语音连接中断/);
});

test("human interview surfaces a real provider close after an earlier transcript was saved", () => {
  const page = loadVoiceChatPage();
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      interviewType: "human_interview",
      status: "ready",
      recording: false,
      transcriptLines: [{ id: "line-1", role: "user", text: "这段已经保存。" }],
      latestTranscriptText: "这段已经保存。",
      persistenceStateText: "已保存",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.handleError.call(context, "语音识别服务连接已关闭，请重新保存这段", {
    kind: "asr",
    code: "asr_provider_connection_closed",
    message: "语音识别服务连接已关闭，请重新保存这段",
  });
  page.handleStatus.call(context, "error");

  assert.equal(context.data.error, "语音识别服务连接已关闭，请重新保存这段");
  assert.equal(context.data.status, "error");
});

test("voice chat refreshes session on show only after page suspension", () => {
  let refreshCalls = 0;
  const page = loadVoiceChatPage();
  const context = {
    ...page,
    pageSuspended: false,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      status: "ready",
    },
    refreshVoiceSessionSnapshot() {
      refreshCalls += 1;
      return Promise.resolve(null);
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.onShow.call(context);
  assert.equal(refreshCalls, 0);

  context.pageSuspended = true;
  page.onShow.call(context);

  assert.equal(refreshCalls, 1);
  assert.equal(context.pageSuspended, false);
});

for (const scenario of [
  { embedded: true, interviewType: "ai_interview", sessionGoal: "open_narration" },
  { embedded: true, interviewType: "human_interview", sessionGoal: "open_narration" },
  { embedded: false, interviewType: "ai_interview", sessionGoal: "book_completion" },
]) {
  test(`voice session keeps the screen on throughout ${scenario.interviewType}/${scenario.sessionGoal}`, async () => {
    const screenRequests = [];
    global.wx = { setKeepScreenOn: ({ keepScreenOn }) => screenRequests.push(keepScreenOn) };
    const page = loadVoiceChatPage();
    const context = componentContext(page.definition, { embedded: scenario.embedded });
    context.refreshVoiceSessionSnapshot = async () => null;

    await context.onLoad({ voiceSessionId: "voice-1", ...scenario });
    assert.deepEqual(screenRequests, [true], "preparing the session already prevents sleep");
    context.handleStatus("listening");
    context.handleStatus("ready");
    context.setData({ followUpLoading: true });
    context.setData({ followUpLoading: false, followUpTTSPlaying: true });
    context.setData({ followUpTTSPlaying: false });
    assert.deepEqual(screenRequests, [true], "recording, AI playback and gaps retain screen wakefulness");

    page.definition.pageLifetimes.hide.call(context);
    assert.equal(screenRequests.at(-1), false);
    page.definition.pageLifetimes.show.call(context);
    assert.equal(screenRequests.at(-1), true);
    context.exitSession({ destination: "transcripts" });
    assert.equal(screenRequests.at(-1), false);
    page.definition.lifetimes.detached.call(context);
    assert.equal(screenRequests.at(-1), false);
  });
}

for (const reconnectFirst of [false, true]) {
  test(`screen stays awake after a background socket close and ${reconnectFirst ? "explicit reconnect" : "recording reconnect"}`, async () => {
    const screenRequests = [];
    const clients = [];
    let recordingStarts = 0;
    global.wx = { setKeepScreenOn: ({ keepScreenOn }) => screenRequests.push(keepScreenOn) };
    const page = loadVoiceChatPage({
      voiceAuthorizationExports: { readVoiceAuthorizationConsent: () => true },
      streamingExports: { MemoirStreamingAsrClient: class {
        constructor(options) { this.options = options; clients.push(this); }
        async connect() { this.options.callbacks.onStatus("ready"); }
        close() {}
        isBargeInMonitoring() { return false; }
        async stopBargeInMonitoring() {}
        startRecording() { recordingStarts += 1; return true; }
        stopRecording() {}
      } },
    });
    const context = componentContext(page.definition, {}, {
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
    });
    context.syncVoiceAuthorizationForSession = async () => true;
    context.focusRecordingTranscript = () => {};
    await context.connectGateway();
    page.definition.pageLifetimes.show.call(context);
    assert.equal(await context.startRecordingAfterPrivacy(), true);
    assert.equal(screenRequests.at(-1), true);

    page.definition.pageLifetimes.hide.call(context);
    assert.equal(screenRequests.at(-1), false);
    clients[0].options.callbacks.onStatus("closed");
    assert.equal(context.data.status, "closed");
    assert.equal(context.runtime().sessionStopConfirmed, false);
    assert.equal(context.runtime().voiceStreaming.hasClient(), false);

    page.definition.pageLifetimes.show.call(context);
    if (reconnectFirst) await context.reconnectGateway();
    assert.equal(await context.startRecordingAfterPrivacy(), true);
    assert.equal(clients.length, 2);
    assert.equal(recordingStarts, 2);
    assert.equal(context.data.recording, true);
    assert.equal(screenRequests.at(-1), true, "a recoverable transport close must not disable screen wakefulness");
    page.definition.lifetimes.detached.call(context);
    assert.equal(screenRequests.at(-1), false);
  });
}

test("embedded voice chat uses home as its only finish action and saves before returning", async () => {
  const template = fs.readFileSync(path.join(__dirname, "../components/voice-session/index.wxml"), "utf8");
  assert.match(template, /wx:if="\{\{!embedded\}\}"\s+id="voice-finish-action"/);
  assert.match(template, /id="voice-home-action"[\s\S]*?bindtap="returnToHome"/);
  const page = loadVoiceChatPage();
  const context = componentContext(page.definition, { embedded: true }, {
    voiceSessionId: "voice-1",
    finishingSession: false,
  });
  let finishCalls = 0;
  context.finishSession = async () => {
    finishCalls += 1;
  };

  await context.returnToHome();

  assert.equal(finishCalls, 1);
  assert.equal(context.runtime().pageSuspended, false);
  assert.deepEqual(context.events, []);
});

test("voice chat stops active AI speech when the page is hidden", () => {
  const cancelReasons = [];
  const pendingWrites = [];
  let audioStopped = false;
  let stopRecordingCalls = 0;
  const page = loadVoiceChatPage({
    memoirCacheExports: {
      writeMemoirVoiceSessionPendingClose: (voiceSessionId, pendingClose) => {
        pendingWrites.push({ voiceSessionId, pendingClose });
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      recording: true,
      liveText: "最后一句还在识别",
      transcriptLines: [
        { id: "user-line-1", role: "user", text: "已经说完的一句" },
        { id: "assistant-stream-1", role: "assistant", text: "那后来怎么样？" },
      ],
      followUpLoading: true,
      followUpTTSLoading: true,
      followUpTTSPlaying: true,
      followUpTTSStateText: "小余正在说",
      assistantPromptText: "那后来怎么样？",
    },
    assistantStreamLineId: "assistant-stream-1",
    assistantTurnRuntime: {
      cancel(reason) {
        cancelReasons.push(reason);
      },
      stopPlayback() {},
      stats() {
        return null;
      },
    },
    audioPlayback: {
      currentToken: () => 4,
      currentAudio: () => null,
      isCurrentToken: () => true,
      nextToken: () => 4,
      stop() {
        audioStopped = true;
      },
    },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},
      hasClient: () => true,
      stopRecording() {
        stopRecordingCalls += 1;
      },
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.onHide.call(context);

  assert.equal(context.pageSuspended, true);
  assert.deepEqual(cancelReasons, ["page_hidden"]);
  assert.equal(audioStopped, true);
  assert.equal(stopRecordingCalls, 1);
  assert.equal(context.assistantTurnRuntime, null);
  assert.deepEqual(context.data.transcriptLines.map((line) => line.id), ["user-line-1"]);
  assert.equal(context.data.recording, false);
  assert.equal(context.data.followUpLoading, false);
  assert.equal(context.data.followUpTTSLoading, false);
  assert.equal(context.data.followUpTTSPlaying, false);
  assert.equal(context.data.persistenceStateText, "正在保存当前句");
  assert.equal(pendingWrites.length, 1);
  assert.equal(pendingWrites[0].voiceSessionId, "voice-1");
  assert.equal(pendingWrites[0].pendingClose.reason, "hide");
  assert.equal(pendingWrites[0].pendingClose.live_text, "最后一句还在识别");
});

test("voice chat keeps hidden ASR final text without starting a new AI turn", () => {
  let followUpCalls = 0;
  const page = loadVoiceChatPage();
  const context = {
    ...page,
    pageSuspended: true,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      guidanceMode: "guided_interview",
      transcriptLines: [],
    },
    generateFollowUp() {
      followUpCalls += 1;
    },
    confirmSavedTranscriptSnapshot() {},
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.handleFinal.call(context, {
    type: "asr.final",
    text: "我小时候经常在院子里摘菜。",
    message_id: "message-1",
    utterance_id: "utterance-1",
    transcript_version_id: "version-1",
    turn_final: true,
  });

  assert.equal(followUpCalls, 0);
  assert.equal(context.data.transcriptLines.length, 1);
  assert.equal(context.data.transcriptLines[0].text, "我小时候经常在院子里摘菜。");
  assert.equal(context.data.followUpLoading, false);
});

test("voice chat warns when a pending live transcript is not in backend detail", () => {
  let clearCalls = 0;
  const page = loadVoiceChatPage({
    memoirCacheExports: {
      clearMemoirVoiceSessionPendingClose: () => {
        clearCalls += 1;
      },
      readMemoirVoiceSessionSnapshot: () => ({
        hasSession: true,
        transcriptLines: [{ id: "line-1", text: "已经保存的一句" }],
        pendingClose: {
          reason: "unload",
          captured_at: "2026-06-18T00:00:00.000Z",
          transcript_lines: [{ id: "line-1", text: "已经保存的一句" }],
          live_text: "最后一句还没回来",
          recording: true,
        },
      }),
      transcriptLinesFromVoiceSessionDetail: () => [
        { id: "line-1", text: "已经保存的一句" },
      ],
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.reconcilePendingClose.call(context, {
    session: { id: "voice-1" },
    messages: [],
    utterances: [],
  });

  assert.equal(clearCalls, 0);
  assert.match(context.data.recoveryNotice, /最后一句可能未保存/);
  assert.equal(context.data.canReconnect, true);
  assert.equal(context.data.canFinishAfterDisconnect, true);
  assert.equal(context.data.persistenceStateText, "已恢复已保存记录");
});

test("voice chat does not request follow-up for non-final saved ASR events", () => {
  const page = loadVoiceChatPage();
  let followUpCalls = 0;
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      guidanceMode: "guided_interview",
      transcriptLines: [],
      lastFollowUpSourceMessageId: "",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    async confirmSavedTranscriptSnapshot() {},
    async generateFollowUp() {
      followUpCalls += 1;
    },
  };

  page.handleFinal.call(context, {
    type: "asr.final",
    text: "先讲一段很长的童年经历。",
    message_id: "message-1",
    utterance_id: "utterance-1",
    transcript_version_id: "version-1",
    turn_final: false,
  });

  assert.equal(context.data.transcriptLines.length, 1);
  assert.equal(context.data.transcriptLines[0].text, "先讲一段很长的童年经历。");
  assert.equal(context.data.persistenceStateText, "已保存");
  assert.equal(followUpCalls, 0);
});

test("human completion interview falls back to the selected speaker when provider final omits role", () => {
  const page = loadVoiceChatPage();
  let verificationCalls = 0;
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      interviewType: "human_interview",
      sessionGoal: "book_completion",
      speakerRole: "interviewer",
      completionIssue: {
        id: "issue-1",
        status: "in_progress",
        question: "后来发生了什么？",
      },
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    startCompletionIssueVerification() {
      verificationCalls += 1;
    },
  };

  page.handleFinal.call(context, {
    type: "asr.final",
    text: "采访者补充说明问题背景。",
    turn_final: true,
  });
  assert.equal(verificationCalls, 0);

  context.data.speakerRole = "narrator";
  page.handleFinal.call(context, {
    type: "asr.final",
    text: "被访者开始回答这个问题。",
    turn_final: true,
  });
  assert.equal(verificationCalls, 1);
  assert.equal(context.data.assistantExpectsAnswer, false);
});

test("completed book answer cannot start another recording", () => {
  const page = loadVoiceChatPage();
  let authorizationChecks = 0;
  const context = {
    ...page,
    recordingStartPromise: null,
    data: {
      ...page.data,
      sessionGoal: "book_completion",
      assistantExpectsAnswer: false,
      transcriptLines: [
        { id: "answer-1", role: "user", text: "这段背景已经补充完整。" },
      ],
      completionIssue: {
        id: "issue-1",
        status: "in_progress",
        question: "这段内容开始前还需要哪些背景？",
      },
    },
    ensureVoiceAuthorization() {
      authorizationChecks += 1;
      return true;
    },
  };

  const started = page.startRecording.call(context, false);

  assert.equal(started, false);
  assert.equal(authorizationChecks, 0);
});

test("voice chat clears live transcript when a new recording session starts", () => {
  const page = loadVoiceChatPage();
  const context = {
    ...page,
    data: {
      ...page.data,
      liveText: "上一轮刚保存的回答",
      liveCharCount: 9,
      liveTranscriptFollowing: false,
      liveReviewStartCharCount: 9,
      persistenceStateText: "已保存",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.handleGatewayEvent.call(context, {
    type: "session.started",
    state: "listening",
  });

  assert.equal(context.data.liveText, "");
  assert.equal(context.data.liveCharCount, 0);
  assert.equal(context.data.liveScrollClass, "live-scroll");
  assert.equal(context.data.liveScrollTop, 0);
  assert.equal(context.data.liveTranscriptFollowing, true);
  assert.equal(context.data.liveReviewStartCharCount, 0);
  assert.equal(context.data.persistenceStateText, "等待识别");
});

test("voice chat follows the rendered transcript height with one scroll source", () => {
  const page = loadVoiceChatPage();
  const rects = {
    ".live-scroll": { top: 0, bottom: 390, left: 0, right: 320, width: 320, height: 390 },
    ".live-text-content": { top: 0, bottom: 560, left: 0, right: 320, width: 320, height: 560 },
  };
  const context = {
    ...page,
    data: {
      ...page.data,
      recording: true,
      voiceAuthorizationAgreed: true,
      liveTranscriptFollowing: true,
    },
    setData(patch, callback) {
      this.data = { ...this.data, ...patch };
      callback?.();
    },
    createSelectorQuery() {
      const measurements = [];
      const query = {
        select(selector) {
          return {
            boundingClientRect(callback) {
              measurements.push([selector, callback]);
              return query;
            },
          };
        },
        exec() {
          for (const [selector, callback] of measurements) callback(rects[selector] || null);
        },
      };
      return query;
    },
  };

  page.handlePartial.call(context, "这是一段正在实时增长的讲述。".repeat(8));

  assert.equal(context.data.liveScrollTop, 170);
  assert.equal(context.data.liveTranscriptFollowing, true);
  assert.equal(context.data.liveScrollTarget, undefined);
  assert.equal(context.data.liveTextEndId, undefined);
});

function recordingViewportContext(page, geometry) {
  const scrolls = [];
  global.wx = { ...global.wx, pageScrollTo: options => scrolls.push(options) };
  let completeMeasurement;
  const context = {
    ...page,
    data: { ...page.data, voiceAuthorizationAgreed: true, status: "ready", assistantPromptText: "长开场。".repeat(50) },
    voiceStreaming: { hasClient: () => true, startRecording: () => true },
    async syncVoiceAuthorizationForSession() { return true; },
    async stopAssistantBargeInMonitor() {},
    cancelFollowUpTTS() {},
    setData(patch, callback) { this.data = { ...this.data, ...patch }; callback?.(); },
    createSelectorQuery() {
      const { stageTop, stageHeight, dockTop, dockHeight = 150, pageTop = 0, deferred = false } = geometry;
      const callbacks = [];
      const query = {
        select(selector) {
          return { boundingClientRect(callback) {
            callbacks.push(() => callback(selector === ".transcript-stage"
              ? { top: stageTop, height: stageHeight } : selector === ".control-bar" ? { top: dockTop, height: dockHeight } : null));
            return query;
          } };
        },
        selectViewport() {
          return { scrollOffset(callback) { callbacks.push(() => callback({ scrollTop: pageTop })); return query; } };
        },
        exec() {
          completeMeasurement = () => callbacks.forEach(callback => callback());
          if (!deferred) completeMeasurement();
        },
      };
      return query;
    },
  };
  return { context, scrolls, complete: () => completeMeasurement() };
}

test("manual and automatic recording bring captions above the dock without following page scroll on partials", async () => {
  for (const autoTriggered of [false, true]) {
    const page = loadVoiceChatPage();
    const { context, scrolls } = recordingViewportContext(page, {
      stageTop: 1200, stageHeight: autoTriggered ? 640 : 300, dockTop: 520, pageTop: 100,
    });
    assert.equal(await context.startRecordingAfterPrivacy(autoTriggered), true);
    assert.equal(scrolls.length, 1);
    const top = 1200 - (scrolls[0].scrollTop - 100);
    assert.ok(top >= 16);
    assert.ok(top + context.data.transcriptStageHeight <= 520 - 16);
    assert.ok(context.data.recordingDockSpace >= 150 + 16);
    assert.equal(scrolls[0].duration, 0);
    assert.equal(context.data.assistantDisplayText, context.data.assistantPromptText);

    context.handlePartial("我家院子有一棵葡萄树。".repeat(10));
    context.pauseLiveTranscriptFollow();
    context.handlePartial("我家院子有一棵葡萄树。".repeat(12));
    assert.equal(scrolls.length, 1);
    assert.equal(context.data.liveTranscriptFollowing, false);
  }
});

test("late recording layout measurements cannot scroll a stopped, hidden or replaced session", () => {
  for (const invalidate of [
    context => { context.data.recording = false; },
    context => { context.pageSuspended = true; },
    context => { context.disposed = true; },
    context => { context.data.liveTranscriptFollowing = false; },
    context => { context.transcriptLayoutVersion += 1; },
  ]) {
    const { context, scrolls, complete } = recordingViewportContext(loadVoiceChatPage(), {
      stageTop: 1200, stageHeight: 300, dockTop: 520, deferred: true,
    });
    context.data.recording = true;
    context.focusRecordingTranscript();
    invalidate(context);
    complete();
    assert.deepEqual(scrolls, []);
    assert.equal(context.data.transcriptStageHeight, 0);
  }
});

test("restoring caption follow refits a window resized during review without moving the review position", async () => {
  const page = loadVoiceChatPage();
  const geometry = { stageTop: 1200, stageHeight: 640, dockTop: 720, pageTop: 100 };
  const { context, scrolls } = recordingViewportContext(page, geometry);
  assert.equal(await context.startRecordingAfterPrivacy(true), true);
  const originalHeight = context.data.transcriptStageHeight;
  const originalSpace = context.data.recordingDockSpace;
  context.handlePartial("我家院子有一棵葡萄树。".repeat(10));
  context.pauseLiveTranscriptFollow();

  Object.assign(geometry, { stageTop: 30, dockTop: 300, dockHeight: 200, pageTop: scrolls[0].scrollTop });
  page.definition.pageLifetimes.resize.call(context);
  assert.equal(scrolls.length, 1);
  assert.equal(context.data.transcriptStageHeight, originalHeight);
  assert.equal(context.data.recordingDockSpace, originalSpace);

  context.resumeLiveTranscriptFollow();
  const top = geometry.stageTop - (scrolls.at(-1).scrollTop - geometry.pageTop);
  assert.ok(top >= 16);
  assert.ok(top + context.data.transcriptStageHeight <= geometry.dockTop - 16);
  assert.ok(context.data.recordingDockSpace >= geometry.dockHeight + 16);
  const fittedHeight = context.data.transcriptStageHeight;
  const scrollCount = scrolls.length;

  context.handlePartial("我家院子有一棵葡萄树。".repeat(12));
  context.pauseLiveTranscriptFollow();
  context.resumeLiveTranscriptFollow();
  assert.equal(scrolls.length, scrollCount, "normal resume only follows text inside the card");
  assert.equal(context.data.transcriptStageHeight, fittedHeight);
});

test("voice chat syncs authorization consent before reconnecting", async () => {
  const syncedOptions = [];
  const page = loadVoiceChatPage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
      syncVoiceAuthorizationConsent: async (options) => {
        syncedOptions.push(options);
        return { id: "consent-1" };
      },
    },
  });
  let connectCalls = 0;
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: false,
      status: "idle",
      primaryActionText: "同意后开始说话",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    async connectGateway() {
      connectCalls += 1;
    },
  };

  page.refreshVoiceAuthorizationConsent.call(context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(syncedOptions, [{ sourceSessionId: "voice-1" }]);
  assert.equal(connectCalls, 1);
  assert.equal(context.data.voiceAuthorizationAgreed, true);
});

test("voice chat records an explicit agreement before opening the recorder gateway", async () => {
  const actions = [];
  const page = loadVoiceChatPage({
    voiceAuthorizationExports: {
      syncVoiceAuthorizationConsent: async (options) => { actions.push(options); return { id: "consent-1" }; },
    },
  });
  const context = {
    ...page,
    data: { ...page.data, voiceSessionId: "voice-1", status: "idle" },
    setData(patch) { this.data = { ...this.data, ...patch }; },
    async syncVoiceAuthorizationAndConnect() { actions.push("connect"); },
  };

  await page.changeVoiceAuthorization.call(context, { detail: { value: ["voice_authorization"] } });

  assert.equal(actions[0].consent.agreed, true);
  assert.equal(actions[0].sourceSessionId, "voice-1");
  assert.equal(actions[1], "connect");
});

test("voice chat uses conservative silence thresholds for automatic turn ending", async () => {
  const connectOptions = [];
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}

        constructor(options) {
          connectOptions.push(options);
          this.options = options;
        }
        async connect() {
          this.options.callbacks.onStatus("ready");
        }
        close() {}
        configureAutoStop() {}
      },
    },
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
      syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      autoStopEnabled: true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.connectGateway.call(context);

  assert.equal(connectOptions.length, 1);
  assert.equal(connectOptions[0].autoStopSilenceMs, 3500);
  assert.equal(connectOptions[0].minAutoStopRecordingMs, 2000);
});

test("voice chat uses no-answer timeout only for auto-listen recordings", async () => {
  const startOptions = [];
  const authorizationSessions = [];
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}

        async connect() {}
        close() {}
        startRecording(options) {
          startOptions.push(options);
          return true;
        }
        stopRecording() {}
        configureAutoStop() {}
        async finishSession() {}
      },
    },
    voiceAuthorizationExports: {
      ensureVoiceRecordingAuthorization: async (voiceSessionId) => {
        authorizationSessions.push(voiceSessionId);
        return true;
      },
      readVoiceAuthorizationConsent: () => true,
      syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      status: "ready",
      transcriptLines: [{
        id: "assistant-line-1",
        role: "assistant",
        text: "后来还发生了什么？",
        message_id: "assistant-message-1",
      }],
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.connectGateway.call(context);
  await page.startRecordingAfterPrivacy.call(context, true);
  context.setData({ recording: false });
  await page.startRecordingAfterPrivacy.call(context, false);

  assert.equal(startOptions.length, 2);
  assert.deepEqual(authorizationSessions, ["voice-1", "voice-1"]);
  assert.deepEqual(startOptions[0], {
    noVoiceActivityTimeoutMs: 15000,
    parentMessageId: "assistant-message-1",
  });
  assert.deepEqual(startOptions[1], {
    noVoiceActivityTimeoutMs: 0,
    parentMessageId: "assistant-message-1",
  });
  assert.equal(context.data.primaryActionText, "暂停收音");
});

test("voice chat waits for barge-in monitor recorder stop before auto-listen recording starts", async () => {
  const startOptions = [];
  let stopCalls = 0;
  let releaseMonitor;
  const monitorStopped = new Promise((resolve) => {
    releaseMonitor = resolve;
  });
  const page = loadVoiceChatPage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      status: "ready",
    },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      stopBargeInMonitoring() { stopCalls += 1; return monitorStopped; },
      hasClient: () => true,
      startRecording(options) {
        startOptions.push(options);
        return true;
      },
      stopRecording() {},
      close() {},
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  const startedPromise = page.startRecordingAfterPrivacy.call(context, true);
  await Promise.resolve();

  assert.equal(stopCalls, 1);
  assert.deepEqual(startOptions, []);

  releaseMonitor();
  const started = await startedPromise;

  assert.equal(started, true);
  assert.deepEqual(startOptions, [{ noVoiceActivityTimeoutMs: 15000 }]);
  assert.equal(context.data.recording, true);
});

test("voice chat starts auto listening after AI audio playback ends when gateway is ready", async () => {
  const startCalls = [];
  installAutoEndingAudioContext();
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}

        async connect() {}
        close() {}
        startRecording() {
          return true;
        }
        stopRecording() {}
        configureAutoStop() {}
        async finishSession() {}
      },
    },
    voiceApiExports: {
      downloadMemoirVoiceAudio: async () => "/tmp/assistant.mp3",
    },
    voiceChatAutoListenExports: {
      canAutoStartListeningAfterTTS: (state) => (
        state.autoListenEnabled
        && state.status === "ready"
        && !state.recording
        && !state.followUpLoading
      ),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      autoListenEnabled: true,
      status: "ready",
      assistantPromptText: "您接着讲吧。",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    async startRecordingAfterPrivacy(autoTriggered) {
      startCalls.push(autoTriggered);
      return true;
    },
  };

  await page.connectGateway.call(context);
  await page.playAudioFile.call(context, "audio-1", { autoListenAfterPlayback: true });
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(startCalls, [true]);
  assert.equal(context.data.followUpTTSPlaying, false);
  assert.equal(context.data.followUpTTSStateText, "播放完成，正在听你说");
});

test("voice chat starts pending auto listening when gateway becomes ready after playback", async () => {
  const startCalls = [];
  let releaseConnection = () => {};
  installAutoEndingAudioContext();
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}

        constructor(options) {
          this.options = options;
        }
        async connect() {
          this.options.callbacks.onStatus("connecting");
          await new Promise((resolve) => {
            releaseConnection = () => {
              this.options.callbacks.onStatus("ready");
              resolve();
            };
          });
        }
        close() {}
        startRecording() {
          return true;
        }
        stopRecording() {}
        configureAutoStop() {}
        async finishSession() {}
      },
    },
    voiceApiExports: {
      downloadMemoirVoiceAudio: async () => "/tmp/assistant.mp3",
    },
    voiceChatAutoListenExports: {
      canAutoStartListeningAfterTTS: (state) => (
        state.autoListenEnabled
        && state.status === "ready"
        && !state.recording
        && !state.followUpLoading
      ),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      autoListenEnabled: true,
      status: "idle",
      assistantPromptText: "您接着讲吧。",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    async startRecordingAfterPrivacy(autoTriggered) {
      startCalls.push(autoTriggered);
      return true;
    },
  };

  const connectPromise = page.connectGateway.call(context);
  await page.playAudioFile.call(context, "audio-1", { autoListenAfterPlayback: true });
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(startCalls, []);
  assert.equal(context.autoListenPendingAfterPlayback, true);

  releaseConnection();
  await connectPromise;

  assert.deepEqual(startCalls, [true]);
  assert.equal(context.autoListenPendingAfterPlayback, false);
});

test("voice chat streams one assistant turn and plays pcm audio frames", async () => {
  const streamStarts = [];
  const played = [];
  const playbackEvents = [];
  const voicePlayedCalls = [];
  const page = loadVoiceChatPage({
    voiceApiExports: {
      markMemoirAutomationEventsVoicePlayed: async (projectId, data) => {
        voicePlayedCalls.push({ projectId, data });
        return { updated_count: data.event_ids.length };
      },
    },
    assistantTurnStreamExports: {
      VoiceAssistantTurnStreamClient: class {
        constructor(voiceSessionId, callbacks) {
          this.voiceSessionId = voiceSessionId;
          this.callbacks = callbacks;
        }
        async start(options) {
          streamStarts.push({ voiceSessionId: this.voiceSessionId, options });
          this.callbacks.onStarted?.({ type: "turn.started", turn_id: "turn-1" });
          this.callbacks.onTextDelta?.({
            type: "text.delta",
            turn_id: "turn-1",
            text_delta: "那时候在院子里，",
            text: "那时候在院子里，",
          });
          this.callbacks.onTextDelta?.({
            type: "text.delta",
            turn_id: "turn-1",
            text_delta: "外婆最常带着您做什么？",
            text: "那时候在院子里，外婆最常带着您做什么？",
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
          this.callbacks.onAudioDone?.({
            type: "audio.done",
            turn_id: "turn-1",
            voice_session_id: "voice-1",
            frame_count: 1,
            duration_seconds: 0.2,
          });
          const completed = {
            type: "turn.completed",
            turn_id: "turn-1",
            state: "completed",
            project_id: "project-1",
            message_id: "assistant-question-1",
            audio_file_id: "audio-question-1",
            text: "那时候在院子里，外婆最常带着您做什么？",
            primary_intent: "follow_up",
            intent_tags: ["follow_up"],
            expects_answer: true,
            progress_update: {
              kind: "valid_utterance_milestone",
              text: "这个项目已经留下 1 条讲述记录。",
            },
            progress_update_used: true,
            progress_voice_selected_event_ids: ["event-1"],
          };
          this.callbacks.onCompleted?.(completed);
          return completed;
        }
        cancel() {}
        close() {}
      },
    },
    streamingSpeechPlaybackExports: {
      StreamingSpeechPlaybackController: class {
        async appendFrame(frame) {
          playbackEvents.push("appendFrame");
          played.push(frame);
        }
        async waitForDrain() {
          playbackEvents.push("waitForDrain");
        }
        async destroy() {
          playbackEvents.push("destroy");
        }
        stop() {}
      },
    },
    voiceChatAutoListenExports: {
      canAutoStartListeningAfterTTS: () => true,
    },
  });
  const startCalls = [];
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      guidanceMode: "guided_interview",
      transcriptLines: [{
        id: "user-line-1",
        role: "user",
        text: "我小时候经常和外婆在院子里摘菜。",
        message_id: "user-message-1",
      }],
      lastFollowUpSourceMessageId: "previous-source",
      autoListenEnabled: true,
      status: "ready",
    },
    audioPlayback: {
      currentToken: () => 1,
      isCurrentToken: () => true,
      nextToken: () => 1,
      stop() {},
    },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},
      hasClient: () => true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    async startRecordingAfterPrivacy(autoTriggered) {
      playbackEvents.push("startRecording");
      startCalls.push(autoTriggered);
      return true;
    },
  };

  await page.generateFollowUp.call(context, false);

  assert.equal(streamStarts[0].voiceSessionId, "voice-1");
  assert.equal(streamStarts[0].options.regenerate, false);
  assert.equal(played.length, 1);
  assert.deepEqual(Array.from(played[0].audio), [1, 2, 3]);
  assert.equal(played[0].sampleRate, 16000);
  assert.equal(context.data.assistantPromptText, "那时候在院子里，外婆最常带着您做什么？");
  assert.equal(context.data.followUpMessageId, "assistant-question-1");
  assert.equal(context.data.followUpAudioFileId, "audio-question-1");
  assert.equal(context.data.transcriptLines.length, 2);
  assert.deepEqual(context.data.transcriptLines.map((line) => line.id), ["user-line-1", "assistant-question-1"]);
  assert.equal(context.data.transcriptLines[1].metadata_json.progress_update.kind, "valid_utterance_milestone");
  assert.equal(context.data.transcriptLines[1].metadata_json.progress_update_used, true);
  assert.equal(context.data.lastFollowUpSourceMessageId, "");
  assert.equal(context.data.persistenceStateText, "等待回答");
  assert.deepEqual(startCalls, [true]);
  assert.deepEqual(playbackEvents, ["appendFrame", "waitForDrain", "destroy", "startRecording"]);
  // Starting the next recording cancels playback cleanup; the persisted
  // question must no longer be treated as a temporary streaming line.
  page.cancelFollowUpTTS.call(context);
  assert.equal(context.data.assistantPromptText, "那时候在院子里，外婆最常带着您做什么？");
  assert.equal(context.data.followUpMessageId, "assistant-question-1");
  assert.deepEqual(voicePlayedCalls, [
    {
      projectId: "project-1",
      data: {
        event_ids: ["event-1"],
        voice_session_id: "voice-1",
        message_id: "assistant-question-1",
      },
    },
  ]);
});

test("voice chat mic interrupts active assistant turn and starts recording", async () => {
  const cancelReasons = [];
  const startOptions = [];
  let audioStopped = false;
  const page = loadVoiceChatPage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      status: "ready",
      guidanceMode: "guided_interview",
      followUpLoading: true,
      followUpTTSLoading: true,
      followUpTTSPlaying: true,
      followUpTTSStateText: "小余正在说",
      followUpError: "previous error",
      assistantPromptText: "那时候在院子里，外婆最常带着您做什么？",
      transcriptLines: [
        { id: "user-line-1", role: "user", text: "我小时候经常在院子里摘菜。" },
        {
          id: "assistant-stream-1",
          role: "assistant",
          text: "那时候在院子里，外婆最常带着您做什么？",
        },
      ],
    },
    assistantStreamLineId: "assistant-stream-1",
    assistantTurnRuntime: {
      cancel(reason) {
        cancelReasons.push(reason);
      },
      stopPlayback() {},
      stats() {
        return null;
      },
    },
    audioPlayback: {
      currentToken: () => 1,
      currentAudio: () => null,
      isCurrentToken: () => true,
      nextToken: () => 1,
      stop() {
        audioStopped = true;
      },
    },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},
      hasClient: () => true,
      startRecording(options) {
        startOptions.push(options);
        return true;
      },
      stopRecording() {},
      close() {},
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  const started = await page.startRecordingAfterPrivacy.call(context, false);

  assert.equal(started, true);
  assert.deepEqual(cancelReasons, ["interrupted_by_user"]);
  assert.deepEqual(startOptions, [{ noVoiceActivityTimeoutMs: 0 }]);
  assert.equal(audioStopped, true);
  assert.equal(context.assistantTurnRuntime, null);
  assert.equal(context.data.recording, true);
  assert.equal(context.data.followUpLoading, false);
  assert.equal(context.data.followUpTTSLoading, false);
  assert.equal(context.data.followUpTTSPlaying, false);
  assert.equal(context.data.followUpError, "");
  assert.equal(context.data.primaryActionText, "暂停收音");
  assert.deepEqual(context.data.transcriptLines.map((line) => line.id), ["user-line-1"]);
});

test("voice chat mic interrupts assistant turn before async recording checks finish", async () => {
  const cancelReasons = [];
  const startOptions = [];
  let resolveConsentSync;
  const consentSyncPromise = new Promise((resolve) => {
    resolveConsentSync = resolve;
  });
  const page = loadVoiceChatPage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
      syncVoiceAuthorizationConsent: () => consentSyncPromise,
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      status: "ready",
      guidanceMode: "guided_interview",
      followUpLoading: true,
      followUpTTSLoading: false,
      followUpTTSPlaying: true,
      followUpTTSStateText: "小余正在说",
      assistantPromptText: "您刚才说到第一份工作，那里最难忘的一个人是谁？",
      transcriptLines: [
        { id: "user-line-1", role: "user", text: "我第一份工作是在供销社。" },
        {
          id: "assistant-stream-1",
          role: "assistant",
          text: "您刚才说到第一份工作，那里最难忘的一个人是谁？",
        },
      ],
    },
    assistantStreamLineId: "assistant-stream-1",
    assistantTurnRuntime: {
      cancel(reason) {
        cancelReasons.push(reason);
      },
      stopPlayback() {},
      stats() {
        return null;
      },
    },
    audioPlayback: {
      currentToken: () => 1,
      currentAudio: () => null,
      isCurrentToken: () => true,
      nextToken: () => 1,
      stop() {},
    },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},
      hasClient: () => true,
      startRecording(options) {
        startOptions.push(options);
        return true;
      },
      stopRecording() {},
      close() {},
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  const startedPromise = page.startRecordingAfterPrivacy.call(context, false);

  assert.deepEqual(cancelReasons, ["interrupted_by_user"]);
  assert.equal(context.assistantTurnRuntime, null);
  assert.equal(context.data.followUpTTSPlaying, false);
  assert.deepEqual(context.data.transcriptLines.map((line) => line.id), ["user-line-1"]);
  assert.deepEqual(startOptions, []);

  resolveConsentSync({ id: "consent-1" });
  const started = await startedPromise;

  assert.equal(started, true);
  assert.deepEqual(startOptions, [{ noVoiceActivityTimeoutMs: 0 }]);
  assert.equal(context.data.recording, true);
});

test("voice chat automatically interrupts assistant playback when server speech monitor fires", async () => {
  let monitorOptions = null;
  const monitor = {
    running: false,
    start(options) {
      monitorOptions = options;
      this.running = true;
      return true;
    },
    stop() {
      this.running = false;
    },
    async stopAndWait() {
      this.stop();
    },
    isRunning() {
      return this.running;
    },
  };
  let releaseConsent;
  let consentCalls = 0;
  const consentReady = new Promise(resolve => { releaseConsent = resolve; });
  const cancelReasons = [];
  const startOptions = [];
  const page = loadVoiceChatPage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
      ensureVoiceRecordingAuthorization: () => { consentCalls++; return consentReady; },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      status: "ready",
      guidanceMode: "guided_interview",
      followUpLoading: true,
      followUpTTSLoading: false,
      followUpTTSPlaying: false,
      followUpTTSStateText: "小余正在说",
      followUpError: "",
      assistantPromptText: "那咱们接着聊朋友。",
      transcriptLines: [
        { id: "user-line-1", role: "user", text: "我想聊聊朋友。" },
        { id: "assistant-stream-1", role: "assistant", text: "那咱们接着聊朋友。" },
      ],
    },
    assistantStreamLineId: "assistant-stream-1",
    assistantTurnRuntime: {
      cancel(reason) {
        cancelReasons.push(reason);
      },
      stopPlayback() {},
      stats() {
        return null;
      },
    },
    audioPlayback: {
      currentToken: () => 1,
      currentAudio: () => null,
      isCurrentToken: () => true,
      nextToken: () => 1,
      stop() {},
    },
    voiceStreaming: {
      isBargeInMonitoring: () => monitor.isRunning(),
      startBargeInMonitoring: (options) => monitor.start(options),
      stopBargeInMonitoring: () => monitor.stopAndWait(),
      hasClient: () => true,
      startRecording(options) {
        startOptions.push(options);
        return true;
      },
      stopRecording() {},
      close() {},
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.prepareStreamingAudioPlayback.call(context, 0);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(monitor.running, true);
  assert.ok(monitorOptions);

  monitorOptions.onBargeIn();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(context.data.recordingStarting, true);
  assert.equal(page.startRecording.call(context, false), true);
  assert.equal(consentCalls, 1);
  releaseConsent({ id: "consent-1" });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(monitor.running, false);
  assert.deepEqual(cancelReasons, ["interrupted_by_user"]);
  assert.deepEqual(startOptions, [{ noVoiceActivityTimeoutMs: 0 }]);
  assert.equal(context.data.recording, true);
  assert.equal(context.data.followUpTTSPlaying, false);
});

test("voice chat does not start barge-in monitor when the setting is off", () => {
  let startCount = 0;
  const monitor = {
    start() {
      startCount += 1;
      return true;
    },
    stop() {},
    async stopAndWait() {},
    isRunning() {
      return false;
    },
  };
  const page = loadVoiceChatPage({
    accessibilityExports: {
      readAccessibilitySettings: () => ({
        largeTextEnabled: false,
        autoBargeInEnabled: false,
      }),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      autoBargeInEnabled: false,
      followUpLoading: true,
      followUpTTSLoading: false,
      followUpTTSPlaying: false,
      assistantPromptText: "那咱们接着聊朋友。",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.startAssistantBargeInMonitor.call(context, 0);

  assert.equal(startCount, 0);
  assert.equal(context.data.autoListenStateText, "可以点按钮回答");
});

test("voice chat checks recorder privacy before passive barge-in monitoring", async () => {
  let startCount = 0;
  const page = loadVoiceChatPage({
    wechatPrivacyExports: {
      ensureRecorderPrivacyAuthorization: async () => {
        throw new Error("privacy denied");
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceAuthorizationAgreed: true,
      followUpTTSPlaying: true,
    },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring() {
        startCount += 1;
        return true;
      },
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.startAssistantBargeInMonitor.call(context, 0);

  assert.equal(startCount, 0);
  assert.equal(context.data.autoListenStateText, "可以点按钮回答");
});

test("voice chat auto barge-in stops assistant before reconnecting a missing gateway client", async () => {
  let monitorOptions = null;
  const monitor = {
    running: false,
    start(options) {
      monitorOptions = options;
      this.running = true;
      return true;
    },
    stop() {
      this.running = false;
    },
    async stopAndWait() {
      this.stop();
    },
    isRunning() {
      return this.running;
    },
  };
  const cancelReasons = [];
  const reconnectCalls = [];
  const page = loadVoiceChatPage({
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      status: "idle",
      guidanceMode: "guided_interview",
      followUpLoading: true,
      followUpTTSLoading: false,
      followUpTTSPlaying: false,
      followUpTTSStateText: "小余正在说",
      assistantPromptText: "那咱们接着聊工作。",
      transcriptLines: [
        { id: "user-line-1", role: "user", text: "我想聊聊工作。" },
        { id: "assistant-stream-1", role: "assistant", text: "那咱们接着聊工作。" },
      ],
    },
    assistantStreamLineId: "assistant-stream-1",
    assistantTurnRuntime: {
      cancel(reason) {
        cancelReasons.push(reason);
      },
      stopPlayback() {},
      stats() {
        return null;
      },
    },
    audioPlayback: {
      currentToken: () => 1,
      currentAudio: () => null,
      isCurrentToken: () => true,
      nextToken: () => 1,
      stop() {},
    },
    voiceStreaming: {
      isBargeInMonitoring: () => monitor.isRunning(),
      startBargeInMonitoring: (options) => monitor.start(options),
      stopBargeInMonitoring: () => monitor.stopAndWait(),
      hasClient: () => false,
      startRecording() {
        throw new Error("startRecording should wait for reconnect");
      },
      stopRecording() {},
      close() {},
    },
    async syncVoiceAuthorizationForSession() {
      return true;
    },
    async connectGateway() {
      reconnectCalls.push(false);
      return false;
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  page.prepareStreamingAudioPlayback.call(context, 0);
  await new Promise((resolve) => setImmediate(resolve));
  monitorOptions.onBargeIn();
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(cancelReasons, ["interrupted_by_user"]);
  assert.deepEqual(reconnectCalls, [false]);
  assert.equal(context.data.followUpTTSPlaying, false);
  assert.equal(context.data.autoListenStateText, "听到您说话了");
});

test("voice chat drives first streaming highlight from WebAudio playback progress", async () => {
  const progressCalls = [];
  const prompt = "欢迎回来，咱们接着聊。";
  const page = loadVoiceChatPage({
    streamingSpeechPlaybackExports: {
      StreamingSpeechPlaybackController: class {
        async appendFrame() {
          return {
            segmentIndex: 0,
            frameIndex: 0,
            durationSeconds: 2,
            startAtSeconds: 10,
            queuedAheadSeconds: 2,
            ended: Promise.resolve(),
          };
        }
        stats() {
          return {
            segmentCount: 1,
            underrunCount: 0,
            nextPlayTimeSeconds: 12,
            currentTimeSeconds: 10.5,
            queuedAheadSeconds: 1.5,
          };
        }
        async waitForDrain() {}
        stop() {}
      },
    },
    ttsTextProgressExports: {
      splitTextByAudioPlaybackProgress: (text, currentTime, duration) => {
        progressCalls.push({ text, currentTime, duration });
        return {
          spokenText: "欢迎",
          currentText: "回",
          remainingText: "来，咱们接着聊。",
          progressPercent: 25,
        };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      assistantPromptText: prompt,
      ttsSpokenText: prompt,
      followUpTTSPlaying: false,
    },
    audioPlayback: {
      currentToken: () => 1,
      isCurrentToken: () => true,
      nextToken: () => 1,
      stop() {},
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  context.assistantTurnRuntime = {
    stats() {
      return {
        segmentCount: 1,
        underrunCount: 0,
        nextPlayTimeSeconds: 12,
        currentTimeSeconds: 10.5,
        queuedAheadSeconds: 1.5,
      };
    },
  };
  page.prepareStreamingAudioPlayback.call(context, 0);
  page.markStreamingAudioFrameScheduled.call(context, {
    startAtSeconds: 10,
    durationSeconds: 2,
    ended: Promise.resolve(),
  });
  page.updateStreamingTTSProgress.call(context);
  page.stopFollowUpProgressTimer.call(context);

  assert.equal(context.data.followUpTTSPlaying, true);
  assert.equal(context.data.ttsSpokenText, "欢迎");
  assert.equal(context.data.ttsCurrentText, "回");
  assert.equal(context.data.ttsRemainingText, "来，咱们接着聊。");
  assert.equal(context.data.ttsProgressPercent, 25);
  assert.equal(progressCalls[0].text, prompt);
  assert.equal(progressCalls[0].currentTime, 0.5);
  assert.equal(progressCalls[0].duration, 2);
  assert.notEqual(context.data.ttsSpokenText, prompt);
});

for (const failureKind of ["playback", "provider"]) test(`voice chat keeps completed assistant text after ${failureKind} failure`, async () => {
  let playbackDestroyed = false;
  const voicePlayedCalls = [];
  const page = loadVoiceChatPage({
    voiceApiExports: {
      markMemoirAutomationEventsVoicePlayed: async (projectId, data) => {
        voicePlayedCalls.push({ projectId, data });
        return { updated_count: data.event_ids.length };
      },
    },
    assistantTurnStreamExports: {
      VoiceAssistantTurnStreamClient: class {
        constructor(voiceSessionId, callbacks) {
          this.callbacks = callbacks;
        }
        async start() {
          if (failureKind === "provider") this.callbacks.onAudioFailed?.({type: "audio.failed", error: "tts unavailable"});
          this.callbacks.onTextDelta?.({
            type: "text.delta",
            turn_id: "turn-1",
            text_delta: "那时候在院子里，外婆最常带着您做什么？",
            text: "那时候在院子里，外婆最常带着您做什么？",
          });
          if (failureKind === "playback") this.callbacks.onAudioFrame?.({
            turnId: "turn-1",
            sequence: 1,
            audio: new Uint8Array([1, 2, 3]),
            mimeType: "audio/L16",
            sampleRate: 16000,
            channels: 1,
            bitsPerSample: 16,
          });
          if (failureKind === "playback") this.callbacks.onAudioDone?.({
            type: "audio.done",
            turn_id: "turn-1",
            voice_session_id: "voice-1",
            frame_count: 1,
            duration_seconds: 0.2,
          });
          const completed = {
            type: "turn.completed",
            turn_id: "turn-1",
            state: "completed",
            project_id: "project-1",
            message_id: "assistant-question-1",
            audio_file_id: failureKind === "playback" ? "audio-question-1" : null,
            audio_status: failureKind === "playback" ? "ready" : "failed",
            text: "那时候在院子里，外婆最常带着您做什么？",
            primary_intent: "follow_up",
            intent_tags: ["follow_up"],
            expects_answer: true,
            progress_voice_selected_event_ids: ["event-1"],
          };
          this.callbacks.onCompleted?.(completed);
          return completed;
        }
        cancel() {}
        close() {}
      },
    },
    streamingSpeechPlaybackExports: {
      StreamingSpeechPlaybackController: class {
        async appendFrame() {
          throw new Error("pcm audio failed");
        }
        async waitForDrain() {}
        async destroy() {
          playbackDestroyed = true;
        }
        stop() {}
      },
    },
    voiceChatAutoListenExports: {
      canAutoStartListeningAfterTTS: () => true,
    },
  });
  const startCalls = [];
  const setDataPatches = [];
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      guidanceMode: "guided_interview",
      transcriptLines: [{
        id: "user-line-1",
        role: "user",
        text: "我小时候经常和外婆在院子里摘菜。",
        message_id: "user-message-1",
      }],
      lastFollowUpSourceMessageId: "previous-source",
      autoListenEnabled: true,
      status: "ready",
    },
    audioPlayback: {
      currentToken: () => 1,
      isCurrentToken: () => true,
      nextToken: () => 1,
      stop() {},
    },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},
      hasClient: () => true,
    },
    setData(patch) {
      setDataPatches.push(patch);
      this.data = { ...this.data, ...patch };
    },
    startRecording(autoTriggered) {
      startCalls.push(autoTriggered);
      return true;
    },
  };

  await page.generateFollowUp.call(context, false);

  assert.equal(context.data.followUpError, "");
  assert.equal(context.data.followUpTTSError, failureKind === "playback" ? "pcm audio failed" : "tts unavailable");
  assert.equal(context.data.assistantPromptText, "那时候在院子里，外婆最常带着您做什么？");
  assert.equal(context.data.followUpTTSStateText, "语音播放失败，可重播");
  assert.equal(context.data.persistenceStateText, "语音播放失败");
  assert.equal(context.data.followUpMessageId, "assistant-question-1");
  assert.equal(context.data.followUpAudioFileId, failureKind === "playback" ? "audio-question-1" : "");
  assert.equal(context.data.lastFollowUpSourceMessageId, "");
  assert.equal(playbackDestroyed, failureKind === "playback");
  assert.deepEqual(startCalls, []);
  assert.deepEqual(voicePlayedCalls, []);
  assert.equal(
    setDataPatches.some((patch) => patch.followUpTTSStateText === "正在整理回应"),
    false,
  );
});

test("voice chat clears temporary streaming assistant state when the turn fails", async () => {
  const page = loadVoiceChatPage({
    assistantTurnStreamExports: {
      VoiceAssistantTurnStreamClient: class {
        constructor(voiceSessionId, callbacks) {
          this.callbacks = callbacks;
        }
        async start() {
          this.callbacks.onStarted?.({ type: "turn.started", turn_id: "turn-1" });
          this.callbacks.onTextDelta?.({
            type: "text.delta",
            turn_id: "turn-1",
            text_delta: "好，那咱们就聊聊朋友。",
            text: "好，那咱们就聊聊朋友。",
          });
          throw new Error("语音合成服务暂时不可用，请稍后重试。");
        }
        cancel() {}
        close() {}
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      guidanceMode: "guided_interview",
      transcriptLines: [{
        id: "user-line-1",
        role: "user",
        text: "今天我想聊聊朋友吧，不太想讲工作了。",
        message_id: "user-message-1",
      }],
      followUpTTSPlaying: true,
      ttsSpokenText: "好，那咱们就聊聊朋友。",
      ttsProgressPercent: 18,
      lastFollowUpSourceMessageId: "previous-source",
    },
    audioPlayback: {
      currentToken: () => 1,
      currentAudio: () => null,
      isCurrentToken: () => true,
      nextToken: () => 2,
      stop() {},
    },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},
      hasClient: () => true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.generateFollowUp.call(context, false);

  assert.deepEqual(context.data.transcriptLines.map((line) => line.id), ["user-line-1"]);
  assert.equal(context.data.assistantPromptText, "");
  assert.equal(context.data.followUpMessageId, "");
  assert.equal(context.data.followUpAudioFileId, "");
  assert.equal(context.data.followUpError, "语音合成服务暂时不可用，请稍后重试。");
  assert.equal(context.data.followUpTTSPlaying, false);
  assert.equal(context.data.followUpTTSLoading, false);
  assert.equal(context.data.followUpTTSStateText, "");
  assert.equal(context.data.ttsProgressPercent, 0);
  assert.equal(context.data.persistenceStateText, "回应失败");
});

test("voice chat replays an existing assistant audio file without regenerating TTS", async () => {
  const played = [];
  const generated = [];
  const page = loadVoiceChatPage({
    voiceApiExports: {
      createMemoirAssistantTTS: async (messageId) => {
        generated.push(messageId);
        return {
          audio_file_id: "new-audio",
          message_id: messageId,
          text: "new",
          metadata_json: {},
        };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      followUpAudioFileId: "audio-question-1",
      followUpMessageId: "assistant-question-1",
      assistantPromptText: "那时候在院子里，外婆最常带着您做什么？",
      ttsAlignment: [],
    },
    audioPlayback: {
      nextToken: () => 1,
      currentToken: () => 1,
      currentAudio: () => null,
      isCurrentToken: () => true,
      stop() {},
      playFile: async (audioFileId, token, options) => {
        played.push({ audioFileId, token });
        options.onEnded?.();
      },
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.playFollowUpAudio.call(context);

  assert.deepEqual(played, [{ audioFileId: "audio-question-1", token: 1 }]);
  assert.deepEqual(generated, []);
});

test("voice chat reuses streaming audio duration for the first replay highlight", async () => {
  const prompt = "那时候在院子里，外婆最常带着您做什么？";
  const progressCalls = [];
  const page = loadVoiceChatPage({
    ttsTextProgressExports: {
      splitTextByAudioPlaybackProgress: (text, currentTime, duration) => {
        progressCalls.push({ text, currentTime, duration });
        if (currentTime <= 0) {
          return {
            spokenText: "",
            currentText: "",
            remainingText: text,
            progressPercent: 0,
          };
        }
        return {
          spokenText: "那时候",
          currentText: "在",
          remainingText: "院子里，外婆最常带着您做什么？",
          progressPercent: 25,
        };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      followUpAudioFileId: "audio-question-1",
      followUpMessageId: "assistant-question-1",
      assistantPromptText: prompt,
      ttsAlignment: [],
      ttsSpokenText: prompt,
      ttsProgressPercent: 100,
    },
    audioPlayback: {
      nextToken: () => 1,
      currentToken: () => 1,
      currentAudio: () => null,
      isCurrentToken: () => true,
      stop() {},
      playFile: async (_audioFileId, _token, options) => {
        const audio = { currentTime: 1, duration: 0 };
        options.onPlayRequested?.(audio);
        options.onTimeUpdate?.(audio);
        assert.equal(context.data.ttsSpokenText, "那时候");
        assert.equal(context.data.ttsCurrentText, "在");
        assert.equal(context.data.ttsProgressPercent, 25);
        options.onEnded?.();
      },
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };
  context.ttsProgress.setStreamingDurationSeconds(4);

  await page.playFollowUpAudio.call(context);
  page.stopFollowUpProgressTimer.call(context);

  assert.equal(progressCalls.some((call) => call.currentTime === 1 && call.duration === 4), true);
  assert.equal(progressCalls[0].text, prompt);
});

test("voice chat caps streaming playback progress before the completed turn state", () => {
  const page = loadVoiceChatPage();

  assert.equal(page.streamingProgressPercent("欢迎回来", "欢迎回来，咱们接着聊。", false), 36);
  assert.equal(page.streamingProgressPercent("欢迎回来，咱们接着聊。", "欢迎回来，咱们接着聊。", false), 99);
  assert.equal(page.streamingProgressPercent("欢迎回来，咱们接着聊。", "欢迎回来，咱们接着聊。", true), 100);
});

test("voice chat passes regenerate flag to streaming assistant turn", async () => {
  const streamStarts = [];
  const page = loadVoiceChatPage({
    assistantTurnStreamExports: {
      VoiceAssistantTurnStreamClient: class {
        constructor(voiceSessionId, callbacks) {
          this.voiceSessionId = voiceSessionId;
          this.callbacks = callbacks;
        }
        async start(options) {
          streamStarts.push(options);
          const completed = {
            type: "turn.completed",
            turn_id: "turn-1",
            state: "completed",
            message_id: "assistant-question-1",
            audio_file_id: "",
            text: "换个说法问：那件事大概发生在什么时候？",
            primary_intent: "follow_up",
            intent_tags: ["follow_up"],
            expects_answer: true,
          };
          this.callbacks.onTextDelta?.({ type: "text.delta", text: completed.text });
          this.callbacks.onCompleted?.(completed);
          return completed;
        }
        cancel() {}
        close() {}
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      guidanceMode: "guided_interview",
      transcriptLines: [{
        id: "user-line-1",
        role: "user",
        text: "记得，不太多。",
        message_id: "user-message-1",
      }],
      lastFollowUpSourceMessageId: "",
    },
    audioPlayback: {
      currentToken: () => 1,
      isCurrentToken: () => true,
      nextToken: () => 1,
      stop() {},
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.generateFollowUp.call(context, true);

  assert.deepEqual(streamStarts, [{ regenerate: true }]);
  assert.equal(context.data.followUpMessageId, "assistant-question-1");
});

test("voice chat asks with AI voice when auto-listen hears no answer", async () => {
  const noAnswerRequests = [];
  const queuedTurns = [];
  const page = loadVoiceChatPage({
    voiceApiExports: {
      createMemoirVoiceNoAnswerPrompt: async (voiceSessionId, data) => {
        noAnswerRequests.push({ voiceSessionId, data });
        return {
          follow_up_text: "我这边没有听到声音，您可以接着刚才的问题慢慢说。",
          primary_intent: "repair",
          intent_tags: ["repair"],
          expects_answer: true,
          message: {
            id: "assistant-no-answer-1",
            role: "assistant",
            content_text: "我这边没有听到声音，您可以接着刚才的问题慢慢说。",
            audio_file_id: "",
            transcript_version_id: "",
            metadata_json: { source: "ai.no_answer_prompt", attempt: 1 },
          },
        };
      },
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      autoStopEnabled: true,
      autoListenStateText: "小余说完，正在听您说",
      persistenceStateText: "等待识别",
      primaryActionText: "暂停收音",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    enqueueAssistantTurn(item) {
      queuedTurns.push(item);
      this.setData({
        assistantPromptText: item.text,
        transcriptLines: [...this.data.transcriptLines, item.line],
      });
    },
  };

  page.handleCaptureStats.call(context, {
    state: "stopping",
    frameCount: 2,
    audioBytesSent: 6400,
    recordingElapsedMs: 15000,
    gatewayState: "recording",
    audioLevel: 0,
    silentElapsedMs: 15000,
    autoStopped: true,
    autoStopReason: "no_voice",
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(noAnswerRequests, [{ voiceSessionId: "voice-1", data: { attempt: 1 } }]);
  assert.deepEqual(queuedTurns.map((item) => ({
    messageId: item.messageId,
    autoListenAfterPlayback: item.autoListenAfterPlayback,
    expectsAnswer: item.expectsAnswer,
    text: item.text,
  })), [{
    messageId: "assistant-no-answer-1",
    autoListenAfterPlayback: true,
    expectsAnswer: true,
    text: "我这边没有听到声音，您可以接着刚才的问题慢慢说。",
  }]);
  assert.equal(context.data.assistantPromptText, "我这边没有听到声音，您可以接着刚才的问题慢慢说。");
  assert.equal(context.data.autoListenStateText, "小余问完会继续听");
  assert.equal(context.data.persistenceStateText, "小余正在确认");
  assert.equal(context.consecutiveNoAnswerPrompts, 1);

  page.handleCaptureStats.call(context, {
    state: "idle",
    frameCount: 2,
    audioBytesSent: 6400,
    recordingElapsedMs: 15000,
    gatewayState: "ready",
    audioLevel: 0,
    silentElapsedMs: 15000,
    autoStopped: true,
    autoStopReason: "no_voice",
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(noAnswerRequests.length, 1);
  assert.equal(context.data.autoListenStateText, "小余问完会继续听");
  assert.equal(context.data.persistenceStateText, "小余正在确认");
});

test("voice chat ignores stale no-answer prompt after manual recording resumes", async () => {
  let resolvePrompt = () => {};
  const noAnswerRequests = [];
  const queuedTurns = [];
  const startOptions = [];
  const noAnswerPrompt = new Promise((resolve) => {
    resolvePrompt = () => resolve({
      follow_up_text: "我这边没有听到声音，您可以接着刚才的问题慢慢说。",
      primary_intent: "repair",
      intent_tags: ["repair"],
      expects_answer: true,
      message: {
        id: "assistant-no-answer-stale",
        role: "assistant",
        content_text: "我这边没有听到声音，您可以接着刚才的问题慢慢说。",
        audio_file_id: "",
        transcript_version_id: "",
        metadata_json: { source: "ai.no_answer_prompt", attempt: 1 },
      },
    });
  });
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}

        async connect() {}
        close() {}
        startRecording(options) {
          startOptions.push(options);
          return true;
        }
        stopRecording() {}
        configureAutoStop() {}
        async finishSession() {}
      },
    },
    voiceApiExports: {
      createMemoirVoiceNoAnswerPrompt: async (voiceSessionId, data) => {
        noAnswerRequests.push({ voiceSessionId, data });
        return noAnswerPrompt;
      },
    },
    voiceAuthorizationExports: {
      readVoiceAuthorizationConsent: () => true,
      syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    },
  });
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      voiceAuthorizationAgreed: true,
      status: "ready",
      autoStopEnabled: true,
      autoListenStateText: "小余说完，正在听您说",
      persistenceStateText: "等待识别",
      primaryActionText: "暂停收音",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    enqueueAssistantTurn(item) {
      queuedTurns.push(item);
    },
  };

  await page.connectGateway.call(context);
  page.handleCaptureStats.call(context, {
    state: "stopping",
    frameCount: 2,
    audioBytesSent: 6400,
    recordingElapsedMs: 15000,
    gatewayState: "recording",
    audioLevel: 0,
    silentElapsedMs: 15000,
    autoStopped: true,
    autoStopReason: "no_voice",
  });
  assert.equal(noAnswerRequests.length, 1);
  assert.equal(context.data.followUpLoading, true);

  await page.startRecordingAfterPrivacy.call(context, false);
  assert.equal(context.data.followUpLoading, false);
  assert.equal(context.consecutiveNoAnswerPrompts, 0);
  assert.equal(context.noAnswerPromptInFlight, false);
  resolvePrompt();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(queuedTurns.length, 0);
  assert.equal(context.data.transcriptLines.length, 0);
  assert.equal(context.data.assistantPromptText, "");
  assert.equal(context.data.primaryActionText, "暂停收音");
  assert.equal(startOptions.at(-1).noVoiceActivityTimeoutMs, 0);
});

test("voice chat pauses after the second consecutive no-answer prompt", async () => {
  const noAnswerRequests = [];
  const queuedTurns = [];
  const page = loadVoiceChatPage({
    voiceApiExports: {
      createMemoirVoiceNoAnswerPrompt: async (voiceSessionId, data) => {
        noAnswerRequests.push({ voiceSessionId, data });
        return {
          follow_up_text: "没关系，我先暂停一下。您准备好了，再点继续说话。",
          primary_intent: "repair",
          intent_tags: ["repair"],
          expects_answer: false,
          message: {
            id: "assistant-no-answer-2",
            role: "assistant",
            content_text: "没关系，我先暂停一下。您准备好了，再点继续说话。",
            audio_file_id: "",
            transcript_version_id: "",
            metadata_json: { source: "ai.no_answer_prompt", attempt: 2 },
          },
        };
      },
    },
  });
  const context = {
    ...page,
    consecutiveNoAnswerPrompts: 1,
    data: {
      ...page.data,
      voiceSessionId: "voice-1",
      autoStopEnabled: true,
      autoListenStateText: "小余说完，正在听您说",
      persistenceStateText: "等待识别",
      primaryActionText: "暂停收音",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    enqueueAssistantTurn(item) {
      queuedTurns.push(item);
      this.setData({
        assistantPromptText: item.text,
        transcriptLines: [...this.data.transcriptLines, item.line],
      });
    },
  };

  page.handleCaptureStats.call(context, {
    state: "stopping",
    frameCount: 2,
    audioBytesSent: 6400,
    recordingElapsedMs: 15000,
    gatewayState: "recording",
    audioLevel: 0,
    silentElapsedMs: 15000,
    autoStopped: true,
    autoStopReason: "no_voice",
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(noAnswerRequests, [{ voiceSessionId: "voice-1", data: { attempt: 2 } }]);
  assert.deepEqual(queuedTurns.map((item) => ({
    messageId: item.messageId,
    autoListenAfterPlayback: item.autoListenAfterPlayback,
    expectsAnswer: item.expectsAnswer,
    text: item.text,
  })), [{
    messageId: "assistant-no-answer-2",
    autoListenAfterPlayback: false,
    expectsAnswer: false,
    text: "没关系，我先暂停一下。您准备好了，再点继续说话。",
  }]);
  assert.equal(context.data.assistantPromptText, "没关系，我先暂停一下。您准备好了，再点继续说话。");
  assert.equal(context.data.autoListenStateText, "已暂停，可点继续说话");
  assert.equal(context.data.persistenceStateText, "已暂停收音");
  assert.equal(context.consecutiveNoAnswerPrompts, 2);
});

test("voice progress rejects responses after hide and shows terminal failures without endless polling", async () => {
  let resolve;
  const page = loadVoiceChatPage({ voiceApiExports: {
    getMemoirDraftProgress: () => new Promise((done) => { resolve = done; }),
  } });
  const context = { ...page, data: { ...page.data, projectId: "project-1", voiceSessionId: "voice-1", storyGrowthNoticeState: "pending" },
    setData(patch) { this.data = { ...this.data, ...patch }; } };
  const pending = context.watchStoryGrowth(context.storyGrowthWatchToken);
  context.pageSuspended = true;
  context.stopStoryGrowthWatch();
  resolve({ status: "ready", title: "可读草稿已更新", detail: "" });
  await pending;
  assert.equal(context.data.storyGrowthNoticeState, "pending");
  assert.equal(context.storyGrowthTimer.pending, false);
  context.pageSuspended = false;
  const failed = context.watchStoryGrowth(context.storyGrowthWatchToken);
  resolve({ status: "failed", title: "本次整理需要继续处理", detail: "模型超时" });
  await failed;
  assert.equal(context.data.storyGrowthNoticeState, "failed");
  assert.equal(context.data.storyGrowthNoticeDetail, "模型超时");
  assert.equal(context.storyGrowthTimer.pending, false);
});

test("voice progress continues beyond the former 48 polls and terminates on no change", async () => {
  let calls = 0;
  const page = loadVoiceChatPage({ voiceApiExports: { getMemoirDraftProgress: async () => ({
    status: ++calls > 50 ? "no_change" : "processing", title: "讲述已保存", detail: "故事结构没有新增",
  }) } });
  const context = { ...page, data: { ...page.data, projectId: "project-1", voiceSessionId: "voice-1" },
    setData(patch) { this.data = { ...this.data, ...patch }; } };
  const { PollingTimer } = require("../services/polling-timer.ts");
  let next;
  let serial = 0;
  context.storyGrowthTimer = new PollingTimer(1, {
    schedule(callback) { next = callback; return ++serial; }, cancel() { next = null; },
  });
  await context.watchStoryGrowth(context.storyGrowthWatchToken);
  while (next) { const callback = next; next = null; callback(); await flushAsyncWork(); }
  assert.equal(calls, 51);
  assert.equal(context.data.storyGrowthNoticeState, "no_change");
  assert.equal(context.storyGrowthTimer.pending, false);
});

test("voice page starts progress notifications for an active session before finish is pressed", async () => {
  const page = loadVoiceChatPage({ voiceApiExports: {
    listMemoirAutomationEvents: async () => ({ items: [{ id: "growth-1", event_type: "story_structure_updated", status: "completed", summary_text: "故事树新长出了1个新章节", metadata_json: {} }] }),
  } });
  const context = { ...page, data: { ...page.data, voiceSessionId: "voice-1" },
    setData(patch) { this.data = { ...this.data, ...patch }; } };
  context.applyVoiceSessionDetail({ session: { id: "voice-1", project_id: "project-1", status: "listening", guidance_mode: "free_narration" }, messages: [], utterances: [] });
  await flushAsyncWork();
  assert.equal(context.data.progressNoticeDetail, "故事树新长出了1个新章节");
  assert.notEqual(context.data.status, "closed");
  context.progressNoticeWatcher?.stop();
});


test("failed saving and finishing in the background never navigate away or claim new growth", async () => {
  for (const suspended of [false, true]) {
    const routes = [], errors = [];
    global.wx = { reLaunch: options => routes.push(options.url) };
    const page = loadVoiceChatPage();
    const context = { ...page, pageSuspended: suspended,
      data: { ...page.data, projectId:"project-1", voiceSessionId:"voice-1" },
      setData(patch) { this.data = {...this.data, ...patch}; },
      voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},hasClient: () => true, finishSession: async () => { if (!suspended) throw Error("音频尚未保存"); }},
      refreshVoiceSessionSnapshot: async () => {},
      stopCompletionIssueRefresh() {}, cancelFollowUpTTS() {},
      handleError: message => errors.push(message),
    };
    await context.finishSession();
    assert.deepEqual(routes, []);
    assert.deepEqual(context.events, []);
    assert.equal(context.data.finishingSession, false);
    if (!suspended) {
      assert.deepEqual(errors, ["音频尚未保存"]);
      assert.notEqual(context.data.persistenceStateText, "已保存");
    }
  }
});


test("a closed voice client exits only after the persisted session reaches a terminal state", async () => {
  for (const status of [null, "active", "stopped", "failed"]) {
    const routes = [], errors = [], toasts = [];
    global.wx = {
      reLaunch: options => routes.push(options.url),
      showToast: options => toasts.push(options.title),
    };
    const page = loadVoiceChatPage();
    const context = { ...page, pageSuspended: false,
      data: { ...page.data, projectId:"project-1", voiceSessionId:"voice-1" },
      setData(patch) { this.data = {...this.data, ...patch}; },
      voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},hasClient: () => false},
      refreshVoiceSessionSnapshot: async () => status ? {session:{status}, utterances:[]} : null,
      stopCompletionIssueRefresh() {}, cancelFollowUpTTS() {},
      handleError: message => errors.push(message),
    };
    await context.finishSession();
    assert.equal(context.data.finishingSession, false);
    if (status === "stopped") {
      assert.deepEqual(routes, []);
      assert.deepEqual(context.events, [{ name: "exit", detail: { destination: "story", projectId: "project-1", narrationSaved: false, finished: true, voiceSessionId: "voice-1" } }]);
      assert.equal(context.data.persistenceStateText, "已保存");
    } else if (status === "failed") {
      assert.deepEqual(context.events, [{ name: "exit", detail: { destination: "transcripts", voiceSessionId: "voice-1" } }]);
      assert.deepEqual(toasts, ["本次保存异常，请核对记录"]);
      assert.deepEqual(errors, []);
    } else {
      assert.deepEqual(routes, []);
      assert.notEqual(context.data.persistenceStateText, "已保存");
      assert.equal(errors.length, 1);
    }
  }
});

test("finished audio-only pending utterances preserve saved audio without claiming narration growth", async () => {
  for (const [raw, edited, narrationSaved] of [
    ["", "", false],
    [" \n", "\t", false],
    ["", "校正后的讲述", true],
    ["已识别的讲述", " ", true],
  ]) {
    const utterance = {
      id: "pending-utterance",
      audio_file_id: "original-audio",
      transcript_raw: raw,
      transcript_edited: edited,
      provider_payload_json: { status: "pending" },
    };
    global.wx = {};
    const page = loadVoiceChatPage();
    const context = {
      ...page,
      data: { ...page.data, projectId: "project-1", voiceSessionId: "voice-1" },
      setData(patch) { this.data = { ...this.data, ...patch }; },
      voiceStreaming: {
        isBargeInMonitoring: () => false,
        startBargeInMonitoring: () => false,
        async stopBargeInMonitoring() {},
        hasClient: () => false,
      },
      refreshVoiceSessionSnapshot: async () => ({ session: { status: "stopped" }, utterances: [utterance] }),
      stopCompletionIssueRefresh() {},
      cancelFollowUpTTS() {},
    };

    await context.finishSession();

    assert.equal(context.events[0].detail.narrationSaved, narrationSaved);
    assert.equal(context.events[0].detail.finished, true);
    assert.equal(context.data.persistenceStateText, "已保存");
    assert.equal(utterance.audio_file_id, "original-audio");
  }
});

test("ASR failure during finish exits to saved records when server confirms failed", async () => {
  const toasts = [];
  global.wx = { showToast: options => toasts.push(options.title) };
  const page = loadVoiceChatPage();
  const context = {
    ...page,
    data: { ...page.data, projectId: "project-1", voiceSessionId: "voice-1" },
    setData(patch) { this.data = { ...this.data, ...patch }; },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},
      hasClient: () => true,
      finishSession: async () => { throw Error("ASR 超时"); },
    },
    refreshVoiceSessionSnapshot: async () => ({ session: { status: "failed" }, utterances: [{ audio_file_id: "audio-1" }] }),
    stopCompletionIssueRefresh() {}, cancelFollowUpTTS() {},
  };

  await context.finishSession();

  assert.deepEqual(context.events, [{ name: "exit", detail: { destination: "transcripts", voiceSessionId: "voice-1" } }]);
  assert.deepEqual(toasts, ["本次保存异常，请核对记录"]);
  assert.equal(context.data.finishingSession, false);
  assert.notEqual(context.data.persistenceStateText, "已保存");
});

for (const status of ["created", "listening", "speaking"]) {
test(`a ${status} voice session stops through REST after the WebSocket is gone`, async () => {
  const stops = [];
  let refreshes = 0;
  const page = loadVoiceChatPage({
    voiceApiExports: {
      stopMemoirVoiceSession: async (sessionId) => stops.push(sessionId),
    },
  });
  const context = { ...page, pageSuspended: false,
    data: { ...page.data, projectId: "project-1", voiceSessionId: "voice-1" },
    setData(patch) { this.data = { ...this.data, ...patch }; },
    voiceStreaming: {
      isBargeInMonitoring: () => false,
      startBargeInMonitoring: () => false,
      async stopBargeInMonitoring() {},
      hasClient: () => false,
    },
    refreshVoiceSessionSnapshot: async () => ({
      session: { status: ++refreshes === 1 ? status : "stopped" },
      utterances: [{ transcript_raw: "哈哈哈哈哈哈", transcript_edited: "哈哈哈哈哈哈", audio_file_id: "audio-1" }],
    }),
    stopCompletionIssueRefresh() {},
    cancelFollowUpTTS() {},
  };

  await context.finishSession();

  assert.deepEqual(stops, ["voice-1"]);
  assert.equal(refreshes, 2);
  assert.equal(context.data.persistenceStateText, "已保存");
  assert.equal(context.events[0]?.detail.destination, "story");
  assert.equal(context.events[0]?.detail.narrationSaved, true);
});
}

test("a disconnected session stays on the page if REST stop fails or remains unconfirmed", async () => {
  for (const stopFails of [false, true]) {
    const stops = [], errors = [];
    const page = loadVoiceChatPage({ voiceApiExports: {
      stopMemoirVoiceSession: async (id) => {
        stops.push(id);
        if (stopFails) throw Error("结束请求失败");
      },
    } });
    const context = {
      ...page,
      data: { ...page.data, projectId: "project-1", voiceSessionId: "voice-1" },
      setData(patch) { this.data = { ...this.data, ...patch }; },
      voiceStreaming: { hasClient: () => false },
      refreshVoiceSessionSnapshot: async () => ({ session: { status: "listening" }, utterances: [] }),
      stopCompletionIssueRefresh() {}, cancelFollowUpTTS() {},
      handleError: message => errors.push(message),
    };

    await context.finishSession();

    assert.deepEqual(stops, ["voice-1"]);
    assert.deepEqual(context.events, []);
    assert.equal(context.data.finishingSession, false);
    assert.notEqual(context.data.persistenceStateText, "已保存");
    assert.equal(errors.length, 1);
  }
});

test("viewing records after disconnect exits only after the server confirms the session stopped", async () => {
  for (const status of [null, "active", "stopped"]) {
    const page = loadVoiceChatPage();
    const context = { ...page, pageSuspended: false,
      data: { ...page.data, voiceSessionId: "voice-1", canFinishAfterDisconnect: true,
        persistenceStateText: "已保存", recording: true },
      setData(patch) { this.data = { ...this.data, ...patch }; },
      voiceStreaming: { close() {} },
      refreshVoiceSessionSnapshot: async () => status ? { session: { status }, utterances: [] } : null,
    };

    await context.finishAfterDisconnect();

    assert.equal(context.data.finishingSession, false);
    assert.equal(context.data.recording, false);
    if (status === "stopped") {
      assert.equal(context.events[0]?.detail.destination, "transcripts");
      assert.equal(context.data.canFinishAfterDisconnect, false);
    } else {
      assert.deepEqual(context.events, []);
      assert.equal(context.data.canFinishAfterDisconnect, true);
      assert.match(context.data.error, /保存状态|尚未确认/);
      assert.equal(context.data.persistenceStateText, "保存状态待确认");
    }
  }
});

test("detaching a voice component during hydration cannot connect or update a later instance", async () => {
  let resolveSnapshot;
  let connects = 0;
  const screenRequests = [];
  global.wx = { setKeepScreenOn: ({ keepScreenOn }) => screenRequests.push(keepScreenOn) };
  const page = loadVoiceChatPage({
    voiceApiExports: { getMemoirVoiceSession: () => new Promise(resolve => { resolveSnapshot = resolve; }) },
    voiceAuthorizationExports: { readVoiceAuthorizationConsent: () => true },
    streamingExports: { MemoirStreamingAsrClient: class {
      isBargeInMonitoring() { return false; }
      startBargeInMonitoring() { return false; }
      async stopBargeInMonitoring() {}
 async connect() { connects += 1; } close() {} } },
  });
  const first = componentContext(page.definition);
  const second = componentContext(page.definition);
  assert.notEqual(first.runtime(), second.runtime());
  assert.notEqual(first.runtime().voiceStreaming, second.runtime().voiceStreaming);
  const pending = first.onLoad({ voiceSessionId: "old-session" });
  page.definition.lifetimes.detached.call(first);
  resolveSnapshot({ session: { id: "old-session", project_id: "old-project", status: "created" }, messages: [], utterances: [] });
  await pending;
  assert.equal(connects, 0);
  assert.equal(first.runtime().disposed, true);
  assert.equal(first.data.projectId, "");
  assert.equal(second.runtime().disposed, false);
  assert.equal(second.data.voiceSessionId, "");
  assert.deepEqual(first.events, []);
  assert.deepEqual(screenRequests, [true, false], "a late snapshot cannot restore wakefulness after leaving");
});

test("a consent response received after hiding the component cannot open its gateway", async () => {
  const page = loadVoiceChatPage();
  let resolveConsent;
  let connects = 0;
  const context = componentContext(page.definition, {}, { voiceSessionId: "voice-1" });
  context.syncVoiceAuthorizationForSession = () => new Promise(resolve => { resolveConsent = resolve; });
  context.connectGateway = async () => { connects += 1; return true; };
  const pending = context.syncVoiceAuthorizationAndConnect();
  page.definition.pageLifetimes.hide.call(context);
  resolveConsent(true);
  assert.equal(await pending, false);
  assert.equal(connects, 0);
});

test("a save confirmed while hidden reaches the host once on return", () => {
  const page = loadVoiceChatPage();
  const context = componentContext(page.definition, {}, { voiceSessionId: "s1", status: "closed" });
  context.runtime().pageSuspended = true;
  const detail = { destination: "story", projectId: "p1", narrationSaved: true, finished: true };
  context.exitSession(detail);
  assert.deepEqual(context.events, []);
  page.definition.pageLifetimes.show.call(context);
  page.definition.pageLifetimes.show.call(context);
  assert.deepEqual(context.events, [{ name: "exit", detail: { ...detail, voiceSessionId: "s1" } }]);
  page.definition.lifetimes.detached.call(context);
  context.exitSession(detail);
  assert.equal(context.events.length, 1);
});

test("capture failure during consent sync cannot reopen a truncated recording later", async () => {
  let releaseConsent;
  const consentReady = new Promise(resolve => { releaseConsent = resolve; });
  const page = loadVoiceChatPage({ voiceAuthorizationExports: { readVoiceAuthorizationConsent: () => true } });
  let starts = 0;
  const context = { ...page, data: { ...page.data, status: "ready", voiceAuthorizationAgreed: true },
    voiceStreaming: { hasClient: () => true, async stopBargeInMonitoring() {},
      startRecording() { starts++; return true; } },
    async syncVoiceAuthorizationForSession() { return consentReady; },
    setData(patch) { this.data = { ...this.data, ...patch }; },
  };
  assert.equal(page.startRecording.call(context), true);
  await Promise.resolve();
  page.handleError.call(context, "录音准备超时，请点按钮重新录音", { kind: "recorder" });
  const pending = context.recordingStartPromise;
  releaseConsent(true);
  assert.equal(await pending, false);
  assert.equal(starts, 0);
  assert.equal(context.data.recording, false);
});

test("revoking voice consent while recorder privacy is pending never starts server monitoring", async () => {
  let releasePrivacy;
  const privacyReady = new Promise(resolve => { releasePrivacy = resolve; });
  const page = loadVoiceChatPage({ wechatPrivacyExports: { ensureRecorderPrivacyAuthorization: () => privacyReady } });
  let starts = 0;
  const context = { ...page, data: { ...page.data, voiceAuthorizationAgreed: true, followUpTTSPlaying: true },
    voiceStreaming: { isBargeInMonitoring: () => false, startBargeInMonitoring() { starts++; return true; } },
    setData(patch) { this.data = { ...this.data, ...patch }; },
  };
  const pending = page.startAssistantBargeInMonitor.call(context, 0);
  context.data.voiceAuthorizationAgreed = false;
  releasePrivacy(); await pending;
  assert.equal(starts, 0);
});

test("late final transcript during finish must not start another assistant connection", async () => {
  const starts = [];
  const page = loadVoiceChatPage();
  const context = {
    ...page,
    data: {
      ...page.data,
      voiceSessionId: "finishing-voice",
      finishingSession: true,
      lastFollowUpSourceMessageId: "previous",
    },
    setData(patch) { this.data = { ...this.data, ...patch }; },
    confirmSavedTranscriptSnapshot: async () => {},
    startCompletionIssueVerification() {},
    async generateStreamingFollowUp() { starts.push(this.data.finishingSession); },
  };
  context.handleFinal({
    type: "asr.final", turn_final: true, text: "最后一句已保存。",
    message_id: "last-message", utterance_id: "last-utterance", audio_file_id: "last-audio",
  });
  await flushAsyncWork();
  assert.deepEqual(starts, []);
  assert.equal(context.data.transcriptLines.length, 1);
  assert.equal(context.data.transcriptLines[0].text, "最后一句已保存。");
  assert.equal(context.data.transcriptLines[0].message_id, "last-message");
});

for (const scenario of [
  { action: "listen", listens: 1, finishes: 0 },
  { action: "pause", listens: 0, finishes: 0 },
  { action: "end", listens: 0, finishes: 1 },
  { action: "end", playbackFails: true, listens: 0, finishes: 0 },
  { action: "end", interrupted: true, listens: 0, finishes: 0 },
  { action: "end", suspended: true, listens: 0, finishes: 0 },
]) {
  test(`spoken action waits for audio and respects cancellation: ${JSON.stringify(scenario)}`, async () => {
    let drain;
    const playback = new Promise(resolve => { drain = resolve; });
    const completed = {
      type: "turn.completed", voice_session_id: "voice-1", project_id: "project-1",
      message_id: "assistant-1", text: "好，今天就聊到这里。", primary_intent: "acknowledge",
      intent_tags: [], conversation_action: scenario.action, expects_answer: scenario.action === "listen",
    };
    const page = loadVoiceChatPage({
      assistantTurnStreamExports: {
        VoiceAssistantTurnStreamClient: class {
          constructor(_, callbacks) { this.callbacks = callbacks; }
          async start() {
            this.callbacks.onAudioFrame({});
            this.callbacks.onAudioDone({ duration_seconds: 1 });
            this.callbacks.onCompleted(completed);
            return completed;
          }
          cancel() {}
          close() {}
        },
      },
      streamingSpeechPlaybackExports: {
        StreamingSpeechPlaybackController: class {
          async appendFrame() { return null; }
          async waitForDrain() { await playback; if (scenario.playbackFails) throw Error("播放失败"); }
          stop() {}
          async destroy() {}
        },
      },
    });
    let listens = 0, finishes = 0;
    const context = {
      ...page, data: { ...page.data, voiceSessionId: "voice-1" },
      followUpRequestSerial: 1, autoListenPendingAfterPlayback: true,
      setData(patch) { this.data = { ...this.data, ...patch }; },
      prepareStreamingAudioPlayback() {}, stopAssistantBargeInMonitor() {}, stopFollowUpProgressTimer() {},
      markStreamingPlaybackError() {},
      async tryStartAutoListenAfterTTS() { listens += 1; },
      async finishSession() { finishes += 1; },
    };
    const pending = page.generateStreamingFollowUp.call(context, "user-1", false, 1);
    await flushAsyncWork();
    assert.equal(finishes, 0);
    assert.equal(listens, 0);
    if (scenario.interrupted) context.followUpRequestSerial += 1;
    if (scenario.suspended) context.pageSuspended = true;
    drain();
    await pending;
    assert.equal(finishes, scenario.finishes);
    assert.equal(listens, scenario.listens);
    if (!scenario.interrupted) {
      assert.equal(context.autoListenPendingAfterPlayback, scenario.action === "listen");
      assert.equal(context.data.transcriptLines.at(-1).metadata_json.conversation_action, scenario.action);
    }
  });
}

test("recorder configuration and automatic interruption evidence reach the session audit", t => {
  const page = loadVoiceChatPage();
  const audit = require("../services/audit-tracker.ts");
  const calls = [];
  t.mock.method(audit, "trackAuditAction", (...args) => calls.push(args));
  const configuration = { capturePurpose: "barge_in", platform: "ohos", wechatVersion: "8.0.22",
    sdkVersion: "3.17.3", nativeVoiceChatSupported: true, requestedAudioMode: "voiceChat" };
  const context = { data: { voiceSessionId: "voice-incident" } };
  const base = { frameCount: 0, audioBytesSent: 0, recordingElapsedMs: 0,
    autoStopped: false, autoStopReason: "" };
  page.handleClientEvent.call(context, { ...base, type: "recorder_start_requested",
    recorderConfiguration: configuration });
  page.handleClientEvent.call(context, { ...base, type: "barge_in_confirmed",
    bargeInStartMs: 0, retainedAudioMs: 856 });
  assert.deepEqual(calls.map(([feature, , options]) => [feature, options.voiceSessionId]),
    [["voice_chat.client_event", "voice-incident"], ["voice_chat.client_event", "voice-incident"]]);
  assert.deepEqual(calls[0][1].recorder_configuration, configuration);
  assert.equal(calls[1][1].barge_in_start_ms, 0);
  assert.equal(calls[1][1].retained_audio_ms, 856);
});
