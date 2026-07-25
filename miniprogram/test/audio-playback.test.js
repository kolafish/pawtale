require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

test("configureAudiblePlayback disables mute switch and sets full volume", () => {
  const calls = [];
  global.wx = {
    setInnerAudioOption(options) {
      calls.push(options);
    },
  };
  const audio = {
    obeyMuteSwitch: true,
    volume: 0.2,
  };
  delete require.cache[require.resolve("../services/audio-playback.ts")];
  const { configureAudiblePlayback } = require("../services/audio-playback.ts");

  configureAudiblePlayback(audio);

  assert.deepEqual(calls, [{ obeyMuteSwitch: false, mixWithOther: true }]);
  assert.equal(audio.obeyMuteSwitch, false);
  assert.equal(audio.volume, 1);

  delete global.wx;
});

test("playAudioFileSequence preserves order and stops when the token is stale", async () => {
  delete require.cache[require.resolve("../services/audio-playback.ts")];
  const { playAudioFileSequence } = require("../services/audio-playback.ts");
  const played = [];
  let current = true;

  const completed = await playAudioFileSequence(
    ["audio-1", "audio-2", "audio-3"],
    {
      token: 7,
      isCurrent: () => current,
      async playFile(audioFileId, index) {
        played.push([audioFileId, index]);
        if (index === 1) {
          current = false;
        }
      },
    },
  );

  assert.equal(completed, false);
  assert.deepEqual(played, [
    ["audio-1", 0],
    ["audio-2", 1],
  ]);
  await assert.rejects(
    playAudioFileSequence([], {
      token: 8,
      isCurrent: () => true,
      async playFile() {},
    }),
    /没有可播放的音频/,
  );
});

function createManualAudioContext() {
  const callbacks = {};
  const audio = {
    src: "",
    obeyMuteSwitch: true,
    volume: 0,
    stopCalls: 0,
    destroyCalls: 0,
    playCalls: 0,
    onPlay(callback) {
      callbacks.play = callback;
    },
    onTimeUpdate(callback) {
      callbacks.timeUpdate = callback;
    },
    onEnded(callback) {
      callbacks.ended = callback;
    },
    onStop(callback) {
      callbacks.stop = callback;
    },
    onError(callback) {
      callbacks.error = callback;
    },
    play() {
      this.playCalls += 1;
    },
    stop() {
      this.stopCalls += 1;
      callbacks.stop?.();
    },
    destroy() {
      this.destroyCalls += 1;
    },
  };
  return { audio, callbacks };
}

test("AudioPlaybackController plays and releases an audio file", async () => {
  delete require.cache[require.resolve("../services/audio-playback.ts")];
  const { AudioPlaybackController } = require("../services/audio-playback.ts");
  const created = createManualAudioContext();
  const configured = [];
  const downloads = [];
  const controller = new AudioPlaybackController({
    downloadAudioFile: async (audioFileId) => {
      downloads.push(audioFileId);
      return `/tmp/${audioFileId}.mp3`;
    },
    createAudioContext: () => created.audio,
    configureAudio: (audio) => {
      configured.push(audio);
    },
  });

  const token = controller.nextToken();
  const promise = controller.playFile("audio-1", token);
  await Promise.resolve();

  assert.deepEqual(downloads, ["audio-1"]);
  assert.equal(created.audio.src, "/tmp/audio-1.mp3");
  assert.equal(created.audio.playCalls, 1);
  assert.deepEqual(configured, [created.audio]);

  created.callbacks.ended();
  await promise;

  assert.equal(created.audio.destroyCalls, 1);
});

