require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  sampleAudioPlan,
  sampleAutomationEvent,
  sampleBookOutput,
  sampleDraft,
  samplePlan,
} = require("./book-fixtures");

function resetMemoirApiModules() {
  delete require.cache[require.resolve("../services/auth.ts")];
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/ai-model-settings.ts")];
  [
    "memoir-book-api",
    "memoir-book-completion-api",
    "memoir-api-client",
    "memoir-transcript-api",
    "memoir-voice-session-api",
  ].forEach((name) => {
    delete require.cache[require.resolve(`../services/${name}.ts`)];
  });
}

function installBookApiWxMock({ requests = [], downloads = [], aiModel = "doubao-seed-2-0-pro-260215" } = {}) {
  global.wx = {
    ...require("./file-system-mock").fileSystemWx(),
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          wsBaseUrl: "ws://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      if (key === "pawtale_ai_model_settings" && aiModel) {
        return { model: aiModel };
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      let data = samplePlan("confirmed");
      if (options.url.includes("/book-output/export/info?")) {
        const params = new URL(options.url).searchParams;
        data = { version_id: params.get("version_id"), cache_key: require("node:crypto").createHash("sha256").update(options.url).digest("hex"), filename: "写给他的话_阅读版_v28.pdf", ready: false };
        options.success({ statusCode: 200, data, header: {} });
        return;
      }
      if (options.url.endsWith("/tts/plan")) {
        data = sampleAudioPlan();
      } else if (options.url.endsWith("/draft") || options.url.endsWith("/draft/confirm")) {
        data = sampleDraft();
      } else if (options.url.endsWith("/book-output/title-suggestions")) {
        data = { suggestions: ["外婆慢慢说"] };
      } else if (options.url.endsWith("/book-output/editorial-copy/regenerate/jobs")) {
        data = sampleAutomationEvent({ event_type: "book_editorial_copy_generated", chapter_id: null });
      } else if (
        options.url.endsWith("/book-output")
        || options.url.endsWith("/book-output/editorial-copy/regenerate")
        || options.url.includes("/book-output/chapters/")
      ) {
        data = sampleBookOutput();
      }
      options.success({ statusCode: 200, data, cookies: [], header: {} });
    },
    downloadFile(options) {
      downloads.push(options);
      options.success({ statusCode: 200, tempFilePath: `${wx.env.USER_DATA_PATH}/download.pdf`, header: {}, errMsg: "downloadFile:ok" });
    },
  };
}

test("transcript organization filters preserve false in query parameters", async () => {
  const requests = [];
  installBookApiWxMock({ requests });
  resetMemoirApiModules();
  const { listMemoirVoiceTranscripts } = require("../services/memoir-transcript-api.ts");

  for (const organized of [false, true, undefined]) {
    await listMemoirVoiceTranscripts({ projectId: "project-1", organized });
  }

  const scopes = requests.filter((item) => item.url.includes("/voice-transcripts?"))
    .map((item) => new URL(item.url).searchParams.get("organized"));
  assert.deepEqual(scopes, ["false", "true", null]);
  delete global.wx;
});

test("voice no-answer prompt API posts attempt without selected AI model", async () => {
  const requests = [];
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      if (key === "pawtale_ai_model_settings") {
        return { model: "deepseek-v4-flash" };
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      options.success({
        statusCode: 200,
        data: {
          message: { id: "message-1" },
          follow_up_text: "我这边没有听到声音，您可以接着刚才的问题慢慢说。",
        },
        cookies: [],
        header: {},
      });
    },
  };
  resetMemoirApiModules();
  const { createMemoirVoiceNoAnswerPrompt } = require("../services/memoir-voice-session-api.ts");

  await createMemoirVoiceNoAnswerPrompt("voice/1", { attempt: 1 });

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/voice-sessions/voice%2F1/no-answer-prompt",
  );
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, { attempt: 1 });
  delete global.wx;
});

