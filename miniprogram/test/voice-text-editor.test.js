require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  componentContext,
  flushAsyncWork,
  loadComponentModule,
  mockModule,
} = require("./page-harness");

test("shared voice text editor shows live transcription without replacing existing text", async () => {
  const controller = {
    callbacks: null,
    finishCount: 0,
    recordingOptions: null,
    async connect(options) {
      this.callbacks = options.callbacks;
    },
    startRecording(options) {
      this.recordingOptions = options;
      return true;
    },
    stopRecording() {},
    async finishSession() {
      this.finishCount += 1;
      return true;
    },
    close() {},
  };
  mockModule("../services/streaming-voice-controller.ts", {
    StreamingVoiceController: class {
      constructor() {
        return controller;
      }
    },
  });
  mockModule("../services/voice-authorization.ts", {
    ensureVoiceRecordingAuthorization: async () => true,
  });
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const component = loadComponentModule(
    "../components/voice-text-editor/index.ts",
  ).component;
  const context = componentContext(component, {
    value: "请保留校舍这一段。",
    gatewayUrl: "ws://backend.local/projects/project-1/dictation",
  });
  component.lifetimes.attached.call(context);

  await context.toggleVoice.call(context);
  controller.callbacks.onStatus("listening");
  controller.callbacks.onPartial("人物称谓");
  controller.callbacks.onPartial("人物称谓要前后一致");
  controller.callbacks.onFinal({
    type: "asr.final",
    text: "人物称谓要前后一致。",
  });
  controller.callbacks.onEvent({ type: "audio.ended" });
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(controller.finishCount, 1);
  assert.deepEqual(controller.recordingOptions, { maxDurationMs: 30000 });
  assert.equal(context.data.voicePhase, "idle");
  assert.equal(
    context.events.filter((event) => event.name === "change").at(-1).detail.value,
    "请保留校舍这一段。\n人物称谓要前后一致。",
  );
  assert.deepEqual(toasts, ["语音已转成文字"]);
});

test("opt-in voice editor starts when attached and submits only after final text", async () => {
  const controller = {
    callbacks: null,
    connectCount: 0,
    finishCount: 0,
    recordingOptions: null,
    async connect(options) {
      this.connectCount += 1;
      this.callbacks = options.callbacks;
    },
    startRecording(options) {
      this.recordingOptions = options;
      return true;
    },
    stopRecording() {},
    async finishSession() {
      this.finishCount += 1;
      return true;
    },
    close() {},
  };
  mockModule("../services/streaming-voice-controller.ts", {
    StreamingVoiceController: class {
      constructor() {
        return controller;
      }
    },
  });
  mockModule("../services/voice-authorization.ts", {
    ensureVoiceRecordingAuthorization: async () => true,
  });
  const toasts = [];
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const component = loadComponentModule("../components/voice-text-editor/index.ts").component;
  const context = componentContext(component, {
    gatewayUrl: "ws://backend.local/projects/project-1/dictation",
    autoStart: true,
    submitOnVoiceFinal: true,
  });
  component.lifetimes.attached.call(context);
  await flushAsyncWork();

  assert.equal(controller.connectCount, 1);
  assert.deepEqual(controller.recordingOptions, {
    maxDurationMs: 30000,
    noVoiceActivityTimeoutMs: 15000,
  });
  controller.callbacks.onStatus("listening");
  controller.callbacks.onPartial("童年回忆换个");
  assert.equal(context.events.filter(event => event.name === "submit").length, 0);
  controller.callbacks.onFinal({ type: "asr.final", text: "童年回忆换个名字。" });
  assert.equal(context.events.filter(event => event.name === "submit").length, 0);
  controller.callbacks.onEvent({ type: "audio.ended" });
  await flushAsyncWork();

  assert.equal(controller.finishCount, 1);
  assert.deepEqual(
    context.events.filter(event => event.name === "submit").map(event => event.detail.value),
    ["童年回忆换个名字。"],
  );
  assert.deepEqual(toasts, ["已听完，正在理解"]);
});

for (const ending of ["silence", "manual"]) {
  test(`voice editor uses the shared recording pipeline to finish on ${ending} and wait for final text`, async (t) => {
    let now = 10000;
    t.mock.method(Date, "now", () => now);
    const native = {};
    const socket = {};
    const sent = [];
    let stops = 0;
    global.wx = {
      getStorageSync: () => "",
      getRecorderManager: () => ({
        onStart: cb => { native.start = cb; },
        onStop: cb => { native.stop = cb; },
        onFrameRecorded: cb => { native.frame = cb; },
        onError: cb => { native.error = cb; },
        start: () => queueMicrotask(() => native.start()),
        stop: () => { stops++; queueMicrotask(() => native.stop()); },
      }),
      connectSocket: () => ({
        onOpen: cb => queueMicrotask(cb),
        onMessage: cb => { socket.message = cb; },
        onError() {}, onClose() {}, close() {},
        send: ({ data }) => {
          const event = JSON.parse(data);
          sent.push(event);
          if (event.type === "session.stop") queueMicrotask(() => message({ type: "session.stopped" }));
        },
      }),
      showToast() {},
    };
    mockModule("../services/voice-authorization.ts", { ensureVoiceRecordingAuthorization: async () => true });
    mockModule("../services/memoir-voice-session-api.ts", { backendSessionCookieHeader: async () => "" });
    // Use the real controller, ASR client, VAD and recorder ownership. Only native I/O is simulated.
    for (const name of ["streaming-voice-controller", "memoir-streaming-asr", "recorder-resource"]) {
      delete require.cache[require.resolve(`../services/${name}.ts`)];
    }
    const component = loadComponentModule("../components/voice-text-editor/index.ts").component;
    const context = componentContext(component, { value: "已有意见。", gatewayUrl: "ws://backend.local/dictation" });
    component.lifetimes.attached.call(context);
    t.after(() => component.lifetimes.detached.call(context));
    function message(event) { socket.message({ data: JSON.stringify(event) }); }
    function frame(sample) {
      now += 100;
      const buffer = new ArrayBuffer(3200);
      new Int16Array(buffer).fill(sample);
      native.frame({ frameBuffer: buffer, isLastFrame: false });
    }

    await context.toggleVoice();
    await flushAsyncWork();
    for (let i = 0; i < 40; i++) frame(0);
    assert.equal(stops, 0, "wait for the user to begin speaking");
    for (let i = 0; i < 3; i++) frame(16384);
    message({ type: "asr.partial", text: "童年记忆换个" });
    for (let i = 0; i < 34; i++) frame(0);
    assert.equal(stops, 0, "a short pause must not end the command");
    if (ending === "silence") frame(0);
    else await context.toggleVoice();
    await flushAsyncWork();
    assert.equal(stops, 1);
    assert.equal(sent.filter(e => e.type === "audio.end").length, 1);
    assert.equal(context.data.voicePhase, "stopping");
    context.submit();
    assert.equal(context.events.filter(e => e.name === "submit").length, 0);

    message({ type: "asr.final", text: "童年记忆换个名字。" });
    message({ type: "audio.ended" });
    await flushAsyncWork(12);
    assert.equal(context.data.voicePhase, "idle");
    assert.equal(context.data.error, "");
    assert.equal(context.events.filter(e => e.name === "change").at(-1).detail.value, "已有意见。\n童年记忆换个名字。");
    assert.equal(context.events.filter(e => e.name === "submit").length, 0, "stopping recording does not execute edits");
    context.submit();
    assert.equal(context.events.filter(e => e.name === "submit").at(-1).detail.value, "已有意见。\n童年记忆换个名字。");
  });
}
