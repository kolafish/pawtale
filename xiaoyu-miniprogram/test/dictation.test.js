const assert = require("node:assert/strict");
const test = require("node:test");
const { mockModule } = require("../../miniprogram/test/page-harness");

async function flushAsyncWork() {
  await Promise.resolve();
  await Promise.resolve();
}

function runtimeHarness(ensureBackendSession = async () => {}) {
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
    ensureBackendSession,
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

test("closing while authentication is pending does not open a socket later", async () => {
  let authenticated;
  const authentication = new Promise((resolve) => { authenticated = resolve; });
  const runtime = runtimeHarness(() => authentication);
  const connecting = runtime.client.connect();
  const rejected = assert.rejects(connecting, /已关闭/);
  await runtime.client.close();
  authenticated();
  await rejected;
  assert.equal(runtime.connectOptions, undefined);
  assert.equal(runtime.recorder.starts.length, 0);
});

test("closing a pending socket rejects connection and ignores late ready", async () => {
  const runtime = runtimeHarness();
  const connecting = runtime.client.connect();
  const rejected = assert.rejects(connecting, /已关闭/);
  await flushAsyncWork();
  await runtime.client.close();
  await rejected;
  runtime.socketCallbacks.message({ data: JSON.stringify({ type: "session.ready" }) });
  await assert.rejects(runtime.client.start(), /尚未就绪/);
  assert.equal(runtime.states.at(-1), "idle");
  assert.equal(runtime.recorder.starts.length, 0);
});

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
  assert.deepEqual(runtime.recorderOffs, ["start", "frame", "stop", "error"]);
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

test("dictation rejects a gateway error during connection without waiting for timeout", async () => {
  const runtime = runtimeHarness();
  const connecting = runtime.client.connect();
  await flushAsyncWork();
  runtime.socketCallbacks.open();

  runtime.socketCallbacks.message({
    data: JSON.stringify({
      type: "session.error",
      error: "article_dictation_unavailable",
    }),
  });

  await assert.rejects(connecting, /当前文章不可口述/);
  assert.deepEqual(runtime.errors, ["当前文章不可口述，请返回后重新打开"]);
});

test("dictation converts native recorder errors to actionable Chinese", async () => {
  const runtime = runtimeHarness();
  const connecting = runtime.client.connect();
  await flushAsyncWork();
  runtime.socketCallbacks.open();
  runtime.socketCallbacks.message({ data: JSON.stringify({ type: "session.ready" }) });
  await connecting;

  const starting = runtime.client.start();
  runtime.recorderCallbacks.error({ errMsg: "record:fail auth deny" });

  await assert.rejects(starting, /没有麦克风权限/);
  assert.deepEqual(runtime.errors, [
    "没有麦克风权限，请在微信设置中允许使用麦克风",
  ]);
});

test("dictation settles a pending recorder start when the socket closes", async () => {
  const runtime = runtimeHarness();
  const connecting = runtime.client.connect();
  await flushAsyncWork();
  runtime.socketCallbacks.open();
  runtime.socketCallbacks.message({ data: JSON.stringify({ type: "session.ready" }) });
  await connecting;

  const starting = runtime.client.start();
  runtime.socketCallbacks.close();

  await assert.rejects(starting, /语音连接已关闭/);
  assert.equal(runtime.recorder.stops, 1);
  assert.equal(runtime.states.at(-1), "error");
});