test("project automation event APIs encode routes and payloads", async () => {
  const requests = [];
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      if (options.url.includes("/automation-events")) {
        const data = options.url.includes("/acknowledge-dismissible")
          ? { updated_count: 9 }
          : options.url.includes("/voice-played")
            ? { updated_count: 1 }
            : options.url.includes("/acknowledge")
              ? {
                id: "event-1",
                project_id: "project/1",
                event_type: "chapter_draft_generated",
                status: "completed",
                utterance_ids: ["utterance-1"],
                summary_text: "已整理 1 条讲述。",
                metadata_json: { acknowledged_by_user: true },
                created_at: "2026-06-23T12:00:00",
                updated_at: "2026-06-23T12:00:00",
              }
              : {
                items: [
                  {
                    id: "event-1",
                    project_id: "project/1",
                    event_type: "chapter_draft_generated",
                    status: "completed",
                    utterance_ids: ["utterance-1"],
                    summary_text: "已整理 1 条讲述。",
                    metadata_json: {},
                    created_at: "2026-06-23T12:00:00",
                    updated_at: "2026-06-23T12:00:00",
                  },
                ],
                total: 1,
                limit: 5,
              };
        options.success({
          statusCode: 200,
          data,
          cookies: [],
          header: {},
        });
        return;
      }
      throw new Error(`unexpected request: ${options.url}`);
    },
  };
  resetMemoirApiModules();
  const {
    acknowledgeMemoirAutomationEvent,
    acknowledgeDismissibleMemoirAutomationEvents,
    listMemoirAutomationEvents,
    markMemoirAutomationEventsVoicePlayed,
  } = require("../services/memoir-voice-session-api.ts");

  const events = await listMemoirAutomationEvents("project/1", 5);
  const acknowledged = await acknowledgeMemoirAutomationEvent("project/1", "event/1");
  const dismissed = await acknowledgeDismissibleMemoirAutomationEvents("project/1");
  const voicePlayed = await markMemoirAutomationEventsVoicePlayed("project/1", {
    event_ids: ["event/1"],
    voice_session_id: "voice/1",
    message_id: "message/1",
  });

  await listMemoirAutomationEvents("project/1", 8, { completedOnly: true });
  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/automation-events?limit=5",
  );
  assert.equal(apiRequests.at(-1).url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/automation-events?limit=8&completed_only=true");
  assert.equal(
    apiRequests[1].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/automation-events/event%2F1/acknowledge",
  );
  assert.equal(apiRequests[1].method, "POST");
  assert.equal(
    apiRequests[2].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/automation-events/acknowledge-dismissible",
  );
  assert.equal(apiRequests[2].method, "POST");
  assert.equal(
    apiRequests[3].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/automation-events/voice-played",
  );
  assert.equal(apiRequests[3].method, "POST");
  assert.deepEqual(apiRequests[3].data, {
    event_ids: ["event/1"],
    voice_session_id: "voice/1",
    message_id: "message/1",
  });
  assert.equal(events.items[0].summary_text, "已整理 1 条讲述。");
  assert.equal(acknowledged.metadata_json.acknowledged_by_user, true);
  assert.equal(dismissed.updated_count, 9);
  assert.equal(voicePlayed.updated_count, 1);
  delete global.wx;
});

test("voice search transcription API posts recorded audio", async () => {
  const requests = [];
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      options.success({
        statusCode: 200,
        data: { transcript: "外婆家的院子" },
        cookies: [],
        header: {},
      });
    },
  };
  resetMemoirApiModules();
  const { transcribeMemoirVoiceSearchAudio } = require("../services/memoir-transcript-api.ts");

  const response = await transcribeMemoirVoiceSearchAudio("cGNt", "audio/pcm");

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/mobile/memoir/search-query/transcribe");
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, {
    audio_base64: "cGNt",
    mime_type: "audio/pcm",
  });
  assert.equal(apiRequests[0].header.Cookie, "pawtale_session=abc");
  assert.deepEqual(response, { transcript: "外婆家的院子" });
  delete global.wx;
});

test("voice search query parse API posts text and selected AI model", async () => {
  const requests = [];
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      options.success({
        statusCode: 200,
        data: {
          original_text: "帮我找外婆家院子",
          query: "外婆 院子",
          keywords: ["外婆", "院子"],
          filters: { included: "not_included", guidance_mode: null, module: "place" },
          display_text: "外婆 院子 · 未收录 · 地点",
          used_ai: true,
          provider: "fake-ai",
          model: "deepseek",
        },
        cookies: [],
        header: {},
      });
    },
  };
  resetMemoirApiModules();
  const { parseMemoirVoiceSearchQuery } = require("../services/memoir-transcript-api.ts");

  const response = await parseMemoirVoiceSearchQuery("帮我找外婆家院子", "deepseek");

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(apiRequests[0].url, "http://backend.local:8000/api/mobile/memoir/search-query/parse");
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, {
    text: "帮我找外婆家院子",
    ai_model: "deepseek",
  });
  assert.equal(apiRequests[0].header.Cookie, "pawtale_session=abc");
  assert.equal(response.query, "外婆 院子");
  assert.equal(response.filters.module, "place");
  delete global.wx;
});