test("AudioPlaybackController can play an existing temp file without download", async () => {
  delete require.cache[require.resolve("../services/audio-playback.ts")];
  const { AudioPlaybackController } = require("../services/audio-playback.ts");
  const created = createManualAudioContext();
  const downloads = [];
  const controller = new AudioPlaybackController({
    downloadAudioFile: async (audioFileId) => {
      downloads.push(audioFileId);
      return `/tmp/${audioFileId}.mp3`;
    },
    createAudioContext: () => created.audio,
    configureAudio: () => {},
  });

  const token = controller.nextToken();
  const promise = controller.playTempFile("/tmp/segment.wav", token);
  await Promise.resolve();

  assert.deepEqual(downloads, []);
  assert.equal(created.audio.src, "/tmp/segment.wav");
  assert.equal(created.audio.playCalls, 1);

  created.callbacks.ended();
  await promise;
});

test("AudioPlaybackController exposes current audio while playback callbacks run", async () => {
  delete require.cache[require.resolve("../services/audio-playback.ts")];
  const { AudioPlaybackController } = require("../services/audio-playback.ts");
  const created = createManualAudioContext();
  const seen = [];
  const controller = new AudioPlaybackController({
    downloadAudioFile: async (audioFileId) => `/tmp/${audioFileId}.mp3`,
    createAudioContext: () => created.audio,
    configureAudio: () => {},
  });

  const token = controller.nextToken();
  const promise = controller.playFile("audio-1", token, {
    onPlay: (audio) => seen.push(["play", audio === controller.currentAudio()]),
    onTimeUpdate: (audio) => seen.push(["time", audio === controller.currentAudio()]),
    onEnded: (audio) => seen.push(["ended", audio === controller.currentAudio()]),
  });
  await Promise.resolve();

  created.callbacks.play();
  created.callbacks.timeUpdate();
  created.callbacks.ended();
  await promise;

  assert.deepEqual(seen, [
    ["play", true],
    ["time", true],
    ["ended", true],
  ]);
  assert.equal(controller.currentAudio(), null);
});

test("AudioPlaybackController stop cancels the active token and resolves playback", async () => {
  delete require.cache[require.resolve("../services/audio-playback.ts")];
  const { AudioPlaybackController } = require("../services/audio-playback.ts");
  const created = createManualAudioContext();
  const controller = new AudioPlaybackController({
    downloadAudioFile: async (audioFileId) => `/tmp/${audioFileId}.mp3`,
    createAudioContext: () => created.audio,
    configureAudio: () => {},
  });

  const token = controller.nextToken();
  const promise = controller.playFile("audio-1", token);
  await Promise.resolve();
  controller.stop();
  await promise;

  assert.notEqual(controller.currentToken(), token);
  assert.equal(created.audio.stopCalls, 1);
  assert.equal(created.audio.destroyCalls, 1);
});

test("AudioPlaybackController skips stale playback after download", async () => {
  delete require.cache[require.resolve("../services/audio-playback.ts")];
  const { AudioPlaybackController } = require("../services/audio-playback.ts");
  let createCalls = 0;
  const controller = new AudioPlaybackController({
    downloadAudioFile: async (audioFileId) => `/tmp/${audioFileId}.mp3`,
    createAudioContext: () => {
      createCalls += 1;
      return createManualAudioContext().audio;
    },
    configureAudio: () => {},
  });

  const staleToken = controller.nextToken();
  controller.nextToken();
  await controller.playFile("audio-1", staleToken);

  assert.equal(createCalls, 0);
});

test("AudioPlaybackController releases audio on playback error", async () => {
  delete require.cache[require.resolve("../services/audio-playback.ts")];
  const { AudioPlaybackController } = require("../services/audio-playback.ts");
  const created = createManualAudioContext();
  const controller = new AudioPlaybackController({
    downloadAudioFile: async (audioFileId) => `/tmp/${audioFileId}.mp3`,
    createAudioContext: () => created.audio,
    configureAudio: () => {},
  });

  const token = controller.nextToken();
  const promise = controller.playFile("audio-1", token);
  await Promise.resolve();
  created.callbacks.error({ errMsg: "boom" });

  await assert.rejects(promise, /boom/);
  assert.equal(created.audio.destroyCalls, 1);
});
