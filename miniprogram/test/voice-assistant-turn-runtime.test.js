require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  VoiceAssistantTurnRuntime,
} = require("../services/voice-assistant-turn-runtime.ts");

function completedTurn(overrides = {}) {
  return {
    type: "turn.completed",
    turn_id: "turn-1",
    voice_session_id: "voice-1",
    state: "completed",
    message_id: "assistant-1",
    text: "那时候谁也在院子里？",
    primary_intent: "follow_up",
    intent_tags: ["follow_up"],
    expects_answer: true,
    ...overrides,
  };
}

test("provider audio failure stops queued audio while the full text still completes", async () => {
  const events = [];
  const runtime = new VoiceAssistantTurnRuntime("voice-1", {
    onCompleted: event => events.push(event.text),
    onPlaybackError: error => events.push(error.message),
  }, {
    createStreamClient: (_id, callbacks) => ({
      async start() {
        callbacks.onAudioFrame({sequence: 1, audio: new Uint8Array([1, 2])});
        callbacks.onAudioFailed({type: "audio.failed", error: "看着文字回答"});
        callbacks.onTextDelta({type: "text.delta", text: "那时候谁也在院子里？"});
        const completed = completedTurn({audio_status: "failed"});
        callbacks.onCompleted(completed);
        return completed;
      }, cancel() {}, close() {},
    }),
    createPlaybackController: () => { throw new Error("failed audio must not be played"); },
  });
  const result = await runtime.start();
  assert.equal(result.completed.message_id, "assistant-1");
  assert.equal(result.playbackError.message, "看着文字回答");
  assert.deepEqual(events, ["看着文字回答", result.completed.text]);
});

test("voice assistant turn runtime streams text, plays frames, and waits for drain", async () => {
  const events = [];
  const appended = [];
  const runtime = new VoiceAssistantTurnRuntime("voice-1", {
    onTextDelta: (event) => events.push(["text", event.text]),
    onAudioFrame: (frame) => events.push(["frame", frame.sequence]),
    onAudioFrameScheduled: (event) => events.push(["scheduled", event.frameIndex]),
    onAudioDrained: (event) => events.push(["drained", event.frame_count]),
    onCompleted: (event) => events.push(["completed", event.message_id]),
  }, {
    createStreamClient: (_voiceSessionId, callbacks) => ({
      async start(options) {
        events.push(["start", options.regenerate]);
        callbacks.onTextDelta?.({ type: "text.delta", text: "那时候" });
        callbacks.onAudioFrame?.({
          turnId: "turn-1",
          sequence: 1,
          audio: new Uint8Array([1, 2]),
          mimeType: "audio/L16",
          sampleRate: 16000,
          channels: 1,
          bitsPerSample: 16,
        });
        callbacks.onAudioDone?.({
          type: "audio.done",
          turn_id: "turn-1",
          voice_session_id: "voice-1",
          frame_count: 1,
          duration_seconds: 0.3,
        });
        const completed = completedTurn();
        callbacks.onCompleted?.(completed);
        return completed;
      },
      cancel() {},
      close() {},
    }),
    createPlaybackController: () => ({
      async appendFrame(frame) {
        appended.push(frame);
        return {
          frameIndex: 0,
          startAtSeconds: 10,
          durationSeconds: 0.3,
          ended: Promise.resolve(),
        };
      },
      async waitForDrain() {
        events.push(["waitForDrain"]);
      },
      stats() {
        return {
          segmentCount: 1,
          underrunCount: 0,
          nextPlayTimeSeconds: 10.3,
          currentTimeSeconds: 10.3,
          queuedAheadSeconds: 0,
        };
      },
      stop() {},
      async destroy() {
        events.push(["destroy"]);
      },
    }),
  });

  const result = await runtime.start({ regenerate: true });

  assert.equal(result.completed.message_id, "assistant-1");
  assert.equal(result.playbackError, null);
  assert.equal(appended.length, 1);
  assert.deepEqual(events, [
    ["start", true],
    ["text", "那时候"],
    ["completed", "assistant-1"],
    ["frame", 1],
    ["scheduled", 0],
    ["waitForDrain"],
    ["drained", 1],
    ["destroy"],
  ]);
});