test("question conversion API posts miniprogram mutation source", async () => {
  const requests = [];
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      options.success({ statusCode: 200, data: {}, cookies: [], header: {} });
    },
  };
  resetMemoirApiModules();
  const { convertMemoirInterviewQuestionToAnswer } = require("../services/memoir-transcript-api.ts");

  await convertMemoirInterviewQuestionToAnswer("project/1", "message/1");

  const apiRequests = requests.filter((request) => request.url.includes("/api/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/memoir/projects/project%2F1/interview-editor/questions/message%2F1/convert-to-answer",
  );
  assert.equal(apiRequests[0].method, "POST");
  assert.deepEqual(apiRequests[0].data, {
    parent_question_message_id: null,
    reason: "miniprogram_convert_question_to_unassigned_answer",
    mutation_source: "miniprogram_transcript_detail",
  });
  assert.equal(apiRequests[0].header.Cookie, "pawtale_session=abc");
  delete global.wx;
});

test("share video APIs encode routes and download preview with session cookie", async () => {
  const requests = [];
  const downloads = [];
  const shareVideoJob = {
    id: "job-1",
    project_id: "project/1",
    utterance_id: "utterance/1",
    audio_file_id: "audio-1",
    status: "succeeded",
    selected_text: "爷爷退休后还在帮公司做事。",
    start_char: 0,
    end_char: 13,
    start_ms: 0,
    end_ms: 4200,
    duration_ms: 4200,
    output: {
      mime_type: "video/mp4",
      size_bytes: 4096,
      download_url: "/api/mobile/memoir/share-video/jobs/job-1/download",
    },
    error_message: "",
    created_at: "2026-07-01T08:00:00.000Z",
    updated_at: "2026-07-01T08:00:02.000Z",
    completed_at: "2026-07-01T08:00:02.000Z",
  };
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      if (key === "pawtale_ai_model_settings") {
        return { model: "deepseek-v4-pro" };
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      if (options.url.endsWith("/share-video/context")) {
        options.success({
          statusCode: 200,
          data: {
            utterance_id: "utterance/1",
            audio_file_id: "audio-1",
            transcript_source: "edited",
            transcript_text: "爷爷退休后还在帮公司做事。",
            text_hash: "hash-1",
            duration_ms: 4200,
            alignment_strategy: "provider_words",
            alignment_confidence: 0.9,
            min_select_ms: 3000,
            max_select_ms: 90000,
            default_background_mode: "auto",
            background_modes: [
              {
                value: "auto",
                label: "自动",
                description: "20 秒以上自动使用多图",
              },
              {
                value: "single",
                label: "单张背景",
                description: "整段视频使用一张背景图",
              },
              {
                value: "smart_multi",
                label: "智能多图",
                description: "按内容拆分多张背景",
              },
            ],
            segments: [
              {
                index: 0,
                text: "爷爷退休后还在帮公司做事。",
                start_char: 0,
                end_char: 13,
                start_ms: 0,
                end_ms: 4200,
                confidence: 0.9,
                selectable: true,
              },
            ],
          },
          cookies: [],
          header: {},
        });
        return;
      }
      options.success({ statusCode: 200, data: shareVideoJob, cookies: [], header: {} });
    },
    downloadFile(options) {
      downloads.push(options);
      options.success({ statusCode: 200, tempFilePath: "/tmp/share.mp4", header: {}, errMsg: "downloadFile:ok" });
    },
  };
  resetMemoirApiModules();
  const {
    createMemoirShareVideoCandidateJob,
    createMemoirTranscriptShareVideoJob,
    downloadMemoirShareVideoJob,
    downloadMemoirShareVideoThumbnail,
    deleteMemoirShareVideoJob,
    getMemoirShareVideoJob,
    getMemoirTranscriptShareVideoContext,
    memoirSharedVideoUrl,
  } = require("../services/memoir-transcript-api.ts");

  const context = await getMemoirTranscriptShareVideoContext("utterance/1");
  const created = await createMemoirTranscriptShareVideoJob("utterance/1", 0, 13, {
    videoStyle: "illustrated",
    backgroundStyle: "gouache",
    backgroundMode: "smart_multi",
  });
  const detail = await getMemoirShareVideoJob("job/1");
  await createMemoirShareVideoCandidateJob("project/1", "candidate/1", {
    videoStyle: "uploaded",
    photoAssetIds: ["photo-2", "photo-1"],
  });
  const path = await downloadMemoirShareVideoJob(shareVideoJob);
  await downloadMemoirShareVideoThumbnail("job/1");
  await deleteMemoirShareVideoJob("job/1");

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/voice-transcripts/utterance%2F1/share-video/context",
  );
  assert.equal(apiRequests[0].method, "GET");
  assert.equal(
    apiRequests[1].url,
    "http://backend.local:8000/api/mobile/memoir/voice-transcripts/utterance%2F1/share-video/jobs",
  );
  assert.equal(apiRequests[1].method, "POST");
  assert.equal(apiRequests[1].timeout, 60000);
  assert.deepEqual(apiRequests[1].data, {
    start_char: 0,
    end_char: 13,
    ai_model: "deepseek-v4-pro",
    video_style: "illustrated",
    background_style: "gouache",
    background_mode: "smart_multi",
  });
  assert.equal(
    apiRequests[2].url,
    "http://backend.local:8000/api/mobile/memoir/share-video/jobs/job%2F1",
  );
  assert.equal(apiRequests[2].method, "GET");
  assert.equal(apiRequests[3].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/share-video-candidates/candidate%2F1/create-video");
  assert.deepEqual(apiRequests[3].data, {
    ai_model: "deepseek-v4-pro", video_style: "uploaded", photo_asset_ids: ["photo-2", "photo-1"],
  });
  assert.equal(apiRequests[4].url,
    "http://backend.local:8000/api/mobile/memoir/share-video/jobs/job%2F1");
  assert.equal(apiRequests[4].method, "DELETE");
  assert.deepEqual(apiRequests[4].data, { confirm: true });
  assert.equal(context.segments[0].text, "爷爷退休后还在帮公司做事。");
  assert.equal(context.background_modes[2].value, "smart_multi");
  assert.equal(created.status, "succeeded");
  assert.equal(detail.output.download_url, "/api/mobile/memoir/share-video/jobs/job-1/download");
  assert.equal(path, "/tmp/share.mp4");
  assert.equal(downloads[0].url, "http://backend.local:8000/api/mobile/memoir/share-video/jobs/job-1/download");
  assert.equal(downloads[0].header.Cookie, "pawtale_session=abc");
  assert.equal(downloads[0].timeout, 60000);
  assert.equal(downloads[1].url, "http://backend.local:8000/api/mobile/memoir/share-video/jobs/job%2F1/download?variant=thumbnail");
  assert.equal(downloads[1].header.Cookie, "pawtale_session=abc");
  const publicVideoUrl = "http://backend.local:8000/api/mobile/memoir/share-video/shares/share%2Fcode/video";
  assert.equal(memoirSharedVideoUrl("share/code"), publicVideoUrl);
  assert.equal(memoirSharedVideoUrl("share/code", "thumbnail"), `${publicVideoUrl}?variant=thumbnail`);
  assert.equal(memoirSharedVideoUrl("share/code", "card"), `${publicVideoUrl}?variant=card&v=2`);
  delete global.wx;
});

