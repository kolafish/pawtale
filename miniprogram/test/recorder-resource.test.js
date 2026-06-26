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