test("voice assistant turn runtime returns completed turn with playback error", async () => {
  const errors = [];
  const drained = [];
  const runtime = new VoiceAssistantTurnRuntime("voice-1", {
    onAudioDrained: (event) => drained.push(event),
    onPlaybackError: (error) => errors.push(error.message),
  }, {
    createStreamClient: (_voiceSessionId, callbacks) => ({
      async start() {
        callbacks.onAudioFrame?.({
          turnId: "turn-1",
          sequence: 1,
          audio: new Uint8Array([1, 2]),
          mimeType: "audio/L16",
          sampleRate: 16000,
          channels: 1,
          bitsPerSample: 16,
        });
        callbacks.onAudioDone?.({
          type: "audio.done",
          turn_id: "turn-1",
          voice_session_id: "voice-1",
          frame_count: 1,
        });
        return completedTurn();
      },
      cancel() {},
      close() {},
    }),
    createPlaybackController: () => ({
      async appendFrame() {
        throw new Error("pcm audio failed");
      },
      async waitForDrain() {
        throw new Error("should not wait after playback failure");
      },
      stats() {
        return {
          segmentCount: 0,
          underrunCount: 0,
          nextPlayTimeSeconds: 0,
          currentTimeSeconds: 0,
          queuedAheadSeconds: 0,
        };
      },
      stop() {},
      async destroy() {},
    }),
  });

  const result = await runtime.start();

  assert.equal(result.completed.message_id, "assistant-1");
  assert.equal(result.playbackError.message, "pcm audio failed");
  assert.deepEqual(errors, ["pcm audio failed"]);
  assert.deepEqual(drained, []);
});

test("voice assistant turn runtime suppresses stale callbacks", async () => {
  const events = [];
  const runtime = new VoiceAssistantTurnRuntime("voice-1", {
    onTextDelta: () => events.push("text"),
  }, {
    isActive: () => false,
    createStreamClient: (_voiceSessionId, callbacks) => ({
      async start() {
        callbacks.onTextDelta?.({ type: "text.delta", text: "stale" });
        return completedTurn();
      },
      cancel() {}, close() {},
    }),
  });

  await runtime.start();

  assert.deepEqual(events, []);
});

test("voice assistant turn runtime cancel stops stream and playback", async () => {
  const cancelReasons = [];
  let stopped = false;
  let destroyed = false;
  const runtime = new VoiceAssistantTurnRuntime("voice-1", {}, {
    createStreamClient: (_voiceSessionId, callbacks) => ({
      async start() {
        callbacks.onAudioFrame?.({
          turnId: "turn-1",
          sequence: 1,
          audio: new Uint8Array([1, 2]),
          mimeType: "audio/L16",
          sampleRate: 16000,
          channels: 1,
          bitsPerSample: 16,
        });
        return new Promise(() => {});
      },
      cancel(reason) {
        cancelReasons.push(reason);
      },
      close() {},
    }),
    createPlaybackController: () => ({
      async appendFrame() {
        return {
          frameIndex: 0,
          startAtSeconds: 0,
          durationSeconds: 0.1,
          ended: Promise.resolve(),
        };
      },
      async waitForDrain() {},
      stats() {
        return {
          segmentCount: 0,
          underrunCount: 0,
          nextPlayTimeSeconds: 0,
          currentTimeSeconds: 0,
          queuedAheadSeconds: 0,
        };
      },
      stop() {
        stopped = true;
      },
      async destroy() {
        destroyed = true;
      },
    }),
  });

  void runtime.start();
  await new Promise((resolve) => setImmediate(resolve));
  runtime.cancel("interrupted_by_user");

  assert.deepEqual(cancelReasons, ["interrupted_by_user"]);
  assert.equal(stopped, true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(destroyed, true);
});
