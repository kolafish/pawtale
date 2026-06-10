require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function playerModule() {
  delete require.cache[require.resolve("../services/memoir-tts-player.ts")];
  return require("../services/memoir-tts-player.ts");
}

test("builds stable TTS playback source keys", () => {
  const { memoirTtsSourceKey } = playerModule();

  assert.equal(
    memoirTtsSourceKey({ source_type: "question", source_id: "question_1" }),
    "question:question_1",
  );
});

test("normalizes TTS playback speed to supported steps", () => {
  const { normalizeMemoirTtsPlaybackSpeed } = playerModule();

  assert.equal(normalizeMemoirTtsPlaybackSpeed(0.6), 0.75);
  assert.equal(normalizeMemoirTtsPlaybackSpeed(1.18), 1.25);
  assert.equal(normalizeMemoirTtsPlaybackSpeed(9), 2);
  assert.equal(normalizeMemoirTtsPlaybackSpeed(Number.NaN), 1);
});

test("exports the supported TTS playback speed steps", () => {
  const { MEMOIR_TTS_PLAYBACK_SPEEDS } = playerModule();

  assert.deepEqual(MEMOIR_TTS_PLAYBACK_SPEEDS, [0.75, 1, 1.25, 1.5, 2]);
});

test("normalizes TTS voice ids to supported voices", () => {
  const { normalizeMemoirTtsVoice } = playerModule();

  assert.equal(normalizeMemoirTtsVoice("wenrounvsheng"), "wenrounvsheng");
  assert.equal(normalizeMemoirTtsVoice("unknown-voice"), "cixingnansheng");
});

test("changing TTS voice clears prepared playback source", () => {
  const {
    readMemoirTtsPlaybackState,
    setMemoirTtsPlaybackVoice,
  } = playerModule();

  setMemoirTtsPlaybackVoice("wenrounvsheng");

  const state = readMemoirTtsPlaybackState();
  assert.equal(state.voice, "wenrounvsheng");
  assert.equal(state.source_key, "");
  assert.equal(state.temp_url, "");
});

test("preloads TTS playback without changing visible playback state", async () => {
  global.wx = {
    cloud: {
      getTempFileURL({ fileList, success }) {
        success({
          fileList: fileList.map((fileID) => ({
            fileID,
            tempFileURL: `https://example.test/${encodeURIComponent(fileID)}`,
          })),
        });
      },
    },
  };

  const memoir = require("../services/memoir.ts");
  const originalCreate = memoir.createMemoirTtsAsset;
  const originalProcess = memoir.processMemoirTtsJob;
  let createCalls = 0;
  let processCalls = 0;
  memoir.createMemoirTtsAsset = async () => {
    createCalls += 1;
    return {
      asset: {
        id: "tts_asset_1",
        file_id: "cloud://tts/question-q1.mp3",
      },
    };
  };
  memoir.processMemoirTtsJob = async () => {
    processCalls += 1;
    throw new Error("should not process reusable asset");
  };

  try {
    const {
      invalidateMemoirTtsPlayback,
      preloadMemoirTtsPlayback,
      prepareMemoirTtsPlayback,
      readMemoirTtsPlaybackState,
    } = playerModule();
    const source = { source_type: "question", source_id: "q1", label: "问题" };
    const beforePreload = readMemoirTtsPlaybackState();

    await preloadMemoirTtsPlayback(source);
    assert.equal(createCalls, 1);
    assert.equal(processCalls, 0);
    assert.deepEqual(readMemoirTtsPlaybackState(), beforePreload);

    const prepared = await prepareMemoirTtsPlayback(source);
    assert.equal(createCalls, 1);
    assert.equal(prepared.status, "ready");
    assert.equal(prepared.source_key, "question:q1");
    assert.equal(prepared.temp_url, "https://example.test/cloud%3A%2F%2Ftts%2Fquestion-q1.mp3");

    invalidateMemoirTtsPlayback(source);
    assert.equal(readMemoirTtsPlaybackState().source_key, "");
    await prepareMemoirTtsPlayback(source);
    assert.equal(createCalls, 2);
  } finally {
    memoir.createMemoirTtsAsset = originalCreate;
    memoir.processMemoirTtsJob = originalProcess;
    delete global.wx;
  }
});

test("invalidated in-flight TTS preload is not reused", async () => {
  global.wx = {
    cloud: {
      getTempFileURL({ fileList, success }) {
        success({
          fileList: fileList.map((fileID) => ({
            fileID,
            tempFileURL: `https://example.test/${encodeURIComponent(fileID)}`,
          })),
        });
      },
    },
  };

  const memoir = require("../services/memoir.ts");
  const originalCreate = memoir.createMemoirTtsAsset;
  const originalProcess = memoir.processMemoirTtsJob;
  let createCalls = 0;
  let resolveFirstCreate;
  memoir.createMemoirTtsAsset = async () => {
    createCalls += 1;
    if (createCalls === 1) {
      return new Promise((resolve) => {
        resolveFirstCreate = resolve;
      });
    }
    return {
      asset: {
        id: "tts_asset_new",
        file_id: "cloud://tts/new.mp3",
      },
    };
  };
  memoir.processMemoirTtsJob = async () => {
    throw new Error("should not process reusable asset");
  };

  try {
    const {
      invalidateMemoirTtsPlayback,
      preloadMemoirTtsPlayback,
      prepareMemoirTtsPlayback,
    } = playerModule();
    const source = { source_type: "answer", source_id: "a1", label: "回答" };
    const preloadPromise = preloadMemoirTtsPlayback(source);

    invalidateMemoirTtsPlayback(source);
    resolveFirstCreate({
      asset: {
        id: "tts_asset_old",
        file_id: "cloud://tts/old.mp3",
      },
    });
    await preloadPromise;

    const prepared = await prepareMemoirTtsPlayback(source);
    assert.equal(createCalls, 2);
    assert.equal(prepared.temp_url, "https://example.test/cloud%3A%2F%2Ftts%2Fnew.mp3");
  } finally {
    memoir.createMemoirTtsAsset = originalCreate;
    memoir.processMemoirTtsJob = originalProcess;
    delete global.wx;
  }
});