test("image library upload and thumbnail download use backend session", async () => {
  const requests = [];
  const uploads = [];
  const downloads = [];
  const asset = {
    id: "asset-1",
    project_id: "project/1",
    status: "ready",
    preview_url: "/api/mobile/memoir/projects/project%2F1/image-assets/asset-1/file",
    mime_type: "image/jpeg",
    size_bytes: 4096,
    width: 1280,
    height: 720,
    processing_preset: "memoir_soft",
    original_mime_type: "image/png",
    original_size_bytes: 2048,
  };
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      options.success({ statusCode: 200, data: {}, cookies: [], header: {} });
    },
    uploadFile(options) {
      uploads.push(options);
      options.success({
        errMsg: "uploadFile:ok",
        statusCode: 201,
        data: JSON.stringify(asset),
      });
    },
    downloadFile(options) {
      downloads.push(options);
      options.success({ statusCode: 200, tempFilePath: "/tmp/processed-background.jpg", header: {}, errMsg: "downloadFile:ok" });
    },
  };
  resetMemoirApiModules();
  const { downloadMemoirImageAssetFile, uploadMemoirImageAsset } = require("../services/memoir-book-api.ts");
  assert.equal((await uploadMemoirImageAsset("project/1", "/tmp/cover.png")).id, "asset-1");
  assert.equal(await downloadMemoirImageAssetFile("project/1", "asset-1"), "/tmp/processed-background.jpg");
  assert.equal(uploads[0].url, "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/image-assets");
  assert.equal(uploads[0].filePath, "/tmp/cover.png");
  assert.equal(uploads[0].name, "file");
  assert.equal(uploads[0].header.Cookie, "pawtale_session=abc");
  assert.equal(
    downloads[0].url.split("&cache_key=")[0],
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/image-assets/asset-1/file",
  );
  assert.equal(downloads[0].header.Cookie, "pawtale_session=abc");
  assert.equal(downloads[0].timeout, 60000);
  assert.equal(requests.length, 0);
  delete global.wx;
});

