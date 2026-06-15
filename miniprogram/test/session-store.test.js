require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const STORE_KEY = "pawtale_memoir_session";

function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function installWxStorageMock(initialStorage = {}, failWrites = 0) {
  const storage = clone(initialStorage) || {};
  const writes = [];
  let remainingFailures = failWrites;

  global.wx = {
    getStorageSync(key) {
      return clone(storage[key]);
    },
    setStorageSync(key, value) {
      writes.push({ key, value: clone(value) });
      if (remainingFailures > 0) {
        remainingFailures -= 1;
        throw new Error("storage full");
      }
      storage[key] = clone(value);
    },
    removeStorageSync(key) {
      delete storage[key];
    },
  };

  return { storage, writes };
}

function sessionStoreModule() {
  delete require.cache[require.resolve("../stores/session.ts")];
  return require("../stores/session.ts");
}

function memoirCacheModule() {
  delete require.cache[require.resolve("../stores/session.ts")];
  delete require.cache[require.resolve("../stores/memoir-cache.ts")];
  return require("../stores/memoir-cache.ts");
}

test("writeMemoirStore persists fallback when voice session cache is too large", () => {
  const mock = installWxStorageMock({}, 1);
  const { replaceMemoirStore } = sessionStoreModule();

  const result = replaceMemoirStore({
    cache_identity: { user_id: "user-1", project_id: "project-1" },
    voice_session_cache: {
      version: 3,
      sessions: {
        voice_1: {
          transcript_lines: [{ id: "line-1", text: "第一句话" }],
          cached_at: "2026-06-08T00:00:00.000Z",
        },
      },
    },
  });

  assert.equal(mock.writes.length, 2);
  assert.equal(result.voice_session_cache, undefined);
  assert.equal(typeof result.cache_write_failed_at, "string");
  assert.deepEqual(mock.storage[STORE_KEY], result);
});

test("writeMemoirStore marks unpersisted result when storage write fails without fallback", () => {
  installWxStorageMock({}, 1);
  const { replaceMemoirStore } = sessionStoreModule();

  const result = replaceMemoirStore({
    cache_identity: { user_id: "user-1", project_id: "project-1" },
  });

  assert.equal(typeof result.cache_write_failed_at, "string");
});

test("writeMemoirStore clears previous write failure marker after successful write", () => {
  const mock = installWxStorageMock({
    [STORE_KEY]: {
      cache_identity: { user_id: "user-1", project_id: "project-1" },
      cache_write_failed_at: "2026-06-08T00:00:00.000Z",
    },
  });
  const { writeMemoirStore } = sessionStoreModule();

  const result = writeMemoirStore({
    project: { id: "project-1", title: "回忆录" },
  });

  assert.equal(result.cache_write_failed_at, undefined);
  assert.equal(mock.storage[STORE_KEY].cache_write_failed_at, undefined);
  assert.equal(mock.storage[STORE_KEY].project.title, "回忆录");
});

