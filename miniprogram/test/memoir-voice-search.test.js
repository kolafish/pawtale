require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadVoiceSearchService({ voiceApiExports = {}, privacyExports = {}, authorizationExports = {} } = {}) {
  const voiceApiPath = require.resolve("../services/memoir-voice-api.ts");
  const privacyPath = require.resolve("../services/wechat-privacy.ts");
  const authorizationPath = require.resolve("../services/voice-authorization.ts");
  const voiceSearchPath = require.resolve("../services/memoir-voice-search.ts");
  delete require.cache[voiceApiPath];
  delete require.cache[privacyPath];
  delete require.cache[authorizationPath];
  delete require.cache[voiceSearchPath];
  require.cache[voiceApiPath] = {
    id: voiceApiPath,
    filename: voiceApiPath,
    loaded: true,
    exports: {
      transcribeMemoirVoiceSearchAudio: async () => {
        throw new Error("transcribeMemoirVoiceSearchAudio was not mocked");
      },
      ...voiceApiExports,
    },
  };
  require.cache[privacyPath] = {
    id: privacyPath,
    filename: privacyPath,
    loaded: true,
    exports: {
      ensureRecorderPrivacyAuthorization: async () => {},
      wechatPrivacyErrorMessage: (message) => message || "录音失败",
      ...privacyExports,
    },
  };
  require.cache[authorizationPath] = {
    id: authorizationPath,
    filename: authorizationPath,
    loaded: true,
    exports: {
      readVoiceAuthorizationConsent: () => true,
      syncVoiceAuthorizationConsent: async () => ({}),
      VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index",
      ...authorizationExports,
    },
  };
  return require(voiceSearchPath);
}

async function flushAsyncSetup() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

test("voice search service records PCM audio and returns transcribed text", async () => {
  let onStop = null;
  const starts = [];
  const readFiles = [];
  const transcriptions = [];
  const syncedAuthorizations = [];
  global.wx = {
    getRecorderManager() {
      return {
        start(options) {
          starts.push(options);
        },
        stop() {},
        onStart() {},
        onStop(callback) {
          onStop = callback;
        },
        onFrameRecorded() {},
        onError() {},
      };
    },
    getFileSystemManager() {
      return {
        readFile(options) {
          readFiles.push(options);
          options.success({ errMsg: "ok", data: "cGNt" });
        },
      };
    },
  };
  const { recordMemoirVoiceSearchQuery } = loadVoiceSearchService({
    voiceApiExports: {
      transcribeMemoirVoiceSearchAudio: async (audioBase64, mimeType) => {
        transcriptions.push({ audioBase64, mimeType });
        return { transcript: " 外婆家的院子 " };
      },
    },
    authorizationExports: {
      syncVoiceAuthorizationConsent: async (options) => {
        syncedAuthorizations.push(options);
        return {};
      },
    },
  });

  const promise = recordMemoirVoiceSearchQuery();
  await flushAsyncSetup();
  onStop({ tempFilePath: "/tmp/search.pcm", duration: 800, fileSize: 3200 });
  const transcript = await promise;

  assert.equal(transcript, "外婆家的院子");
  assert.equal(starts[0].duration, 10000);
  assert.equal(starts[0].sampleRate, 16000);
  assert.equal(starts[0].numberOfChannels, 1);
  assert.equal(starts[0].format, "PCM");
  assert.equal(readFiles[0].filePath, "/tmp/search.pcm");
  assert.equal(readFiles[0].encoding, "base64");
  assert.deepEqual(transcriptions, [{ audioBase64: "cGNt", mimeType: "audio/pcm" }]);
  assert.deepEqual(syncedAuthorizations, [undefined]);
  delete global.wx;
});

test("voice search service navigates to authorization before recording", async () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { recordMemoirVoiceSearchQuery } = loadVoiceSearchService({
    authorizationExports: {
      readVoiceAuthorizationConsent: () => false,
    },
  });

  await assert.rejects(recordMemoirVoiceSearchQuery(), /请先同意声纹授权协议/);

  assert.deepEqual(navigations, ["/pages/voice-authorization/index"]);
  delete global.wx;
});

test("voice search service rejects pending recording when stop fails", async () => {
  global.wx = {
    getRecorderManager() {
      return {
        start() {},
        stop() {
          throw new Error("operateRecorder:fail already stopped");
        },
        onStart() {},
        onStop() {},
        onFrameRecorded() {},
        onError() {},
      };
    },
  };
  const {
    recordMemoirVoiceSearchQuery,
    stopMemoirVoiceSearchRecording,
  } = loadVoiceSearchService();

  const promise = recordMemoirVoiceSearchQuery();
  await flushAsyncSetup();
  stopMemoirVoiceSearchRecording();

  await assert.rejects(promise, /already stopped/);
  delete global.wx;
});
