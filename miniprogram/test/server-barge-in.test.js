require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { MemoirStreamingAsrClient } = require("../services/memoir-streaming-asr.ts");
const { WechatRecorderResource } = require("../services/recorder-resource.ts");

function harness(t, sdkVersion = "3.17.3", nativeVoiceChatSupported = true) {
  const native = { starts: 0, stops: 0, callbacks: {},
    onStart(fn) { this.callbacks.start = fn; }, onFrameRecorded(fn) { this.callbacks.frame = fn; },
    onStop(fn) { this.callbacks.stop = fn; }, onError(fn) { this.callbacks.error = fn; },
    start(options) { this.starts++; this.options = options; },
    stop() { this.stops++; queueMicrotask(() => this.callbacks.stop()); },
  };
  global.wx = {
    getStorageSync() { return ""; },
    getAppBaseInfo() { return { version: "8.0.68", SDKVersion: sdkVersion }; },
    getDeviceInfo() { return { platform: "ios", system: "iOS 18" }; },
    canIUse(schema) {
      assert.equal(schema, "RecorderManager.start.object.audioMode.voiceChat");
      return nativeVoiceChatSupported;
    },
  };
  const events = [], errors = [], clientEvents = [];
  const client = new MemoirStreamingAsrClient({ voiceSessionId: "test", guidanceMode: "free_narration",
    callbacks: { onStatus() {}, onEvent() {}, onPartial() {}, onFinal() {}, onError: msg => errors.push(msg),
      onClientEvent: event => clientEvents.push(event) },
  }, new WechatRecorderResource(() => native));
  client.socketOpen = true;
  client.socketTask = { send: ({ data }) => events.push(JSON.parse(data)), close() {} };
  const callbacks = { hits: 0, errors: [], onBargeIn() { this.hits++; }, onError(msg) { this.errors.push(msg); } };
  const start = () => { assert.equal(client.startBargeInMonitoring(callbacks), true); native.callbacks.start(); };
  const frame = value => {
    const buffer = new Int16Array(2048).fill(value).buffer;
    native.callbacks.frame({ frameBuffer: buffer, isLastFrame: false });
    return Buffer.from(buffer);
  };
  const speech = (start_ms = 0, monitor_id = client.bargeInId) => client.handleSocketMessage(JSON.stringify({
    type: "barge_in.speech", monitor_id, start_ms,
  }));
  t.after(() => client.close({ force: true }));
  return { client, native, events, errors, clientEvents, callbacks, start, frame, speech };
}

test("server confirmation promotes the same recorder and sends the exact retained opening once", t => {
  const h = harness(t); h.start();
  assert.equal(h.native.options.audioMode, "voiceChat");
  h.frame(10); const opening = h.frame(20); const word = h.frame(30);
  assert.equal(h.events.some(e => e.type === "audio.frame"), false);
  h.speech(128); h.speech(128);
  assert.equal(h.callbacks.hits, 1);
  assert.deepEqual(h.clientEvents.filter(e => e.type === "barge_in_confirmed").map(e =>
    [e.bargeInStartMs, e.retainedAudioMs]), [[128, 256]]);
  const whileAuthorizing = h.frame(40);
  void h.client.stopBargeInMonitoring(); // playback cancellation preserves confirmed capture
  assert.equal(h.client.startRecording(), true);
  assert.equal(h.native.starts, 1); assert.equal(h.native.stops, 0);
  assert.deepEqual(h.events.filter(e => e.type === "audio.frame").map(e => Buffer.from(e.audio, "base64")),
    [opening, word, whileAuthorizing]);
  assert.equal(h.events.filter(e => e.type === "session.start").length, 1);
  assert.ok(h.events.findIndex(e => e.type === "session.start") < h.events.findIndex(e => e.type === "audio.frame"));
});