test("voice session snapshots persist backend detail as transcript lines", () => {
  const mock = installWxStorageMock();
  const {
    readMemoirVoiceSessionSnapshot,
    transcriptLinesFromVoiceSessionDetail,
    writeMemoirVoiceSessionSnapshot,
  } = memoirCacheModule();
  const detail = {
    session: {
      id: "voice-1",
      project_id: "project-1",
      status: "stopped",
      guidance_mode: "guided_interview",
      current_guidance_module: "people",
      error_message: "",
      metadata_json: {},
      created_at: "2026-06-08T00:00:00.000Z",
      updated_at: "2026-06-08T00:03:00.000Z",
    },
    messages: [
      {
        id: "assistant-1",
        project_id: "project-1",
        voice_session_id: "voice-1",
        role: "assistant",
        content_text: "你想先聊谁？",
        content_status: "final",
        turn_index: 1,
        metadata_json: {},
        created_at: "2026-06-08T00:01:00.000Z",
        updated_at: "2026-06-08T00:01:00.000Z",
      },
      {
        id: "message-1",
        project_id: "project-1",
        voice_session_id: "voice-1",
        role: "user",
        content_text: "我想聊我的外婆。",
        content_status: "final",
        audio_file_id: "audio-1",
        transcript_version_id: "transcript-1",
        turn_index: 2,
        metadata_json: {},
        created_at: "2026-06-08T00:02:00.000Z",
        updated_at: "2026-06-08T00:02:00.000Z",
      },
    ],
    utterances: [
      {
        id: "utterance-1",
        voice_session_id: "voice-1",
        chat_message_id: "message-1",
        audio_file_id: "audio-1",
        transcript_raw: "我想聊我的外婆",
        transcript_edited: "我想聊我的外婆。",
        start_ms: 120,
        end_ms: 2600,
        provider_payload_json: {},
        created_at: "2026-06-08T00:02:00.000Z",
      },
    ],
  };

  assert.deepEqual(transcriptLinesFromVoiceSessionDetail(detail), [
    {
      id: "assistant-1",
      text: "你想先聊谁？",
      role: "assistant",
      message_id: "assistant-1",
      utterance_id: undefined,
      audio_file_id: undefined,
      transcript_version_id: undefined,
      start_ms: undefined,
      end_ms: undefined,
    },
    {
      id: "message-1",
      text: "我想聊我的外婆。",
      role: "user",
      message_id: "message-1",
      utterance_id: "utterance-1",
      audio_file_id: "audio-1",
      transcript_version_id: "transcript-1",
      start_ms: 120,
      end_ms: 2600,
    },
  ]);

  writeMemoirVoiceSessionSnapshot("voice-1", { detail });
  const snapshot = readMemoirVoiceSessionSnapshot("voice-1");

  assert.equal(snapshot.hasSession, true);
  assert.equal(snapshot.isFresh, true);
  assert.equal(snapshot.detail.session.guidance_mode, "guided_interview");
  assert.equal(snapshot.transcriptLines.length, 2);
  assert.equal(snapshot.transcriptLines[0].text, "你想先聊谁？");
  assert.equal(snapshot.transcriptLines[1].text, "我想聊我的外婆。");
  assert.equal(mock.storage[STORE_KEY].voice_session_cache.version, 3);
});

test("voice session snapshots keep longer local transcript when backend detail lags", () => {
  installWxStorageMock();
  const {
    readMemoirVoiceSessionSnapshot,
    writeMemoirVoiceSessionSnapshot,
  } = memoirCacheModule();
  writeMemoirVoiceSessionSnapshot("voice-1", {
    transcriptLines: [
      { id: "line-1", text: "第一句" },
      { id: "line-2", text: "第二句" },
    ],
  });
  writeMemoirVoiceSessionSnapshot("voice-1", {
    detail: {
      session: {
        id: "voice-1",
        project_id: "project-1",
        status: "listening",
        guidance_mode: "free_narration",
        error_message: "",
        metadata_json: {},
        created_at: "2026-06-08T00:00:00.000Z",
        updated_at: "2026-06-08T00:01:00.000Z",
      },
      messages: [
        {
          id: "message-1",
          project_id: "project-1",
          voice_session_id: "voice-1",
          role: "user",
          content_text: "第一句",
          content_status: "final",
          turn_index: 1,
          metadata_json: {},
          created_at: "2026-06-08T00:01:00.000Z",
          updated_at: "2026-06-08T00:01:00.000Z",
        },
      ],
      utterances: [],
    },
  });

  const snapshot = readMemoirVoiceSessionSnapshot("voice-1");

  assert.equal(snapshot.hasSession, true);
  assert.equal(snapshot.detail.session.id, "voice-1");
  assert.deepEqual(snapshot.transcriptLines.map((line) => line.text), ["第一句", "第二句"]);
});
