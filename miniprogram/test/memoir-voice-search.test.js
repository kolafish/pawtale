require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadVoiceSearchService({ voiceApiExports = {}, privacyExports = {}, authorizationExports = {} } = {}) {
  const voiceApiPath = require.resolve("../services/memoir-transcript-api.ts");
  const privacyPath = require.resolve("../services/wechat-privacy.ts");
  const authorizationPath = require.resolve("../services/voice-authorization.ts");
  const recorderResourcePath = require.resolve("../services/recorder-resource.ts");
  const voiceSearchPath = require.resolve("../services/memoir-voice-search.ts");
  delete require.cache[voiceApiPath];
  delete require.cache[privacyPath];
  delete require.cache[authorizationPath];
  delete require.cache[recorderResourcePath];
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
      ensureVoiceRecordingAuthorization: async () => true,
      ...authorizationExports,
    },
  };
  return {
    ...require(voiceSearchPath),
    sharedRecorderResource: require(recorderResourcePath).sharedRecorderResource,
  };
}

async function flushAsyncSetup() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function pcmFrame(sample, samples = 2000) {
  const buffer = new ArrayBuffer(samples * 2);
  const view = new DataView(buffer);
  for (let index = 0; index < samples; index += 1) {
    view.setInt16(index * 2, sample, true);
  }
  return buffer;
}

