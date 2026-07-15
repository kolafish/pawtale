require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  sampleAudioResponse,
  sampleAutomationEvent,
  sampleBookOutput,
  sampleBookVoiceCommandExecuteResponse,
  sampleBookVoiceCommandPreview,
  sampleDraft,
  samplePlan,
  sampleSourceCoverage,
} = require("./book-fixtures");

function resetMemoirVoiceApiModules() {
  delete require.cache[require.resolve("../services/auth.ts")];
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/ai-model-settings.ts")];
  delete require.cache[require.resolve("../services/memoir-voice-api.ts")];
}

function installBookApiWxMock({ requests = [], downloads = [], aiModel = "doubao-seed-2-0-pro-260215" } = {}) {
  global.wx = {
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
      if (options.url.endsWith("/draft/tts") || options.url.endsWith("/outline/tts") || options.url.endsWith("/book-output/tts")) {
        data = sampleAudioResponse();
      } else if (options.url.endsWith("/book-voice-command/parse")) {
        data = sampleBookVoiceCommandPreview();
      } else if (options.url.endsWith("/book-voice-command/execute")) {
        data = sampleBookVoiceCommandExecuteResponse("rename_chapter", samplePlan("confirmed"));
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
      } else if (options.url.endsWith("/book-source-coverage")) {
        data = sampleSourceCoverage();
      }
      options.success({ statusCode: 200, data, cookies: [], header: {} });
    },
    downloadFile(options) {
      downloads.push(options);
      options.success({ statusCode: 200, tempFilePath: "/tmp/book.pdf", header: {}, errMsg: "downloadFile:ok" });
    },
  };
}

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
  resetMemoirVoiceApiModules();
  const { createMemoirVoiceNoAnswerPrompt } = require("../services/memoir-voice-api.ts");

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
        options.success({
          statusCode: 200,
          data: options.url.includes("/voice-played")
            ? { updated_count: 1 }
            : options.url.includes("/acknowledge")
            ? {
              id: "event-1",
              project_id: "project/1",
              event_type: "source_assigned",
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
                  event_type: "source_assigned",
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
            },
          cookies: [],
          header: {},
        });
        return;
      }
      throw new Error(`unexpected request: ${options.url}`);
    },
  };
  resetMemoirVoiceApiModules();
  const {
    acknowledgeMemoirAutomationEvent,
    listMemoirAutomationEvents,
    markMemoirAutomationEventsVoicePlayed,
  } = require("../services/memoir-voice-api.ts");

  const events = await listMemoirAutomationEvents("project/1", 5);
  const acknowledged = await acknowledgeMemoirAutomationEvent("project/1", "event/1");
  const voicePlayed = await markMemoirAutomationEventsVoicePlayed("project/1", {
    event_ids: ["event/1"],
    voice_session_id: "voice/1",
    message_id: "message/1",
  });

  const apiRequests = requests.filter((request) => request.url.includes("/api/mobile/memoir/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/automation-events?limit=5",
  );
  assert.equal(
    apiRequests[1].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/automation-events/event%2F1/acknowledge",
  );
  assert.equal(apiRequests[1].method, "POST");
  assert.equal(
    apiRequests[2].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/automation-events/voice-played",
  );
  assert.equal(apiRequests[2].method, "POST");
  assert.deepEqual(apiRequests[2].data, {
    event_ids: ["event/1"],
    voice_session_id: "voice/1",
    message_id: "message/1",
  });
  assert.equal(events.items[0].summary_text, "已整理 1 条讲述。");
  assert.equal(acknowledged.metadata_json.acknowledged_by_user, true);
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
  resetMemoirVoiceApiModules();
  const { transcribeMemoirVoiceSearchAudio } = require("../services/memoir-voice-api.ts");

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
  resetMemoirVoiceApiModules();
  const { parseMemoirVoiceSearchQuery } = require("../services/memoir-voice-api.ts");

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
  resetMemoirVoiceApiModules();
  const { convertMemoirInterviewQuestionToAnswer } = require("../services/memoir-voice-api.ts");

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
  resetMemoirVoiceApiModules();
  const {
    createMemoirTranscriptShareVideoJob,
    downloadMemoirShareVideoJob,
    getMemoirShareVideoJob,
    getMemoirTranscriptShareVideoContext,
  } = require("../services/memoir-voice-api.ts");

  const context = await getMemoirTranscriptShareVideoContext("utterance/1");
  const created = await createMemoirTranscriptShareVideoJob("utterance/1", 0, 13, {
    backgroundStyle: "storybook",
    backgroundMode: "smart_multi",
  });
  const detail = await getMemoirShareVideoJob("job/1");
  const path = await downloadMemoirShareVideoJob(shareVideoJob);

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
  assert.deepEqual(apiRequests[1].data, {
    start_char: 0,
    end_char: 13,
    background_style: "storybook",
    background_mode: "smart_multi",
  });
  assert.equal(
    apiRequests[2].url,
    "http://backend.local:8000/api/mobile/memoir/share-video/jobs/job%2F1",
  );
  assert.equal(apiRequests[2].method, "GET");
  assert.equal(context.segments[0].text, "爷爷退休后还在帮公司做事。");
  assert.equal(context.background_modes[2].value, "smart_multi");
  assert.equal(created.status, "succeeded");
  assert.equal(detail.output.download_url, "/api/mobile/memoir/share-video/jobs/job-1/download");
  assert.equal(path, "/tmp/share.mp4");
  assert.equal(downloads[0].url, "http://backend.local:8000/api/mobile/memoir/share-video/jobs/job-1/download");
  assert.equal(downloads[0].header.Cookie, "pawtale_session=abc");
  assert.equal(downloads[0].timeout, 60000);
  delete global.wx;
});

