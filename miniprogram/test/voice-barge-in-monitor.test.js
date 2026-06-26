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
      callbacks.stop?.();
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

function pcm16Frame(sample, byteLength = 3200) {
  const frame = new ArrayBuffer(byteLength);
  const view = new DataView(frame);
  for (let offset = 0; offset < byteLength; offset += 2) {
    view.setInt16(offset, sample, true);
  }
  return frame;
}

test("barge-in monitor triggers after sustained voice above playback baseline", () => {
  const recorder = recorderHarness();
  const { VoiceBargeInMonitor } = require("../services/voice-barge-in-monitor.ts");
  const monitor = new VoiceBargeInMonitor(() => recorder);
  let triggers = 0;

  const started = monitor.start({
    warmupMs: 300,
    triggerVoiceMs: 250,
    minTriggerLevel: 0.04,
    baselineMultiplier: 1.5,
    onBargeIn: () => {
      triggers += 1;
      void monitor.stopAndWait();
    },
  });

  assert.equal(started, true);
  assert.equal(recorder.starts.length, 1);
  for (let index = 0; index < 3; index += 1) {
    recorder.callbacks.frame({ frameBuffer: pcm16Frame(900), isLastFrame: false });
  }
  assert.equal(triggers, 0);

  for (let index = 0; index < 2; index += 1) {
    recorder.callbacks.frame({ frameBuffer: pcm16Frame(5000), isLastFrame: false });
  }
  assert.equal(triggers, 0);

  recorder.callbacks.frame({ frameBuffer: pcm16Frame(5000), isLastFrame: false });

  assert.equal(triggers, 1);
  assert.equal(monitor.isRunning(), false);
  assert.equal(recorder.stops, 1);
});

test("barge-in monitor detects user speech above uneven playback echo", () => {
  const recorder = recorderHarness();
  const { VoiceBargeInMonitor } = require("../services/voice-barge-in-monitor.ts");
  const monitor = new VoiceBargeInMonitor(() => recorder);
  let triggers = 0;

  const started = monitor.start({
    warmupMs: 300,
    triggerVoiceMs: 250,
    minTriggerLevel: 0.025,
    onBargeIn: () => {
      triggers += 1;
    },
  });

  assert.equal(started, true);
  recorder.callbacks.frame({ frameBuffer: pcm16Frame(900), isLastFrame: false });
  recorder.callbacks.frame({ frameBuffer: pcm16Frame(2600), isLastFrame: false });
  recorder.callbacks.frame({ frameBuffer: pcm16Frame(900), isLastFrame: false });
  assert.equal(triggers, 0);

  for (let index = 0; index < 3; index += 1) {
    recorder.callbacks.frame({ frameBuffer: pcm16Frame(3500), isLastFrame: false });
  }

  assert.equal(triggers, 1);
  assert.equal(monitor.isRunning(), false);
});

test("barge-in monitor waits for recorder onStop before releasing the microphone", async () => {
  const recorder = recorderHarness();
  recorder.stop = function stopWithoutImmediateCallback() {
    this.stops += 1;
  };
  const { VoiceBargeInMonitor } = require("../services/voice-barge-in-monitor.ts");
  const monitor = new VoiceBargeInMonitor(() => recorder);
  let stopped = false;

  monitor.start({ onBargeIn: () => {} });
  const stopPromise = monitor.stopAndWait().then(() => {
    stopped = true;
  });
  await Promise.resolve();

  assert.equal(recorder.stops, 1);
  assert.equal(stopped, false);

  recorder.callbacks.stop();
  await stopPromise;

  assert.equal(stopped, true);
  assert.equal(monitor.isRunning(), false);
});

test("barge-in monitor ignores stable assistant playback echo", () => {
  const recorder = recorderHarness();
  const { VoiceBargeInMonitor } = require("../services/voice-barge-in-monitor.ts");
  const monitor = new VoiceBargeInMonitor(() => recorder);
  let triggers = 0;

  monitor.start({
    warmupMs: 300,
    triggerVoiceMs: 250,
    minTriggerLevel: 0.04,
    baselineMultiplier: 1.5,
    onBargeIn: () => {
      triggers += 1;
    },
  });

  for (let index = 0; index < 10; index += 1) {
    recorder.callbacks.frame({ frameBuffer: pcm16Frame(2600), isLastFrame: false });
  }

  assert.equal(triggers, 0);
  assert.equal(monitor.isRunning(), true);
  monitor.stop();
  assert.equal(recorder.stops, 1);
});

test("barge-in monitor reports recorder start errors without staying active", () => {
  const recorder = recorderHarness();
  recorder.start = () => {
    throw new Error("recorder busy");
  };
  const { VoiceBargeInMonitor } = require("../services/voice-barge-in-monitor.ts");
  const monitor = new VoiceBargeInMonitor(() => recorder);
  let message = "";

  const started = monitor.start({
    onBargeIn: () => {},
    onError: (errorMessage) => {
      message = errorMessage;
    },
  });

  assert.equal(started, false);
  assert.equal(monitor.isRunning(), false);
  assert.equal(message, "recorder busy");
});