test("book service APIs encode backend routes and selected AI model", async () => {
  const requests = [];
  installBookApiWxMock({ requests });
  resetMemoirApiModules();
  const api = {
    ...require("../services/memoir-book-api.ts"),
    ...require("../services/memoir-book-completion-api.ts"),
  };
  const apiClient = require("../services/memoir-api-client.ts");
  const base = "http://backend.local:8000/api/mobile/memoir/projects/project%2F1";
  const narrativeBase = "http://backend.local:8000/api/memoir/projects/project%2F1";
  const selectedModel = "doubao-seed-2-0-pro-260215";
  assert.equal(
    apiClient.memoirProjectDictationUrl("project/1"),
    "ws://backend.local:8000/api/memoir/projects/project%2F1/dictation",
  );
  const cases = [
    { call: () => api.getMemoirBookPlan("project/1"), method: "GET", url: `${base}/book-plan` },
    { call: () => api.updateMemoirBookChapterTitle("project/1", "chapter/1", { title: "新章名" }), method: "PATCH", url: `${base}/book-plan/chapters/chapter%2F1/title`, data: { title: "新章名" } },
    { call: () => api.generateMemoirBookChapterTitle("project/1", "chapter/1", {}), method: "POST", url: `${base}/book-plan/chapters/chapter%2F1/title/generate`, data: { ai_model: selectedModel } },
    { call: () => api.getMemoirBookChapterDraft("project/1", "chapter/1"), method: "GET", url: `${base}/book-plan/chapters/chapter%2F1/draft` },
    { call: () => api.reviseMemoirBookChapterDraft("project/1", "chapter/1", { expected_version_id: "version/1", sections: [{ section_id: "section/1", content: "修改后的小节正文" }] }), method: "POST", url: `${base}/book-plan/chapters/chapter%2F1/draft/revise`, data: { expected_version_id: "version/1", sections: [{ section_id: "section/1", content: "修改后的小节正文" }] } },
    { call: () => api.confirmMemoirBookChapterDraft("project/1", "chapter/1"), method: "POST", url: `${base}/book-plan/chapters/chapter%2F1/draft/confirm` },
    { call: () => api.assembleMemoirBookOutput("project/1"), method: "POST", url: `${narrativeBase}/book-output/assemble`, data: {} },
    { call: () => api.prepareMemoirAudio("project/1", { kind: "chapter", chapterId: "chapter/1" }, {}), method: "POST", url: `${base}/book-plan/chapters/chapter%2F1/draft/tts/plan`, timeout: 30000 },
    { call: () => api.prepareMemoirAudio("project/1", { kind: "section", chapterId: "chapter/1", sectionId: "section/1" }, {}), method: "POST", url: `${base}/book-plan/chapters/chapter%2F1/draft/sections/section%2F1/tts/plan`, timeout: 30000 },
    { call: () => api.prepareMemoirAudio("project/1", { kind: "outline" }, {}), method: "POST", url: `${base}/book-plan/outline/tts/plan`, timeout: 30000 },
    { call: () => api.prepareMemoirAudio("project/1", { kind: "book", versionId: "version/1" }, { start_index: 2 }), method: "POST", url: `${base}/book-output/tts/plan`, timeout: 30000, data: { version_id: "version/1", start_index: 2 } },
    { call: () => api.applyMemoirBookPlanStructureCommand("project/1", { type: "merge_chapters", chapter_id: "chapter/2", target_chapter_id: "chapter/1" }), method: "POST", url: `${base}/book-plan/structure-commands`, data: { type: "merge_chapters", chapter_id: "chapter/2", target_chapter_id: "chapter/1" } },
    { call: () => api.getMemoirBookCompletionOverview("project/1"), method: "GET", url: `${narrativeBase}/book-completion/overview` },
    { call: () => api.enqueueMemoirBookReviewJob("project/1", true), method: "POST", url: `${narrativeBase}/book-review/jobs`, data: { force_regenerate: true, ai_model: selectedModel } },
    { call: () => api.cancelMemoirBookReviewJob("project/1", "job/1"), method: "DELETE", url: `${narrativeBase}/book-review/jobs/job%2F1` },
    { call: () => api.enqueueMemoirBookRevisionJob("project/1", true, ["action/1"]), method: "POST", url: `${narrativeBase}/book-review/revision/jobs`, data: { include_answers: true, action_ids: ["action/1"], ai_model: selectedModel } },
    { call: () => api.cancelMemoirBookRevisionJob("project/1", "job/1"), method: "DELETE", url: `${narrativeBase}/book-review/revision/jobs/job%2F1` },
    { call: () => api.getMemoirBookRevisionProposal("project/1", "proposal/1"), method: "GET", url: `${narrativeBase}/book-review/revision-proposals/proposal%2F1?preview=none` },
    { call: () => api.getMemoirBookRevisionProposal("project/1", "proposal/1", "after"), method: "GET", url: `${narrativeBase}/book-review/revision-proposals/proposal%2F1?preview=after` },
    { call: () => api.enqueueMemoirBookRevisionFeedback("project/1", "proposal/1", "请统一称谓"), method: "POST", url: `${narrativeBase}/book-review/revision-proposals/proposal%2F1/feedback/jobs`, data: { feedback: "请统一称谓", ai_model: selectedModel } },
    { call: () => api.getMemoirBookRevisionFeedbackJob("project/1", "proposal/1"), method: "GET", url: `${narrativeBase}/book-review/revision-proposals/proposal%2F1/feedback/jobs/current` },
    { call: () => api.enqueueMemoirBookRevisionDraft("project/1", "proposal/1"), method: "POST", url: `${narrativeBase}/book-review/revision-proposals/proposal%2F1/draft/jobs`, data: { ai_model: selectedModel } },
    { call: () => api.getMemoirBookRevisionDraftJob("project/1", "proposal/1"), method: "GET", url: `${narrativeBase}/book-review/revision-proposals/proposal%2F1/draft/jobs/current` },
    { call: () => api.applyMemoirBookRevisionProposal("project/1", "proposal/1"), method: "POST", url: `${narrativeBase}/book-review/revision-proposals/proposal%2F1/apply?preview=none` },
    { call: () => api.dismissMemoirBookRevisionProposal("project/1", "proposal/1"), method: "POST", url: `${narrativeBase}/book-review/revision-proposals/proposal%2F1/dismiss?preview=none` },
    { call: () => api.enqueueMemoirBookCompletionJob("project/1"), method: "POST", url: `${narrativeBase}/book-completion/jobs`, data: { retry_job_id: null, ai_model: selectedModel } },
    { call: () => api.enqueueMemoirBookCompletionJob("project/1", "job/0"), method: "POST", url: `${narrativeBase}/book-completion/jobs`, data: { retry_job_id: "job/0", ai_model: selectedModel } },
    { call: () => api.cancelMemoirBookCompletionJob("project/1", "job/1"), method: "DELETE", url: `${narrativeBase}/book-completion/jobs/job%2F1` },
    { call: () => api.getMemoirBookOutput("project/1"), method: "GET", url: `${base}/book-output` },
    { call: () => api.getMemoirBookOutputVersion("project/1", "version/1"), method: "GET", url: `${base}/book-output/versions/version%2F1` },
    { call: () => api.updateMemoirBookOutput("project/1", { title: "外婆慢慢说", cover_style: "warm_gray" }), method: "PATCH", url: `${base}/book-output`, data: { title: "外婆慢慢说", cover_style: "warm_gray" } },
    { call: () => api.suggestMemoirBookOutputTitles("project/1", { count: 4 }), method: "POST", url: `${base}/book-output/title-suggestions`, data: { count: 4, ai_model: selectedModel } },
    { call: () => api.enqueueMemoirBookOutputEditorialCopyGeneration("project/1", { targets: ["preface"], chapter_ids: ["chapter/1"], feedback: "更自然" }), method: "POST", url: `${base}/book-output/editorial-copy/regenerate/jobs`, data: { targets: ["preface"], chapter_ids: ["chapter/1"], feedback: "更自然", ai_model: selectedModel } },
    { call: () => api.getMemoirBookEditorialCopyJobs("project/1"), method: "GET", url: `${base}/book-output/editorial-copy/regenerate/jobs?include_subtitle=true` },
  ];

  for (const apiCase of cases) {
    await apiCase.call();
  }

  const apiRequests = requests.filter((request) => (
    request.url.includes("/api/mobile/memoir/") || request.url.includes("/api/memoir/")
  ));
  assert.equal(apiRequests.length, cases.length);
  cases.forEach((apiCase, index) => {
    const request = apiRequests[index];
    assert.equal(request.url, apiCase.url);
    assert.equal(request.method, apiCase.method);
    if (apiCase.data) {
      assert.deepEqual(request.data, apiCase.data);
    }
    if (apiCase.timeout) {
      assert.equal(request.timeout, apiCase.timeout);
    }
    if (apiCase.cookie) {
      assert.equal(request.header.Cookie, "pawtale_session=abc");
    }
  });
  delete global.wx;
});