test("share video custom background upload and preview use backend session", async () => {
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
  resetMemoirVoiceApiModules();
  const {
    downloadMemoirShareVideoCustomBackgroundPreview,
    uploadMemoirShareVideoCustomBackground,
  } = require("../services/memoir-voice-api.ts");

  const uploaded = await uploadMemoirShareVideoCustomBackground("project/1", "/tmp/family.png");
  const previewPath = await downloadMemoirShareVideoCustomBackgroundPreview(uploaded);

  assert.equal(uploaded.id, "asset-1");
  assert.equal(previewPath, "/tmp/processed-background.jpg");
  assert.equal(uploads.length, 1);
  assert.equal(uploads[0].url, "http://backend.local:8000/api/mobile/memoir/share-video/background-assets");
  assert.equal(uploads[0].filePath, "/tmp/family.png");
  assert.equal(uploads[0].name, "file");
  assert.deepEqual(uploads[0].formData, {
    project_id: "project/1",
    processing_preset: "memoir_soft",
  });
  assert.equal(uploads[0].header.Cookie, "pawtale_session=abc");
  assert.equal(uploads[0].timeout, 60000);
  assert.equal(
    downloads[0].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/image-assets/asset-1/file",
  );
  assert.equal(downloads[0].header.Cookie, "pawtale_session=abc");
  assert.equal(downloads[0].timeout, 60000);
  assert.equal(requests[0].url, "http://backend.local:8000/api/auth/me");
  delete global.wx;
});

