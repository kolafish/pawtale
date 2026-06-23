require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function mockModule(relativePath, exports) {
  const modulePath = require.resolve(relativePath);
  delete require.cache[modulePath];
  require.cache[modulePath] = {
    id: modulePath,
    filename: modulePath,
    loaded: true,
    exports,
  };
}

function loadVoiceChatPage({
  streamingExports = {},
  voiceApiExports = {},
  memoirCacheExports = {},
  voiceAuthorizationExports = {},
  wechatPrivacyExports = {},
  voiceChatAutoListenExports = {},
} = {}) {
  const pagePath = require.resolve("../pages/voice-chat/index.ts");
  delete require.cache[pagePath];
  mockModule("../services/audio-playback.ts", {
    configureAudiblePlayback: () => {},
  });
  mockModule("../services/memoir-streaming-asr.ts", {
    classifyVoiceClientConnectionError: () => ({ kind: "unknown" }),
    MemoirStreamingAsrClient: streamingExports.MemoirStreamingAsrClient || class {
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
  mockModule("../services/memoir-voice-api.ts", {
    createMemoirAssistantTTS: async () => {
      throw new Error("createMemoirAssistantTTS was not mocked");
    },
    createMemoirVoiceFollowUp: async () => {
      throw new Error("createMemoirVoiceFollowUp was not mocked");
    },
    createMemoirVoiceNoAnswerPrompt: async () => {
      throw new Error("createMemoirVoiceNoAnswerPrompt was not mocked");
    },
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
    ...voiceApiExports,
  });
  mockModule("../services/tts-text-progress.ts", {
    splitTextByAudioPlaybackProgress: () => ({
      spokenText: "",
      currentText: "",
      remainingText: "",
      progressPercent: 0,
    }),
  });
  mockModule("../services/tts-settings.ts", {
    readChatTTSSettings: () => ({ voice: "", speedRatio: 1 }),
  });
  mockModule("../services/voice-chat-auto-listen.ts", {
    canAutoStartListeningAfterTTS: () => false,
    ...voiceChatAutoListenExports,
  });
  mockModule("../services/voice-chat-state.ts", {
    assistantPanelCopy: () => ({
      assistantPromptLabel: "AI 开场",
      assistantLoadingText: "正在准备第一句话",
      assistantEmptyText: "AI 会先说一句，帮你开始讲。",
    }),
    canRequestFollowUp: () => false,
    countTextChars: (value) => value.length,
    coveredFollowUpSourceMessageId: () => "",
    errorKindText: () => "",
    followUpSourceMessageId: () => "",
    formatDuration: () => "00:00",
    gatewayStateText: () => "识别未同步",
    latestAssistantMessageId: () => "",
    latestTranscriptText: () => "",
    modeLabel: (mode) => (mode === "guided_interview" ? "主题讲述" : "自由讲述"),
    statusText: (status) => status,
    totalTranscriptChars: () => 0,
    ttsAlignmentFromAssistantLine: () => [],
    ttsAlignmentFromMetadata: () => [],
    upsertTranscriptLine: (lines, line) => [...lines, line],
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
    readVoiceAuthorizationConsent: () => false,
    saveVoiceAuthorizationConsent: (agreed = true) => ({ agreed, version: "test", agreedAt: "now" }),
    syncVoiceAuthorizationConsent: async () => ({ id: "consent-1" }),
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
    ...voiceAuthorizationExports,
  });
  mockModule("../services/wechat-privacy.ts", {
    ensureRecorderPrivacyAuthorization: async () => {},
    wechatPrivacyErrorMessage: (message) => message || "微信隐私授权失败",
    ...wechatPrivacyExports,
  });

  let page = null;
  const previousPage = global.Page;
  global.Page = (definition) => {
    page = definition;
  };
  require(pagePath);
  if (previousPage === undefined) {
    delete global.Page;
  } else {
    global.Page = previousPage;
  }
  return page;
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
  assert.match(context.data.error, /声纹授权协议/);
  assert.equal(context.data.autoListenStateText, "自动收音已暂停，等待声纹授权");
  assert.equal(toasts[0].title, "请先同意声纹授权协议");
  delete global.wx;
});

test("voice chat does not connect a stopped voice session", async () => {
  let connectCalls = 0;
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
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
  assert.equal(context.data.primaryActionText, "已结束");
  assert.equal(context.data.persistenceStateText, "已保存");
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

  assert.equal(context.data.connecting, false);
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
  assert.equal(context.data.connecting, false);
  assert.equal(context.data.status, "ready");
});

test("voice chat retries gateway connection after a failed connect", async () => {
  let connectCalls = 0;
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
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
          throw new Error("startRecording should wait for reconnect");
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
      connecting: false,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };

  await page.connectGateway.call(context);
  assert.equal(connectCalls, 1);
  assert.equal(context.data.connecting, false);
  assert.equal(context.data.status, "error");

  const started = page.startRecording.call(context, false);
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(started, false);
  assert.equal(connectCalls, 2);
  assert.equal(context.data.connecting, false);
  assert.equal(context.data.status, "ready");
});

test("voice chat finishSession ignores duplicate finish taps", async () => {
  let finishCalls = 0;
  let resolveFinish = () => {};
  const modals = [];
  global.wx = {
    showModal(options) {
      modals.push(options);
    },
  };
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
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
          status: "stopped",
          guidance_mode: "free_narration",
          current_guidance_module: "",
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
  assert.equal(modals.length, 1);
  delete global.wx;
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
      connecting: false,
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

test("voice chat uses no-answer timeout only for auto-listen recordings", async () => {
  const startOptions = [];
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
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
  assert.equal(startOptions[0].noVoiceActivityTimeoutMs, 15000);
  assert.equal(startOptions[1].noVoiceActivityTimeoutMs, 0);
  assert.equal(context.data.primaryActionText, "暂停收音");
});

test("voice chat starts auto listening after AI audio playback ends when gateway is ready", async () => {
  const startCalls = [];
  installAutoEndingAudioContext();
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
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
        && !state.connecting
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
    startRecording(autoTriggered) {
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
  delete global.wx;
});

test("voice chat starts pending auto listening when gateway becomes ready after playback", async () => {
  const startCalls = [];
  let releaseConnection = () => {};
  installAutoEndingAudioContext();
  const page = loadVoiceChatPage({
    streamingExports: {
      MemoirStreamingAsrClient: class {
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
        && !state.connecting
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
    startRecording(autoTriggered) {
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
  delete global.wx;
});

test("voice chat asks with AI voice when auto-listen hears no answer", async () => {
  const noAnswerRequests = [];
  const preparedTTS = [];
  const page = loadVoiceChatPage({
    voiceApiExports: {
      createMemoirVoiceNoAnswerPrompt: async (voiceSessionId, data) => {
        noAnswerRequests.push({ voiceSessionId, data });
        return {
          follow_up_text: "我这边没有听到声音，您可以接着刚才的问题慢慢说。",
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
      autoListenStateText: "AI 说完，正在听你说",
      persistenceStateText: "等待识别",
      primaryActionText: "暂停收音",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    async prepareFollowUpTTS(messageId, forceRegenerate, autoListenAfterPlayback) {
      preparedTTS.push({ messageId, forceRegenerate, autoListenAfterPlayback });
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

  assert.equal(context.data.captureStateText, "正在收尾");
  assert.equal(context.data.autoStopStateText, "未听到回答，已暂停收音");
  assert.deepEqual(noAnswerRequests, [{ voiceSessionId: "voice-1", data: { attempt: 1 } }]);
  assert.deepEqual(preparedTTS, [{
    messageId: "assistant-no-answer-1",
    forceRegenerate: false,
    autoListenAfterPlayback: true,
  }]);
  assert.equal(context.data.assistantPromptText, "我这边没有听到声音，您可以接着刚才的问题慢慢说。");
  assert.equal(context.data.autoListenStateText, "AI 确认后会继续听");
  assert.equal(context.data.persistenceStateText, "AI 正在确认");
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
  assert.equal(context.data.autoListenStateText, "AI 确认后会继续听");
  assert.equal(context.data.persistenceStateText, "AI 正在确认");
});

test("voice chat ignores stale no-answer prompt after manual recording resumes", async () => {
  let resolvePrompt = () => {};
  const noAnswerRequests = [];
  const preparedTTS = [];
  const startOptions = [];
  const noAnswerPrompt = new Promise((resolve) => {
    resolvePrompt = () => resolve({
      follow_up_text: "我这边没有听到声音，您可以接着刚才的问题慢慢说。",
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
      autoListenStateText: "AI 说完，正在听你说",
      persistenceStateText: "等待识别",
      primaryActionText: "暂停收音",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    async prepareFollowUpTTS(messageId, forceRegenerate, autoListenAfterPlayback) {
      preparedTTS.push({ messageId, forceRegenerate, autoListenAfterPlayback });
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

  assert.equal(preparedTTS.length, 0);
  assert.equal(context.data.transcriptLines.length, 0);
  assert.equal(context.data.assistantPromptText, "");
  assert.equal(context.data.primaryActionText, "暂停收音");
  assert.equal(startOptions.at(-1).noVoiceActivityTimeoutMs, 0);
});

test("voice chat pauses after the second consecutive no-answer prompt", async () => {
  const noAnswerRequests = [];
  const preparedTTS = [];
  const page = loadVoiceChatPage({
    voiceApiExports: {
      createMemoirVoiceNoAnswerPrompt: async (voiceSessionId, data) => {
        noAnswerRequests.push({ voiceSessionId, data });
        return {
          follow_up_text: "没关系，我先暂停一下。您准备好了，再点继续说话。",
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
      autoListenStateText: "AI 说完，正在听你说",
      persistenceStateText: "等待识别",
      primaryActionText: "暂停收音",
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    async prepareFollowUpTTS(messageId, forceRegenerate, autoListenAfterPlayback) {
      preparedTTS.push({ messageId, forceRegenerate, autoListenAfterPlayback });
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
  assert.deepEqual(preparedTTS, [{
    messageId: "assistant-no-answer-2",
    forceRegenerate: false,
    autoListenAfterPlayback: false,
  }]);
  assert.equal(context.data.assistantPromptText, "没关系，我先暂停一下。您准备好了，再点继续说话。");
  assert.equal(context.data.autoListenStateText, "已暂停，可点继续说话");
  assert.equal(context.data.persistenceStateText, "已暂停收音");
  assert.equal(context.consecutiveNoAnswerPrompts, 2);
});
