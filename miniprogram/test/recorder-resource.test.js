require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function recorderHarness() {
  const callbacks = {};
  return {
    starts: [],
    stops: 0,
    callbacks,
    start(options) {
      this.starts.push(options);
    },
    stop() {
      this.stops += 1;
    },
    onStart(callback) {
      callbacks.start = callback;
    },
    onFrameRecorded(callback) {
      callbacks.frame = callback;
    },
    onError(callback) {
      callbacks.error = callback;
    },
    onStop(callback) {
      callbacks.stop = callback;
    },
  };
}

test("recorder resource gives the microphone to only one owner until onStop releases it", async () => {
  const recorder = recorderHarness();
  const { WechatRecorderResource } = require("../services/recorder-resource.ts");
  const resource = new WechatRecorderResource(() => recorder);

  assert.equal(resource.start("barge-in", { format: "PCM" }, {}), true);
  assert.equal(resource.currentOwner(), "barge-in");
  assert.equal(resource.start("asr", { format: "PCM" }, {}), false);
  recorder.callbacks.start();

  const stopped = resource.stopAndWait("barge-in");
  assert.equal(recorder.stops, 1);
  assert.equal(resource.isBusy(), true);
  assert.equal(resource.start("asr", { format: "PCM" }, {}), false);

  recorder.callbacks.stop();
  await stopped;

  assert.equal(resource.isBusy(), false);
  assert.equal(resource.start("asr", { format: "PCM" }, {}), true);
  assert.equal(resource.currentOwner(), "asr");
});

test("recorder resource releases ownership when recorder start throws", () => {
  const recorder = recorderHarness();
  recorder.start = () => {
    throw new Error("operateRecorder:fail is recording or paused");
  };
  const { WechatRecorderResource } = require("../services/recorder-resource.ts");
  const resource = new WechatRecorderResource(() => recorder);

  assert.throws(
    () => resource.start("barge-in", { format: "PCM" }, {}),
    /is recording or paused/,
  );
  assert.equal(resource.isBusy(), false);
});

test("recorder resource releases ownership before notifying stop handlers", () => {
  const recorder = recorderHarness();
  const { WechatRecorderResource } = require("../services/recorder-resource.ts");
  const resource = new WechatRecorderResource(() => recorder);

  let nextStartResult = false;
  assert.equal(resource.start("barge-in", { format: "PCM" }, {
    onStop() {
      nextStartResult = resource.start("asr", { format: "PCM" }, {});
    },
  }), true);

  recorder.callbacks.stop();

  assert.equal(nextStartResult, true);
  assert.equal(resource.currentOwner(), "asr");
});

test("recorder resource waits for native start before completing an early stop", async () => {
  const recorder = recorderHarness();
  let nativeStarted = false;
  const errors = [];
  let starts = 0;
  recorder.stop = function stopBeforeNativeStart() {
    this.stops += 1;
    if (!nativeStarted) {
      queueMicrotask(() => recorder.callbacks.error({
        errMsg: "operateRecorder:fail recorder not start",
      }));
    }
  };
  const { WechatRecorderResource } = require("../services/recorder-resource.ts");
  const resource = new WechatRecorderResource(() => recorder);

  assert.equal(resource.start("barge-in", { format: "PCM" }, {
    onStart: () => { starts += 1; },
    onError: (message) => errors.push(message),
  }), true);
  const stopped = resource.stopAndWait("barge-in");
  await Promise.resolve();

  assert.deepEqual(errors, []);
  assert.equal(recorder.stops, 0);
  assert.equal(resource.isBusy(), true);
  assert.equal(resource.start("asr", { format: "PCM" }, {}), false);

  nativeStarted = true;
  recorder.callbacks.start();
  assert.equal(starts, 0);
  assert.equal(recorder.stops, 1);
  recorder.callbacks.stop();
  await stopped;

  assert.equal(resource.isBusy(), false);
  assert.equal(resource.start("asr", { format: "PCM" }, {}), true);
});

test("recorder resource stops an orphan native recorder start after owner release", () => {
  const recorder = recorderHarness();
  const { WechatRecorderResource } = require("../services/recorder-resource.ts");
  const resource = new WechatRecorderResource(() => recorder);

  assert.equal(resource.start("asr", { format: "PCM" }, {}), true);
  resource.forceRelease("asr");

  recorder.callbacks.start();

  assert.equal(recorder.stops, 1);
  assert.equal(resource.isBusy(), false);
});

test("recorder resource stops once when the start handler requests cancellation", async () => {
  const recorder = recorderHarness();
  const { WechatRecorderResource } = require("../services/recorder-resource.ts");
  const resource = new WechatRecorderResource(() => recorder);
  let stopped;
  resource.start("asr", { format: "PCM" }, {
    onStart: () => { stopped = resource.stopAndWait("asr"); },
  });

  recorder.callbacks.start();
  assert.equal(recorder.stops, 1);
  recorder.callbacks.stop();
  await stopped;
  assert.equal(resource.isBusy(), false);
});

test("recorder resource reports a real native start failure while cancellation is pending", async () => {
  const recorder = recorderHarness();
  const { WechatRecorderResource } = require("../services/recorder-resource.ts");
  const resource = new WechatRecorderResource(() => recorder);
  const errors = [];
  resource.start("asr", { format: "PCM" }, {
    onError: (message) => errors.push(message),
  });
  const stopped = resource.stopAndWait("asr");
  recorder.callbacks.error({ errMsg: "record:fail auth deny" });
  await stopped;

  assert.deepEqual(errors, ["record:fail auth deny"]);
  assert.equal(recorder.stops, 0);
  assert.equal(resource.isBusy(), false);
});