test("book service APIs encode backend routes and selected AI model", async () => {
  const requests = [];
  installBookApiWxMock({ requests });
  resetMemoirVoiceApiModules();
  const api = require("../services/memoir-voice-api.ts");
  const base = "http://backend.local:8000/api/mobile/memoir/projects/project%2F1";
  const narrativeBase = "http://backend.local:8000/api/memoir/projects/project%2F1";
  const selectedModel = "doubao-seed-2-0-pro-260215";
  const cases = [
    { call: () => api.getMemoirBookPlan("project/1"), method: "GET", url: `${base}/book-plan` },
    { call: () => api.generateMemoirBookPlan("project/1", { max_chapters: 6 }), method: "POST", url: `${base}/book-plan/generate`, data: { max_chapters: 6, ai_model: selectedModel } },
    { call: () => api.confirmMemoirBookPlan("project/1", { title: "目录", chapters: [] }), method: "PUT", url: `${base}/book-plan`, data: { title: "目录", chapters: [] } },
    { call: () => api.updateMemoirBookPlan("project/1", { title: "目录", chapters: [] }), method: "PATCH", url: `${base}/book-plan`, data: { title: "目录", chapters: [] } },
    { call: () => api.getMemoirBookSourceCoverage("project/1"), method: "GET", url: `${base}/book-source-coverage` },
    { call: () => api.assignMemoirBookSources("project/1", { assignments: [{ chapter_id: "chapter/1", utterance_ids: ["utterance/1"] }] }), method: "POST", url: `${base}/book-source-assignments`, data: { assignments: [{ chapter_id: "chapter/1", utterance_ids: ["utterance/1"] }], ai_model: selectedModel } },
    { call: () => api.moveMemoirBookSource("project/1", { source_chapter_id: "chapter/1", target_chapter_id: "chapter/2", utterance_id: "utterance/1" }), method: "POST", url: `${base}/book-source-moves`, data: { source_chapter_id: "chapter/1", target_chapter_id: "chapter/2", utterance_id: "utterance/1", ai_model: selectedModel } },
    { call: () => api.updateMemoirBookChapterTitle("project/1", "chapter/1", { title: "新章名" }), method: "PATCH", url: `${base}/book-plan/chapters/chapter%2F1/title`, data: { title: "新章名" } },
    { call: () => api.generateMemoirBookChapterTitle("project/1", "chapter/1", {}), method: "POST", url: `${base}/book-plan/chapters/chapter%2F1/title/generate`, data: { ai_model: selectedModel } },
    { call: () => api.getMemoirBookChapterDraft("project/1", "chapter/1"), method: "GET", url: `${base}/book-plan/chapters/chapter%2F1/draft` },
    { call: () => api.confirmMemoirBookChapterDraft("project/1", "chapter/1"), method: "POST", url: `${base}/book-plan/chapters/chapter%2F1/draft/confirm` },
    { call: () => api.assembleMemoirChapterDraftBlocks("project/1", "chapter/1"), method: "POST", url: `${narrativeBase}/chapters/chapter%2F1/draft-blocks/assemble`, data: {} },
    { call: () => api.assembleMemoirBookDraftBlocks("project/1"), method: "POST", url: `${narrativeBase}/book-output/assemble-from-draft-blocks`, data: {} },
    { call: () => api.createMemoirBookChapterDraftTTS("project/1", "chapter/1", {}), method: "POST", url: `${base}/book-plan/chapters/chapter%2F1/draft/tts`, timeout: 180000 },
    { call: () => api.createMemoirBookPlanOutlineTTS("project/1", {}), method: "POST", url: `${base}/book-plan/outline/tts`, timeout: 180000 },
    { call: () => api.applyMemoirBookPlanStructureCommand("project/1", { type: "merge_chapters", chapter_id: "chapter/2", target_chapter_id: "chapter/1" }), method: "POST", url: `${base}/book-plan/structure-commands`, data: { type: "merge_chapters", chapter_id: "chapter/2", target_chapter_id: "chapter/1" } },
    { call: () => api.executeMemoirBookVoiceCommand("project/1", { intent: "rename_chapter", operation: { type: "rename_chapter", chapter_id: "chapter/1", new_title: "童年往事" } }), method: "POST", url: `${base}/book-voice-command/execute`, data: { intent: "rename_chapter", operation: { type: "rename_chapter", chapter_id: "chapter/1", new_title: "童年往事" } } },
    { call: () => api.getMemoirBookOutput("project/1"), method: "GET", url: `${base}/book-output` },
    { call: () => api.updateMemoirBookOutput("project/1", { title: "外婆慢慢说", cover_style: "warm_gray" }), method: "PATCH", url: `${base}/book-output`, data: { title: "外婆慢慢说", cover_style: "warm_gray" } },
    { call: () => api.suggestMemoirBookOutputTitles("project/1", { count: 4 }), method: "POST", url: `${base}/book-output/title-suggestions`, data: { count: 4, ai_model: selectedModel } },
    { call: () => api.enqueueMemoirBookOutputEditorialCopyGeneration("project/1", { targets: ["preface"], chapter_ids: ["chapter/1"], feedback: "更自然" }), method: "POST", url: `${base}/book-output/editorial-copy/regenerate/jobs`, data: { targets: ["preface"], chapter_ids: ["chapter/1"], feedback: "更自然", ai_model: selectedModel } },
  ];

  assert.equal(
    api.bookVoiceAssistantTurnStreamUrl("project/1"),
    "ws://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-voice-assistant/turn-stream",
  );
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
  resetMemoirVoiceApiModules();
  const { downloadMemoirBookOutputExport } = require("../services/memoir-voice-api.ts");

  const path = await downloadMemoirBookOutputExport("project/1", "pdf", "version/1");
  await downloadMemoirBookOutputExport("project/1", "pdf", "version/1", "standard", false);

  const baseExportUrl = "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/book-output/export";
  assert.equal(path, "/tmp/book.pdf");
  assert.equal(
    downloads[0].url,
    `${baseExportUrl}?format=pdf&layout=standard&show_source_appendix=true&version_id=version%2F1`,
  );
  assert.equal(downloads[0].header.Cookie, "pawtale_session=abc");
  assert.equal(downloads[0].timeout, 120000);
  assert.equal(
    downloads[1].url,
    `${baseExportUrl}?format=pdf&layout=standard&show_source_appendix=false&version_id=version%2F1`,
  );
  delete global.wx;
});