for (const nativeSupport of [false, null]) {
  test(`new SDK with native AEC capability ${nativeSupport} does not start passive capture`, t => {
    const h = harness(t, "3.17.3", nativeSupport);
    if (nativeSupport === null) delete global.wx.canIUse;
    global.wx.getDeviceInfo = () => ({ platform: "ohos", system: "HarmonyOS" });
    global.wx.getAppBaseInfo = () => ({ version: "8.0.22", SDKVersion: "3.17.3" });
    assert.equal(h.client.startBargeInMonitoring(h.callbacks), false);
    assert.equal(h.native.starts, 0);
    assert.deepEqual(h.events, []);
    assert.match(h.callbacks.errors[0], /点按钮回答/);
    const configuration = h.clientEvents.find(e => e.type === "barge_in_unavailable").recorderConfiguration;
    assert.deepEqual(configuration, { capturePurpose: "barge_in", platform: "ohos",
      wechatVersion: "8.0.22", sdkVersion: "3.17.3", nativeVoiceChatSupported: nativeSupport,
      requestedAudioMode: "default" });

    assert.equal(h.client.startRecording(), true);
    h.native.callbacks.start(); h.frame(30);
    assert.equal(Object.hasOwn(h.native.options, "audioMode"), false);
    assert.equal(h.events.filter(e => e.type === "audio.frame").length, 1);
    assert.deepEqual(h.errors, []);
  });
}

test("recording diagnostics include the runtime platform, versions and requested audio mode", t => {
  const h = harness(t); h.start();
  const requested = h.clientEvents.find(e => e.type === "recorder_start_requested");
  assert.deepEqual(requested.recorderConfiguration, {
    capturePurpose: "barge_in", platform: "ios", wechatVersion: "8.0.68", sdkVersion: "3.17.3",
    nativeVoiceChatSupported: true, requestedAudioMode: "voiceChat",
  });
  assert.equal(h.native.options.audioMode, requested.recorderConfiguration.requestedAudioMode);
});

for (const sdkVersion of ["3.17.2", "2.32.3", "", "unknown", null]) {
  test(`unsupported or unknown base library ${JSON.stringify(sdkVersion)} keeps manual recording available`, t => {
    const h = harness(t, sdkVersion);
    if (sdkVersion === null) delete global.wx.getAppBaseInfo;
    assert.equal(h.client.startBargeInMonitoring(h.callbacks), false);
    assert.equal(h.client.isBargeInMonitoring(), false);
    assert.equal(h.native.starts, 0);
    assert.deepEqual(h.events, []);
    assert.match(h.callbacks.errors[0], /点按钮回答/);
    assert.deepEqual(h.errors, []);

    assert.equal(h.client.startRecording(), true);
    h.native.callbacks.start();
    const opening = h.frame(30);
    assert.equal(Object.hasOwn(h.native.options, "audioMode"), false);
    assert.equal(h.events.filter(e => e.type === "session.start").length, 1);
    assert.deepEqual(h.events.filter(e => e.type === "audio.frame").map(e => Buffer.from(e.audio, "base64")),
      [opening]);
  });
}

for (const sdkVersion of ["3.17.3", "3.18.0", "4.0.0"]) {
  test(`base library ${sdkVersion} enables native echo cancellation for ordinary recording too`, t => {
    const h = harness(t, sdkVersion);
    assert.equal(h.client.startRecording(), true);
    h.native.callbacks.start();
    assert.equal(h.native.options.audioMode, "voiceChat");
  });
}

test("a stopped playback cannot interrupt a newer playback", async t => {
  const h = harness(t); h.start(); const oldId = h.client.bargeInId;
  await h.client.stopBargeInMonitoring(true);
  h.start(); h.frame(30); h.speech(0, oldId);
  assert.equal(h.callbacks.hits, 0);
  h.speech(); assert.equal(h.callbacks.hits, 1);
});