test("book output export download encodes route and session cookie", async () => {
  const downloads = [];
  installBookApiWxMock({ downloads });
  resetMemoirApiModules();
  const { downloadMemoirBookOutputExport } = require("../services/memoir-book-api.ts");

  const path = await downloadMemoirBookOutputExport("project/1", "pdf", "version/1");
  await downloadMemoirBookOutputExport("project/1", "pdf", "version/1", "standard", false);
  await downloadMemoirBookOutputExport("project/1", "pdf", "version/1", "standard", false, {
    custom_layout: { font_family: "sans", font_scale: "large", line_spacing: "relaxed", page_margin: "wide" },
  });

  const baseExportUrl = "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output/export";
  assert.ok(path.endsWith("/写给他的话_阅读版_v28.pdf"));
  assert.equal(
    downloads[0].url.split("&cache_key=")[0],
    `${baseExportUrl}?format=pdf&layout=standard&show_source_appendix=true&version_id=version%2F1`,
  );
  assert.equal(downloads[0].header.Cookie, "pawtale_session=abc");
  assert.equal(downloads[0].timeout, 300000);
  assert.equal(
    downloads[1].url.split("&cache_key=")[0],
    `${baseExportUrl}?format=pdf&layout=standard&show_source_appendix=false&version_id=version%2F1`,
  );
  assert.equal(
    downloads[2].url.split("&cache_key=")[0],
    `${baseExportUrl}?format=pdf&layout=standard&show_source_appendix=false&preview_font_family=sans&preview_font_scale=large&preview_line_spacing=relaxed&preview_page_margin=wide&version_id=version%2F1`,
  );
  delete global.wx;
});


