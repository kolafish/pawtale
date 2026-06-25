require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function createFakeWebAudioContext() {
  const scheduled = [];
  const context = {
    state: "running",
    currentTime: 3,
    destination: {
      connect() {
        return this;
      },
    },
    async resume() {
      this.state = "running";
    },
    async close() {
      this.state = "closed";
    },
    createBuffer(channels, length, sampleRate) {
      const channelData = Array.from({ length: channels }, () => new Float32Array(length));
      return {
        sampleRate,
        length,
        duration: length / sampleRate,
        numberOfChannels: channels,
        getChannelData(channel) {
          return channelData[channel];
        },
      };
    },
    createBufferSource() {
      const source = {
        buffer: null,
        stopped: false,
        disconnected: false,
        onended: null,
        connect(destination) {
          return destination;
        },
        disconnect() {
          this.disconnected = true;
        },
        start(when) {
          scheduled.push({ source: this, when });
        },
        stop() {
          this.stopped = true;
        },
      };
      return source;
    },
  };
  return { context, scheduled };
}

test("StreamingSpeechPlaybackController plays PCM frames through WebAudio", async () => {
  delete require.cache[require.resolve("../services/streaming-speech-playback.ts")];
  const { StreamingSpeechPlaybackController } = require("../services/streaming-speech-playback.ts");
  const { context, scheduled } = createFakeWebAudioContext();
  const scheduledEvents = [];
  const endedEvents = [];
  const controller = new StreamingSpeechPlaybackController({
    createContext: () => context,
    minLeadTimeSeconds: 0.1,
    onFrameScheduled: (event) => scheduledEvents.push(event),
    onFrameEnded: (event) => endedEvents.push(event),
  });

  const event = await controller.appendFrame({
    audio: new Uint8Array(16000 * 2),
    mimeType: "audio/pcm",
    sampleRate: 16000,
    channels: 1,
    bitsPerSample: 16,
  });
  scheduled[0].source.onended();
  await controller.waitForDrain();

  assert.equal(event.frameIndex, 0);
  assert.equal(event.startAtSeconds, 3.1);
  assert.equal(scheduledEvents.length, 1);
  assert.equal(endedEvents.length, 1);
  assert.equal(controller.stats().segmentCount, 1);
});

test("StreamingSpeechPlaybackController rejects non-PCM frames instead of falling back", async () => {
  delete require.cache[require.resolve("../services/streaming-speech-playback.ts")];
  const { StreamingSpeechPlaybackController } = require("../services/streaming-speech-playback.ts");
  const { context } = createFakeWebAudioContext();
  const controller = new StreamingSpeechPlaybackController({
    createContext: () => context,
  });

  await assert.rejects(
    () => controller.appendFrame({
      audio: new Uint8Array([1, 2, 3]),
      mimeType: "audio/mpeg",
      sampleRate: 16000,
      channels: 1,
      bitsPerSample: 16,
    }),
    /只支持 PCM 音频帧/,
  );
});

test("StreamingSpeechPlaybackController stop drains pending playback without ended callback", async () => {
  delete require.cache[require.resolve("../services/streaming-speech-playback.ts")];
  const { StreamingSpeechPlaybackController } = require("../services/streaming-speech-playback.ts");
  const { context, scheduled } = createFakeWebAudioContext();
  const endedEvents = [];
  const controller = new StreamingSpeechPlaybackController({
    createContext: () => context,
    onFrameEnded: (event) => endedEvents.push(event),
  });

  await controller.appendFrame({
    audio: new Uint8Array(16000 * 2),
    sampleRate: 16000,
    channels: 1,
    bitsPerSample: 16,
  });
  controller.stop();
  await controller.waitForDrain();

  assert.equal(scheduled[0].source.stopped, true);
  assert.equal(scheduled[0].source.disconnected, true);
  assert.equal(endedEvents.length, 0);
});