test("voice search service records PCM audio and returns transcribed text", async () => {
  let onStop = null;
  const starts = [];
  const readFiles = [];
  const transcriptions = [];
  const authorizationChecks = [];
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
      ensureVoiceRecordingAuthorization: async (sourceSessionId) => {
        authorizationChecks.push(sourceSessionId);
        return true;
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
  assert.deepEqual(authorizationChecks, [undefined]);
  delete global.wx;
});

test("voice search service auto-stops after speech followed by silence", async () => {
  let onStop = null;
  let onFrameRecorded = null;
  const stops = [];
  global.wx = {
    getRecorderManager() {
      return {
        start() {},
        stop() {
          stops.push("stop");
        },
        onStart() {},
        onStop(callback) {
          onStop = callback;
        },
        onFrameRecorded(callback) {
          onFrameRecorded = callback;
        },
        onError() {},
      };
    },
    getFileSystemManager() {
      return {
        readFile(options) {
          options.success({ errMsg: "ok", data: "cGNt" });
        },
      };
    },
  };
  const {
    recordMemoirVoiceSearchQuery,
    stopMemoirVoiceSearchRecording,
  } = loadVoiceSearchService({
    voiceApiExports: {
      transcribeMemoirVoiceSearchAudio: async () => ({ transcript: "春节" }),
    },
  });

  const promise = recordMemoirVoiceSearchQuery();
  await flushAsyncSetup();
  onFrameRecorded({ frameBuffer: pcmFrame(2600), isLastFrame: false });
  onFrameRecorded({ frameBuffer: pcmFrame(2600), isLastFrame: false });
  for (let index = 0; index < 8; index += 1) {
    onFrameRecorded({ frameBuffer: pcmFrame(0), isLastFrame: false });
  }

  assert.deepEqual(stops, ["stop"]);
  stopMemoirVoiceSearchRecording();
  assert.deepEqual(stops, ["stop"]);
  onStop({ tempFilePath: "/tmp/search.pcm", duration: 1200, fileSize: 4800 });
  assert.equal(await promise, "春节");
  delete global.wx;
});

test("voice search service auto-stops when no speech is detected", async () => {
  let onStop = null;
  let onFrameRecorded = null;
  const stops = [];
  const noVoiceStops = [];
  global.wx = {
    getRecorderManager() {
      return {
        start() {},
        stop() {
          stops.push("stop");
        },
        onStart() {},
        onStop(callback) {
          onStop = callback;
        },
        onFrameRecorded(callback) {
          onFrameRecorded = callback;
        },
        onError() {},
      };
    },
    getFileSystemManager() {
      return {
        readFile(options) {
          options.success({ errMsg: "ok", data: "cGNt" });
        },
      };
    },
  };
  const { recordMemoirVoiceSearchQuery } = loadVoiceSearchService({
    voiceApiExports: {
      transcribeMemoirVoiceSearchAudio: async () => ({ transcript: "" }),
    },
  });

  const promise = recordMemoirVoiceSearchQuery({
    onNoVoiceAutoStop: () => noVoiceStops.push("no-voice"),
    noVoiceTranscriptMessage: "没有听到声音，语音对话已暂停",
  });
  await flushAsyncSetup();
  for (let index = 0; index < 30; index += 1) {
    onFrameRecorded({ frameBuffer: pcmFrame(0), isLastFrame: false });
  }

  assert.deepEqual(stops, ["stop"]);
  assert.deepEqual(noVoiceStops, ["no-voice"]);
  onStop({ tempFilePath: "/tmp/search.pcm", duration: 3600, fileSize: 14400 });
  await assert.rejects(promise, /没有听到声音，语音对话已暂停/);
  delete global.wx;
});

test("voice input manual mode waits for the user to stop despite initial silence", async () => {
  const onStarts = [];
  const onStops = [];
  const onFrames = [];
  const starts = [];
  const stops = [];
  global.wx = {
    getRecorderManager() {
      return {
        start(options) {
          starts.push(options);
        },
        stop() {
          stops.push("stop");
        },
        onStart(callback) {
          onStarts.push(callback);
        },
        onStop(callback) {
          onStops.push(callback);
        },
        onFrameRecorded(callback) {
          onFrames.push(callback);
        },
        onError() {},
      };
    },
    getFileSystemManager() {
      return {
        readFile(options) {
          options.success({ errMsg: "ok", data: "cGNt" });
        },
      };
    },
  };
  const {
    recordMemoirVoiceSearchQuery,
    sharedRecorderResource,
    stopMemoirVoiceSearchRecording,
  } = loadVoiceSearchService({
    voiceApiExports: {
      transcribeMemoirVoiceSearchAudio: async () => ({ transcript: "把外婆改成奶奶" }),
    },
  });

  sharedRecorderResource.start("previous-voice-session", {}, {});
  onStarts.forEach((callback) => callback());
  const previousStop = sharedRecorderResource.stopAndWait(
    "previous-voice-session",
  );
  onStops.forEach((callback) => callback({}));
  await previousStop;
  starts.length = 0;
  stops.length = 0;

  const promise = recordMemoirVoiceSearchQuery({
    stopMode: "manual",
    maxDurationMs: 30000,
  });
  await flushAsyncSetup();
  onStarts.forEach((callback) => callback());
  assert.deepEqual(stops, []);
  for (let index = 0; index < 40; index += 1) {
    onFrames.forEach((callback) => callback({
      frameBuffer: pcmFrame(0),
      isLastFrame: false,
    }));
  }

  assert.equal(starts[0].duration, 30000);
  assert.deepEqual(stops, []);
  stopMemoirVoiceSearchRecording();
  assert.deepEqual(stops, ["stop"]);
  onStops.forEach((callback) => callback({
    tempFilePath: "/tmp/voice-edit.pcm",
    duration: 5000,
    fileSize: 20000,
  }));
  assert.equal(await promise, "把外婆改成奶奶");
  delete global.wx;
});

test("voice search service stops before recording when authorization is missing", async () => {
  global.wx = {};
  const { recordMemoirVoiceSearchQuery } = loadVoiceSearchService({
    authorizationExports: {
      ensureVoiceRecordingAuthorization: async () => false,
    },
  });

  await assert.rejects(recordMemoirVoiceSearchQuery(), /请先同意语音授权/);
  delete global.wx;
});

test("voice search service finishes a recording when native stop fails", async () => {
  let onStart = null;
  global.wx = {
    getRecorderManager() {
      return {
        start() {},
        stop() {
          throw new Error("operateRecorder:fail already stopped");
        },
        onStart(callback) {
          onStart = callback;
        },
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
  onStart();
  stopMemoirVoiceSearchRecording();

  await assert.rejects(promise, /没有生成可识别的语音/);
  delete global.wx;
});

test("cancelled voice search does not start recording after authorization returns", async () => {
  let resolveAuthorization;
  let recorderStarts = 0;
  let cancelled = false;
  global.wx = {
    getRecorderManager() {
      return {
        start() { recorderStarts += 1; },
        stop() {},
        onStart() {}, onStop() {}, onFrameRecorded() {}, onError() {},
      };
    },
  };
  const { recordMemoirVoiceSearchQuery } = loadVoiceSearchService({
    authorizationExports: {
      ensureVoiceRecordingAuthorization: () => new Promise((resolve) => { resolveAuthorization = resolve; }),
    },
  });

  const search = recordMemoirVoiceSearchQuery({ isCancelled: () => cancelled });
  cancelled = true;
  resolveAuthorization(true);

  await assert.rejects(search, /语音搜索已取消/);
  assert.equal(recorderStarts, 0);
  delete global.wx;
});

test("cancelled voice search stops the recorder and never uploads its late stop result", async () => {
  let onStop;
  let stops = 0;
  let uploads = 0;
  global.wx = {
    getRecorderManager() {
      return {
        start() {},
        stop() { stops += 1; },
        onStart() {},
        onStop(callback) { onStop = callback; },
        onFrameRecorded() {}, onError() {},
      };
    },
    getFileSystemManager() {
      return { readFile() { throw new Error("cancelled recording must not read the file"); } };
    },
  };
  const {
    recordMemoirVoiceSearchQuery,
    cancelMemoirVoiceSearchRecording,
  } = loadVoiceSearchService({
    voiceApiExports: {
      transcribeMemoirVoiceSearchAudio: async () => { uploads += 1; return { transcript: "" }; },
    },
  });

  const search = recordMemoirVoiceSearchQuery();
  await flushAsyncSetup();
  cancelMemoirVoiceSearchRecording();
  onStop({ tempFilePath: "/tmp/search.pcm", duration: 800, fileSize: 3200 });

  await assert.rejects(search, /语音搜索已取消/);
  assert.equal(stops, 1);
  assert.equal(uploads, 0);
  delete global.wx;
});

test("cancelled voice search never uploads after recording stopped while its file is reading", async () => {
  let onStop;
  let fileRead;
  let uploads = 0;
  let cancelled = false;
  global.wx = {
    getRecorderManager() {
      return {
        start() {}, stop() {}, onStart() {},
        onStop(callback) { onStop = callback; },
        onFrameRecorded() {}, onError() {},
      };
    },
    getFileSystemManager() {
      return { readFile(options) { fileRead = options; } };
    },
  };
  const { recordMemoirVoiceSearchQuery } = loadVoiceSearchService({
    voiceApiExports: {
      transcribeMemoirVoiceSearchAudio: async () => { uploads += 1; return { transcript: "" }; },
    },
  });

  const search = recordMemoirVoiceSearchQuery({ isCancelled: () => cancelled });
  await flushAsyncSetup();
  onStop({ tempFilePath: "/tmp/search.pcm", duration: 800, fileSize: 3200 });
  await flushAsyncSetup();
  assert.equal(typeof fileRead?.success, "function");
  cancelled = true;
  fileRead.success({ errMsg: "ok", data: "cGNt" });

  await assert.rejects(search, /语音搜索已取消/);
  assert.equal(uploads, 0);
  delete global.wx;
});
