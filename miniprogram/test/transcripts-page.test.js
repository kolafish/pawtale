require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadTranscriptsPage({ authExports = {}, voiceApiExports = {} } = {}) {
  const authPath = require.resolve("../services/auth.ts");
  const voiceApiPath = require.resolve("../services/memoir-voice-api.ts");
  const transcriptsPath = require.resolve("../pages/transcripts/index.ts");
  delete require.cache[authPath];
  delete require.cache[voiceApiPath];
  delete require.cache[transcriptsPath];
  require.cache[authPath] = {
    id: authPath,
    filename: authPath,
    loaded: true,
    exports: {
      getMemoirIdentity: async () => {
        throw new Error("getMemoirIdentity was not mocked");
      },
      ...authExports,
    },
  };
  require.cache[voiceApiPath] = {
    id: voiceApiPath,
    filename: voiceApiPath,
    loaded: true,
    exports: {
      deleteMemoirVoiceTranscript: async () => {
        throw new Error("deleteMemoirVoiceTranscript was not mocked");
      },
      downloadMemoirVoiceAudio: async () => {
        throw new Error("downloadMemoirVoiceAudio was not mocked");
      },
      listMemoirVoiceTranscripts: async () => {
        throw new Error("listMemoirVoiceTranscripts was not mocked");
      },
      updateMemoirVoiceTranscript: async () => {
        throw new Error("updateMemoirVoiceTranscript was not mocked");
      },
      ...voiceApiExports,
    },
  };

  let page = null;
  const previousPage = global.Page;
  global.Page = (definition) => {
    page = definition;
  };
  const mod = require(transcriptsPath);
  if (previousPage === undefined) {
    delete global.Page;
  } else {
    global.Page = previousPage;
  }
  return { mod, page };
}

function pageContext(page, dataPatch = {}) {
  return {
    ...page,
    data: { ...page.data, ...dataPatch },
    setData(patch) {
      this.data = { ...this.data, ...patch };
    },
  };
}

test("transcript audio summary only exposes duration", () => {
  const { transcriptAudioText } = loadTranscriptsPage().mod;
  const summary = transcriptAudioText({
    audio_duration_seconds: 74,
    audio_file_id: "audio-1",
    audio_size_bytes: 491000,
    audio_mime_type: "audio/pcm",
  });

  assert.equal(summary, "时长 1 分 14 秒");
  assert.equal(summary.includes("音频已保存"), false);
  assert.equal(summary.includes("491"), false);
  assert.equal(summary.includes("PCM"), false);
});

test("transcript audio summary falls back to utterance timestamps", () => {
  const { transcriptAudioText } = loadTranscriptsPage().mod;

  assert.equal(
    transcriptAudioText({
      audio_duration_seconds: null,
      start_ms: 1000,
      end_ms: 10500,
    }),
    "时长 10 秒",
  );
});

test("transcript book inclusion text shows included chapter titles", () => {
  const { transcriptBookInclusionText } = loadTranscriptsPage().mod;

  assert.equal(
    transcriptBookInclusionText({
      included_chapters: [
        { chapter_id: "chapter-2", title: "离家求学", sort_order: 1 },
        { chapter_id: "chapter-1", title: "童年院子", sort_order: 0 },
      ],
    }),
    "已收录：童年院子、离家求学",
  );
  assert.equal(transcriptBookInclusionText({ included_chapters: [] }), "未收录到章节");
});

test("transcripts page stays browseable when login is required", async () => {
  const switches = [];
  global.wx = {
    switchTab(options) {
      switches.push(options.url);
    },
  };
  const { ApiError } = require("../services/request.ts");
  const { page } = loadTranscriptsPage({
    authExports: {
      getMemoirIdentity: async () => {
        throw new ApiError(401, "请先登录", {});
      },
    },
  });
  const context = pageContext(page);

  await page.loadTranscripts.call(context, true);

  assert.equal(context.data.needsLogin, true);
  assert.equal(context.data.projectTitle, "我的回忆录");
  assert.deepEqual(context.data.items, []);
  assert.deepEqual(switches, []);
  delete global.wx;
});
