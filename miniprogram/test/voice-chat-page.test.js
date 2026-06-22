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
  voiceAuthorizationExports = {},
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
    readMemoirVoiceSessionSnapshot: () => ({ hasSession: false }),
    transcriptLinesFromVoiceSessionDetail: () => [],
    writeMemoirVoiceSessionSnapshot: () => {},
  });
  mockModule("../services/voice-authorization.ts", {
    readVoiceAuthorizationConsent: () => false,
    saveVoiceAuthorizationConsent: (agreed = true) => ({ agreed, version: "test", agreedAt: "now" }),
    VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
    ...voiceAuthorizationExports,
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