test("story share requests retain their section selector for status, preparation, preview and stop", async () => {
  const requests = [];
  installBookApiWxMock({ requests });
  resetMemoirApiModules();
  const api = require("../services/memoir-book-api.ts");
  await api.getMemoirBookShareStatus("p/1", "c/1", "s/1");
  await api.updateMemoirBookShare("p/1", "c/1", "s/1");
  await api.previewMemoirBookShare("p/1", "c/1", "s/1");
  await api.revokeMemoirBookShare("p/1", "c/1", "s/1");
  const selection = { token: "s1.token", version_id: "v1", excerpt_start: 0, excerpt_length: 7 };
  await api.saveMemoirShareCardSelection("p/1", selection, "c/1", "s/1");
  const shares = requests.filter(item => item.url.includes("/book-output/share"));
  assert.equal(shares.length, 5);
  assert.equal(shares[4].method, "PATCH");
  assert.deepEqual(shares[4].data, selection);
  for (const item of shares) {
    const url = new URL(item.url);
    assert.equal(url.searchParams.get("section_id"), "s/1");
    assert.equal(url.searchParams.get("chapter_id"), "c/1");
    assert.match(url.pathname, /projects\/p%2F1\/book-output\/share/);
  }
  assert.equal(shares[2].url.includes("/share/preview?"), true);
});
