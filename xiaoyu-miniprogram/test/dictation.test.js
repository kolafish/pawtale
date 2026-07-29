const assert = require("node:assert/strict");
const test = require("node:test");
const { mockModule } = require("../../miniprogram/test/page-harness");

async function flushAsyncWork() {
  await Promise.resolve();
  await Promise.resolve();
}

function runtimeHarness() {
  const socketCallbacks = {};
  const recorderCallbacks = {};
  const sent = [];
  const states = [];
  const transcripts = [];
  const errors = [];
  const recorderOffs = [];
  const recorder = {
    starts: [],
    stops: 0,
    start(options) {
      this.starts.push(options);
    },
    stop() {
      this.stops += 1;
    },
    onStart(callback) {
      recorderCallbacks.start = callback;
    },
    onFrameRecorded(callback) {
      recorderCallbacks.frame = callback;
    },
    onStop(callback) {
      recorderCallbacks.stop = callback;
    },
    onError(callback) {
      recorderCallbacks.error = callback;
    },
    offStart(callback) {
      recorderOffs.push(callback === recorderCallbacks.start ? "start" : "wrong");
    },
    offFrameRecorded(callback) {
      recorderOffs.push(callback === recorderCallbacks.frame ? "frame" : "wrong");
    },
    offStop(callback) {
      recorderOffs.push(callback === recorderCallbacks.stop ? "stop" : "wrong");
    },
    offError(callback) {
      recorderOffs.push(callback === recorderCallbacks.error ? "error" : "wrong");
    },
  };
  const socket = {
    send({ data }) {
      sent.push(JSON.parse(data));
    },
    close() {},
    onOpen(callback) {
      socketCallbacks.open = callback;
    },
    onMessage(callback) {
      socketCallbacks.message = callback;
    },
    onError(callback) {
      socketCallbacks.error = callback;
    },
    onClose(callback) {
      socketCallbacks.close = callback;
    },
  };
  let connectOptions;
  global.wx = {
    connectSocket(options) {
      connectOptions = options;
      return socket;
    },
    getRecorderManager() {
      return recorder;
    },
    arrayBufferToBase64(buffer) {
      return Buffer.from(buffer).toString("base64");
    },
  };
  mockModule("../../xiaoyu-miniprogram/services/auth.ts", {
    ensureBackendSession: async () => {},
  });
  mockModule("../../xiaoyu-miniprogram/services/request.ts", {
    readSessionCookie: () => "pawtale_session=dictation",
  });
  delete require.cache[require.resolve("../services/dictation.ts")];
  const { ArticleDictationClient } = require("../services/dictation.ts");
  const client = new ArticleDictationClient({
    projectId: "project-1",
    documentId: "document-1",
    callbacks: {
      onState: (state) => states.push(state),
      onTranscript: (text, final) => transcripts.push({ text, final }),
      onError: (message) => errors.push(message),
    },
  });
  return {
    client,
    errors,
    recorder,
    recorderCallbacks,
    recorderOffs,
    sent,
    socketCallbacks,
    states,
    transcripts,
    get connectOptions() {
      return connectOptions;
    },
  };
}

test("dictation streams PCM frames and returns final text without interview events", async () => {
  const runtime = runtimeHarness();

  const connecting = runtime.client.connect();
  await flushAsyncWork();
  runtime.socketCallbacks.open();
  runtime.socketCallbacks.message({
    data: JSON.stringify({ type: "session.ready", state: "ready" }),
  });
  await connecting;

  const starting = runtime.client.start();
  runtime.recorderCallbacks.start();
  await starting;
  runtime.recorderCallbacks.frame({
    frameBuffer: Uint8Array.from([1, 2, 3]).buffer,
    isLastFrame: false,
  });
  runtime.socketCallbacks.message({
    data: JSON.stringify({ type: "asr.partial", text: "正在口述" }),
  });
  runtime.socketCallbacks.message({
    data: JSON.stringify({ type: "asr.final", text: "口述完成" }),
  });

  const stopping = runtime.client.stop();
  runtime.recorderCallbacks.stop();
  runtime.socketCallbacks.message({
    data: JSON.stringify({ type: "audio.ended", state: "ready" }),
  });
  await stopping;
  const closing = runtime.client.close();
  await flushAsyncWork();
  runtime.socketCallbacks.message({
    data: JSON.stringify({ type: "session.stopped", state: "stopped" }),
  });
  await closing;

  assert.match(runtime.connectOptions.url, /projects\/project-1\/documents\/document-1\/dictation$/);
  assert.deepEqual(runtime.connectOptions.header, {
    Cookie: "pawtale_session=dictation",
  });
  assert.deepEqual(runtime.sent, [
    { type: "session.start" },
    { type: "audio.frame", audio: "AQID" },
    { type: "audio.end" },
    { type: "session.stop" },
  ]);
  assert.deepEqual(runtime.transcripts, [
    { text: "正在口述", final: false },
    { text: "口述完成", final: true },
  ]);
  assert.deepEqual(runtime.recorderOffs, ["start", "frame", "stop", "error"]);
  assert.equal(runtime.errors.length, 0);
});

test("dictation stops the native recorder when the gateway fails", async () => {
  const runtime = runtimeHarness();
  const connecting = runtime.client.connect();
  await flushAsyncWork();
  runtime.socketCallbacks.open();
  runtime.socketCallbacks.message({ data: JSON.stringify({ type: "session.ready" }) });
  await connecting;
  const starting = runtime.client.start();
  runtime.recorderCallbacks.start();
  await starting;

  runtime.socketCallbacks.message({
    data: JSON.stringify({
      type: "session.error",
      error: "voice_gateway_capacity_reached",
    }),
  });

  assert.equal(runtime.recorder.stops, 1);
  assert.deepEqual(runtime.errors, ["当前语音服务繁忙，请稍后再试"]);
  assert.equal(runtime.states.at(-1), "error");
});
