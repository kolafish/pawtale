require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function createFakeWebAudioContext(options = {}) {
  const scheduled = [];
  const buffers = [];
  const sources = [];
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
          if (options.throwOnStart) {
            throw new Error("start failed");
          }
          scheduled.push({ source: this, when });
        },
        stop() {
          this.stopped = true;
        },
      };
      sources.push(source);
      return source;
    },
  };
  return { context, scheduled, buffers, sources };
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

test("pcm16leToAudioBuffer rejects invalid PCM format", () => {
  delete require.cache[require.resolve("../services/webaudio-pcm-playback.ts")];
  const { pcm16leToAudioBuffer } = require("../services/webaudio-pcm-playback.ts");
  const { context } = createFakeWebAudioContext();

  assert.throws(
    () => pcm16leToAudioBuffer(context, new Uint8Array(2), {
      sampleRate: 4000,
      channels: 1,
      bitsPerSample: 16,
    }),
    /有效采样率/,
  );
  assert.throws(
    () => pcm16leToAudioBuffer(context, new Uint8Array(2), {
      sampleRate: 16000,
      channels: 0,
      bitsPerSample: 16,
    }),
    /有效声道数/,
  );
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
  await queue.destroy();
});

test("WebAudioPCMPlaybackQueue configures audible playback before creating WebAudio context", async () => {
  delete require.cache[require.resolve("../services/webaudio-pcm-playback.ts")];
  const calls = [];
  const order = [];
  global.wx = {
    setInnerAudioOption(options) {
      order.push("configure");
      calls.push(options);
    },
  };
  const { WebAudioPCMPlaybackQueue } = require("../services/webaudio-pcm-playback.ts");
  const { context } = createFakeWebAudioContext();
  const queue = new WebAudioPCMPlaybackQueue({
    createContext: () => {
      order.push("createContext");
      return context;
    },
  });
  const oneSecondPcm = new Uint8Array(16000 * 2);

  try {
    await queue.appendPCM16LE(oneSecondPcm, {
      sampleRate: 16000,
      channels: 1,
      bitsPerSample: 16,
    });
    await queue.appendPCM16LE(oneSecondPcm, {
      sampleRate: 16000,
      channels: 1,
      bitsPerSample: 16,
    });
  } finally {
    await queue.destroy();
    delete global.wx;
  }

  assert.deepEqual(order.slice(0, 2), ["configure", "createContext"]);
  assert.deepEqual(calls, [{ obeyMuteSwitch: false, mixWithOther: true }]);
});

test("WebAudioPCMPlaybackQueue releases source when scheduling fails", async () => {
  delete require.cache[require.resolve("../services/webaudio-pcm-playback.ts")];
  const { WebAudioPCMPlaybackQueue } = require("../services/webaudio-pcm-playback.ts");
  const { context, sources } = createFakeWebAudioContext({ throwOnStart: true });
  const queue = new WebAudioPCMPlaybackQueue({
    createContext: () => context,
  });

  await assert.rejects(
    () => queue.appendPCM16LE(new Uint8Array(16000 * 2), {
      sampleRate: 16000,
      channels: 1,
      bitsPerSample: 16,
    }),
    /start failed/,
  );

  assert.equal(sources[0].disconnected, true);
  assert.equal(queue.stats().segmentCount, 0);
});

test("WebAudioPCMPlaybackQueue completes segments when WebAudio onended is missing", async () => {
  delete require.cache[require.resolve("../services/webaudio-pcm-playback.ts")];
  const { WebAudioPCMPlaybackQueue } = require("../services/webaudio-pcm-playback.ts");
  const { context, scheduled } = createFakeWebAudioContext();
  const endedEvents = [];
  const queue = new WebAudioPCMPlaybackQueue({
    createContext: () => context,
    minLeadTimeSeconds: 0,
    completionGraceSeconds: 0,
    completionPollIntervalMs: 1,
    onSegmentEnded: (event) => endedEvents.push(event),
  });

  const event = await queue.appendPCM16LE(new Uint8Array(32), {
    sampleRate: 16000,
    channels: 1,
    bitsPerSample: 16,
  });
  context.currentTime = event.startAtSeconds + event.durationSeconds;
  await Promise.race([
    event.ended,
    new Promise((_, reject) => setTimeout(() => reject(new Error("segment did not end")), 100)),
  ]);

  assert.equal(scheduled.length, 1);
  assert.equal(scheduled[0].source.disconnected, true);
  assert.equal(endedEvents.length, 1);
  assert.equal(endedEvents[0].segmentIndex, 0);
  assert.equal(queue.stats().segmentCount, 1);
});

test("WebAudioPCMPlaybackQueue resolves pending segment when stopped", async () => {
  delete require.cache[require.resolve("../services/webaudio-pcm-playback.ts")];
  const { WebAudioPCMPlaybackQueue } = require("../services/webaudio-pcm-playback.ts");
  const { context, scheduled } = createFakeWebAudioContext();
  const endedEvents = [];
  const queue = new WebAudioPCMPlaybackQueue({
    createContext: () => context,
    minLeadTimeSeconds: 0.2,
    onSegmentEnded: (event) => endedEvents.push(event),
  });

  const event = await queue.appendPCM16LE(new Uint8Array(16000 * 2), {
    sampleRate: 16000,
    channels: 1,
    bitsPerSample: 16,
  });
  queue.stop();
  await event.ended;

  assert.equal(scheduled[0].source.stopped, true);
  assert.equal(scheduled[0].source.disconnected, true);
  assert.equal(endedEvents.length, 0);
  assert.equal(queue.stats().queuedAheadSeconds, 0);
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
  await queue.destroy();
});
