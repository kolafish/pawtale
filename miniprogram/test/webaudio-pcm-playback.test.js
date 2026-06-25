require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function createFakeWebAudioContext() {
  const scheduled = [];
  const buffers = [];
  const context = {
    state: "suspended",
    currentTime: 10,
    sampleRate: 44100,
    destination: {
      connect() {
        return this;
      },
    },
    async resume() {
      this.state = "running";
    },
    async suspend() {
      this.state = "suspended";
    },
    async close() {
      this.state = "closed";
    },
    createBuffer(channels, length, sampleRate) {
      const channelData = Array.from({ length: channels }, () => new Float32Array(length));
      const buffer = {
        sampleRate,
        length,
        duration: length / sampleRate,
        numberOfChannels: channels,
        getChannelData(channel) {
          return channelData[channel];
        },
        channelData,
      };
      buffers.push(buffer);
      return buffer;
    },
    createBufferSource() {
      const source = {
        buffer: null,
        connected: false,
        disconnected: false,
        stopped: false,
        onended: null,
        connect(destination) {
          this.connected = destination === context.destination;
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
  return { context, scheduled, buffers };
}

test("pcm16leToAudioBuffer converts little-endian PCM to normalized channels", () => {
  delete require.cache[require.resolve("../services/webaudio-pcm-playback.ts")];
  const { pcm16leToAudioBuffer } = require("../services/webaudio-pcm-playback.ts");
  const { context, buffers } = createFakeWebAudioContext();
  const pcm = new Uint8Array([
    0x00, 0x00,
    0xff, 0x7f,
    0x00, 0x80,
    0x00, 0x40,
  ]);

  const buffer = pcm16leToAudioBuffer(context, pcm, {
    sampleRate: 16000,
    channels: 2,
    bitsPerSample: 16,
  });

  assert.equal(buffer, buffers[0]);
  assert.equal(buffer.numberOfChannels, 2);
  assert.equal(buffer.length, 2);
  assert.equal(buffer.sampleRate, 16000);
  assert.deepEqual(Array.from(buffer.getChannelData(0)), [0, -1]);
  assert.equal(Math.round(buffer.getChannelData(1)[0] * 32767), 32767);
  assert.equal(Math.round(buffer.getChannelData(1)[1] * 32767), 16384);
});

test("WebAudioPCMPlaybackQueue schedules PCM chunks without file playback gaps", async () => {
  delete require.cache[require.resolve("../services/webaudio-pcm-playback.ts")];
  const { WebAudioPCMPlaybackQueue } = require("../services/webaudio-pcm-playback.ts");
  const { context, scheduled } = createFakeWebAudioContext();
  const events = [];
  const queue = new WebAudioPCMPlaybackQueue({
    createContext: () => context,
    minLeadTimeSeconds: 0.2,
    onSegmentScheduled: (event) => events.push(event),
  });
  const oneSecondPcm = new Uint8Array(16000 * 2);

  const first = await queue.appendPCM16LE(oneSecondPcm, {
    sampleRate: 16000,
    channels: 1,
    bitsPerSample: 16,
  });
  const second = await queue.appendPCM16LE(oneSecondPcm, {
    sampleRate: 16000,
    channels: 1,
    bitsPerSample: 16,
  });

  assert.equal(context.state, "running");
  assert.equal(first.startAtSeconds, 10.2);
  assert.equal(second.startAtSeconds, 11.2);
  assert.deepEqual(scheduled.map((item) => item.when), [10.2, 11.2]);
  assert.equal(events.length, 2);
  assert.equal(queue.stats().segmentCount, 2);
  assert.equal(queue.stats().underrunCount, 0);
});

test("WebAudioPCMPlaybackQueue records underruns when chunks arrive too late", async () => {
  delete require.cache[require.resolve("../services/webaudio-pcm-playback.ts")];
  const { WebAudioPCMPlaybackQueue } = require("../services/webaudio-pcm-playback.ts");
  const { context, scheduled } = createFakeWebAudioContext();
  const underruns = [];
  const queue = new WebAudioPCMPlaybackQueue({
    createContext: () => context,
    minLeadTimeSeconds: 0.2,
    onUnderrun: (event) => underruns.push(event),
  });
  const halfSecondPcm = new Uint8Array(16000);

  await queue.appendPCM16LE(halfSecondPcm, {
    sampleRate: 16000,
    channels: 1,
    bitsPerSample: 16,
  });
  context.currentTime = 12;
  await queue.appendPCM16LE(halfSecondPcm, {
    sampleRate: 16000,
    channels: 1,
    bitsPerSample: 16,
  });

  assert.equal(underruns.length, 1);
  assert.equal(underruns[0].segmentIndex, 1);
  assert.equal(scheduled[1].when, 12.2);
  assert.equal(queue.stats().underrunCount, 1);
});

test("sinePCM16LEChunk keeps phase continuous across chunks", () => {
  delete require.cache[require.resolve("../services/webaudio-pcm-playback.ts")];
  const { sinePCM16LEChunk } = require("../services/webaudio-pcm-playback.ts");

  const first = sinePCM16LEChunk({
    frequencyHz: 440,
    durationSeconds: 0.1,
    sampleRate: 16000,
    channels: 1,
  });
  const second = sinePCM16LEChunk({
    frequencyHz: 440,
    durationSeconds: 0.1,
    sampleRate: 16000,
    channels: 1,
    phase: first.nextPhase,
  });

  assert.equal(first.pcm.byteLength, 1600 * 2);
  assert.equal(second.pcm.byteLength, 1600 * 2);
  assert.notEqual(first.nextPhase, 0);
  assert.notEqual(second.nextPhase, first.nextPhase);
});