test("authorization failure discards pending capture and releases the microphone", async t => {
  const h = harness(t); h.start(); h.frame(20); h.speech(); h.frame(40);
  await h.client.stopBargeInMonitoring(true);
  assert.equal(h.client.isBargeInMonitoring(), false);
  assert.equal(h.native.stops, 1);
  assert.equal(h.events.some(e => e.type === "audio.frame"), false);
});

test("a result older than the rolling buffer never starts a truncated ASR turn", t => {
  const h = harness(t); h.start(); for (let i = 0; i < 25; i++) h.frame(20);
  h.speech(0);
  assert.equal(h.callbacks.hits, 0);
  assert.match(h.callbacks.errors[0], /延迟/);
  assert.equal(h.client.isBargeInMonitoring(), false);
});

test("pending capture expires explicitly while consent or connection work is stalled", t => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const h = harness(t); h.start(); h.frame(20); h.speech();
  t.mock.timers.tick(10000);
  assert.equal(h.client.isBargeInMonitoring(), false);
  assert.match(h.errors[0], /录音准备超时/);
  assert.equal(h.native.stops, 1);
});

test("closing while the native recorder is opening stops passive capture", async t => {
  const h = harness(t);
  h.client.startBargeInMonitoring(h.callbacks);
  h.client.close({ force: true });
  await Promise.resolve();
  assert.equal(h.client.isBargeInMonitoring(), false);
  assert.equal(h.native.stops, 0);
  h.native.callbacks.start();
  await Promise.resolve();
  assert.equal(h.native.stops, 1);
  assert.equal(h.events.some(e => e.type === "session.start"), false);
});

test("cancelling passive capture before native start keeps the gateway available for manual recording", async t => {
  const h = harness(t);
  let nativeStarted = false;
  h.native.stop = () => {
    h.native.stops++;
    queueMicrotask(() => {
      if (!nativeStarted) {
        h.native.callbacks.error({ errMsg: "operateRecorder:fail recorder not start" });
      } else {
        nativeStarted = false;
        h.native.callbacks.stop();
      }
    });
  };
  assert.equal(h.client.startBargeInMonitoring(h.callbacks), true);
  const stopped = h.client.stopBargeInMonitoring();
  await Promise.resolve();
  assert.deepEqual(h.errors, []);
  assert.equal(h.native.stops, 0);

  nativeStarted = true;
  h.native.callbacks.start();
  await stopped;
  assert.deepEqual(h.errors, []);
  assert.equal(h.native.stops, 1);
  assert.equal(h.client.socketOpen, true);
  assert.equal(h.events.some(e => e.type === "session.start"), false);

  assert.equal(h.client.startRecording(), true);
  nativeStarted = true;
  h.native.callbacks.start();
  h.frame(30);
  assert.equal(h.events.filter(e => e.type === "session.start").length, 1);
  assert.equal(h.events.filter(e => e.type === "audio.frame").length, 1);
});

test("server unavailability leaves manual recording available", async t => {
  const h = harness(t); h.start();
  h.client.handleSocketMessage(JSON.stringify({ type: "barge_in.error", monitor_id: h.client.bargeInId }));
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.equal(h.callbacks.hits, 0);
  assert.equal(h.callbacks.errors.length, 1);
  assert.equal(h.client.socketOpen, true);
});

test("manual interruption awaits a stop already requested by playback cancellation", async t => {
  const h = harness(t); h.start();
  let release;
  h.native.stop = () => { h.native.stops++; release = () => h.native.callbacks.stop(); };
  const first = h.client.stopBargeInMonitoring();
  let stopped = false;
  const second = h.client.stopBargeInMonitoring().then(() => { stopped = true; });
  await Promise.resolve();
  assert.equal(stopped, false);
  assert.equal(h.native.stops, 1);
  release(); await Promise.all([first, second]);
  assert.equal(h.client.startRecording(), true);
  assert.equal(h.native.starts, 2);
  h.native.stop = () => h.native.callbacks.stop();
});
