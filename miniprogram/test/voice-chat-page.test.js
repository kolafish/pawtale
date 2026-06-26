require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { clearModule, loadPageModule, mockModule } = require("./page-harness");

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
} = {}) {
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
  clearModule("../services/streaming-voice-controller.ts");
  mockModule("../services/memoir-voice-api.ts", {
    createMemoirAssistantTTS: async () => {
      throw new Error("createMemoirAssistantTTS was not mocked");
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
  mockModule("../services/tts-settings.ts", {
    readChatTTSSettings: () => ({ voice: "", speedRatio: 1 }),
  });
  mockModule("../services/voice-chat-auto-listen.ts", {
    canAutoStartListeningAfterTTS: () => false,
    ...voiceChatAutoListenExports,
  });
  mockModule("../services/voice-chat-state.ts", {
    assistantPanelCopy: () => ({
      assistantPromptLabel: "老余开场",
      assistantLoadingText: "老余正在准备第一句话",
      assistantEmptyText: "老余会先说一句，帮你开始讲。",
    }),
    assistantTranscriptState: (lines, options = {}) => ({
      transcriptLines: lines,
      latestTranscriptText: lines.length ? lines[lines.length - 1].text : "",
      assistantPromptText: options.assistantPromptText || "",
      assistantPromptLabel: "老余开场",
      assistantLoadingText: "老余正在准备第一句话",
      assistantEmptyText: "老余会先说一句，帮你开始讲。",
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
    latestTranscriptText: () => "",
    modeLabel: (mode) => (mode === "guided_interview" ? "主题讲述" : "自由讲述"),
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

  return loadPageModule("../pages/voice-chat/index.ts").page;
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
  assert.equal(context.data.primaryActionText, "已保存");
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

test("voice chat reconnects on user start after a failed connect", async () => {
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

test("voice chat does not auto reconnect when recorder start is not ready", async () => {
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
      connecting: false,
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

test("voice chat streams one assistant turn and plays pcm audio frames", async () => {
  const streamStarts = [];
  const played = [];
  const playbackEvents = [];
  const page = loadVoiceChatPage({
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
      hasClient: () => true,
    },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
    startRecording(autoTriggered) {
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
      followUpTTSStateText: "老余正在说",
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

test("voice chat keeps completed streaming assistant turns when playback fails", async () => {
  let playbackDestroyed = false;
  const page = loadVoiceChatPage({
    assistantTurnStreamExports: {
      VoiceAssistantTurnStreamClient: class {
        constructor(voiceSessionId, callbacks) {
          this.callbacks = callbacks;
        }
        async start() {
          this.callbacks.onTextDelta?.({
            type: "text.delta",
            turn_id: "turn-1",
            text_delta: "那时候在院子里，外婆最常带着您做什么？",
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
            message_id: "assistant-question-1",
            audio_file_id: "audio-question-1",
            text: "那时候在院子里，外婆最常带着您做什么？",
            primary_intent: "follow_up",
            intent_tags: ["follow_up"],
            expects_answer: true,
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
  assert.equal(context.data.followUpTTSError, "pcm audio failed");
  assert.equal(context.data.followUpTTSStateText, "语音播放失败，可重播");
  assert.equal(context.data.persistenceStateText, "语音播放失败");
  assert.equal(context.data.followUpMessageId, "assistant-question-1");
  assert.equal(context.data.followUpAudioFileId, "audio-question-1");
  assert.equal(context.data.lastFollowUpSourceMessageId, "");
  assert.equal(playbackDestroyed, true);
  assert.deepEqual(startCalls, []);
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
    assistantStreamingAudioDurationSeconds: 4,
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
      autoListenStateText: "AI 说完，正在听你说",
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

  assert.equal(context.data.captureStateText, "正在收尾");
  assert.equal(context.data.autoStopStateText, "未听到回答，已暂停收音");
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
  assert.equal(context.data.autoListenStateText, "老余确认后会继续听");
  assert.equal(context.data.persistenceStateText, "老余正在确认");
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
  assert.equal(context.data.autoListenStateText, "老余确认后会继续听");
  assert.equal(context.data.persistenceStateText, "老余正在确认");
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
      autoListenStateText: "AI 说完，正在听你说",
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
