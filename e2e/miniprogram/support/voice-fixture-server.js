const http = require("node:http");
const { randomUUID } = require("node:crypto");
const { WebSocketServer } = require("ws");

const FIXTURE_PROJECT_ID = "e2e-project";
const FIXTURE_SESSION_ID = "e2e-voice-session";
const FIXTURE_USER_MESSAGE_ID = "e2e-user-message";
const FIXTURE_UTTERANCE_ID = "e2e-utterance";
const FIXTURE_ASSISTANT_MESSAGE_ID = "e2e-assistant-reply";
const FIXTURE_USER_TEXT = "那年夏天，我第一次坐火车去了北京。";
const FIXTURE_ASSISTANT_TEXT = "我听见了，这段第一次坐火车的经历很珍贵。";

function timestamp() {
  return "2026-08-25T00:00:00Z";
}

function initialAssistantMessage(sessionId = FIXTURE_SESSION_ID) {
  return {
    id: "e2e-opening-message",
    project_id: FIXTURE_PROJECT_ID,
    voice_session_id: sessionId,
    role: "assistant",
    content_text: "准备好了，您可以开始讲。",
    content_status: "final",
    audio_file_id: null,
    transcript_version_id: null,
    parent_message_id: null,
    guidance_intent: "opening",
    turn_index: 0,
    metadata_json: {},
    created_at: timestamp(),
    updated_at: timestamp(),
  };
}

function createVoiceFixtureServer() {
  const sessionPrefix = `${FIXTURE_SESSION_ID}-${randomUUID()}`;
  const server = http.createServer();
  const webSockets = new WebSocketServer({ noServer: true });
  const clients = new Set();
  const state = {
    activeGatewayClientEvents: [],
    assistantClientEvents: [],
    gatewayClientEvents: [],
    gatewayConnections: [],
    httpRequests: [],
    createdSessions: 0,
    sessionId: sessionPrefix,
    sessionStatus: "created",
    sessionConfig: {},
    projectTitle: "E2E 回忆录",
    projectProfile: {
      narrator_name: "",
      narrator_gender: "unspecified",
      narrator_origin: "",
      narrator_age: null,
      default_guidance_mode: "free_narration",
      default_interview_type: "ai_interview",
      default_session_goal: "open_narration",
    },
    unexpectedRequests: [],
    messages: [initialAssistantMessage()],
    utterances: [],
  };

  function projectDetail() {
    return {
      id: FIXTURE_PROJECT_ID,
      title: state.projectTitle,
      type: "memoir",
      total_word_count: 0,
      metadata_json: { memoir_profile: { ...state.projectProfile } },
      groups: [],
      references: [],
      created_at: timestamp(),
      updated_at: timestamp(),
    };
  }

  function identity() {
    const project = projectDetail();
    return {
      id: "e2e-user",
      display_name: "测试作者",
      email: "fixture@example.invalid",
      active_project_id: project.id,
      active_project: project,
      projects: [project],
    };
  }

  function sessionDetail() {
    return {
      session: {
        id: state.sessionId,
        project_id: FIXTURE_PROJECT_ID,
        status: state.sessionStatus,
        guidance_mode: state.sessionConfig.guidance_mode || "free_narration",
        interview_type: state.sessionConfig.interview_type || "ai_interview",
        session_goal: state.sessionConfig.session_goal || "open_narration",
        current_completion_issue_id: null,
        anchor_framework_item_id: null,
        current_guidance_module: null,
        provider: "e2e-fixture",
        input_format: "pcm",
        started_at: timestamp(),
        stopped_at: state.sessionStatus === "stopped" ? timestamp() : null,
        error_message: "",
        metadata_json: {},
        created_at: timestamp(),
        updated_at: timestamp(),
      },
      messages: state.messages,
      utterances: state.utterances,
    };
  }

  function respond(response, statusCode, data) {
    response.writeHead(statusCode, { "content-type": "application/json; charset=utf-8" });
    response.end(`${JSON.stringify(data)}\n`);
  }

  async function requestBody(request) {
    const chunks = [];
    for await (const chunk of request) {
      chunks.push(chunk);
    }
    if (!chunks.length) {
      return {};
    }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  }

  server.on("request", async (request, response) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    const body = await requestBody(request);
    state.httpRequests.push({ method: request.method, path: url.pathname, body });

    if (request.method === "GET" && url.pathname === "/api/mobile/memoir/story-snapshot") {
      respond(response, 200, {
        server_revision: state.httpRequests.length,
        identity: identity(),
        latest_transcript: null, chapters: [], growth_events: [],
        book_issues: { review_count: 0, supplement_count: 0 },
        book_readable: false, book_char_count: 0, book_chapter_count: 0, updated_at: timestamp(),
        unassigned_image_count: 0, unassigned_video_count: 0,
        progress: state.utterances.length ? {
          project_id: FIXTURE_PROJECT_ID, status: "processing", stage: "organizing",
          title: "正在整理这次讲述", detail: "讲述已保存，正在归入故事", can_retry: false,
          material_count: state.utterances.length, pending_material_count: state.utterances.length,
          needs_user_material_count: 0, readable_chapter_count: 0, total_chapter_count: 0,
        } : null, automation_events: { items: [], total: 0, limit: 8 },
      });
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/mobile/memoir/me") {
      respond(response, 200, identity());
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/mobile/memoir/projects") {
      const currentIdentity = identity();
      respond(response, 200, {
        active_project_id: currentIdentity.active_project_id,
        active_project: currentIdentity.active_project,
        projects: currentIdentity.projects,
      });
      return;
    }
    if (request.method === "PATCH" && url.pathname === `/api/mobile/memoir/projects/${FIXTURE_PROJECT_ID}`) {
      state.projectTitle = body.title || state.projectTitle;
      state.projectProfile = {
        ...state.projectProfile,
        narrator_name: body.elder_name,
        narrator_gender: body.narrator_gender,
        narrator_origin: body.narrator_origin,
        narrator_age: body.narrator_age,
        default_guidance_mode: body.default_guidance_mode,
        default_interview_type: body.default_interview_type,
        default_session_goal: body.default_session_goal,
      };
      respond(response, 200, projectDetail());
      return;
    }
    if (request.method === "GET" && url.pathname === `/api/memoir/projects/${FIXTURE_PROJECT_ID}/book-completion/overview`) {
      respond(response, 200, {
        project_id: FIXTURE_PROJECT_ID, headline: "还没有待补问题", detail: "先讲一段回忆，再整理书稿。",
        total_chapters: 0, completed_chapters: 0, chapters: [],
        completion_job: {
          job_id: null, project_id: FIXTURE_PROJECT_ID, status: "idle", total_chapter_count: 0,
          processed_chapter_count: 0, succeeded_chapter_count: 0, failed_chapter_count: 0,
          cancelled_chapter_count: 0, current_chapter_id: null, current_chapter_title: "",
          requested_ai_model: null, chapters: [], can_cancel: false, can_retry: false, error_message: "",
        },
        book_review: null, book_review_job: null, book_issues: [], book_output_available: false, revision_job: null,
      });
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/mobile/memoir/voice-sessions") {
      state.sessionConfig = body;
      state.createdSessions += 1;
      state.sessionId = `${sessionPrefix}-${state.createdSessions}`;
      state.sessionStatus = "created";
      state.messages = body.interview_type === "human_interview" ? [] : [initialAssistantMessage(state.sessionId)];
      state.utterances = [];
      respond(response, 200, sessionDetail());
      return;
    }
    if (request.method === "GET" && url.pathname === `/api/mobile/memoir/projects/${FIXTURE_PROJECT_ID}/automation-events`) {
      respond(response, 200, { items: [], total: 0, limit: 8 });
      return;
    }
    if (request.method === "GET" && url.pathname === `/api/mobile/memoir/projects/${FIXTURE_PROJECT_ID}/draft-progress`) {
      respond(response, 200, { status: "idle", title: "等待讲述", detail: "", can_retry: false });
      return;
    }

    if (
      request.method === "GET"
      && url.pathname === `/api/mobile/memoir/voice-sessions/${state.sessionId}`
    ) {
      respond(response, 200, sessionDetail());
      return;
    }
    if (
      request.method === "POST"
      && url.pathname === "/api/mobile/memoir/voice-authorization-consent"
    ) {
      respond(response, 200, {
        id: "e2e-consent",
        user_id: "e2e-user",
        consent_type: "voice_authorization",
        version: body.version,
        agreed: body.agreed,
        source: body.source,
        source_session_id: body.source_session_id || null,
        metadata_json: body.metadata_json || {},
        created_at: timestamp(),
        updated_at: timestamp(),
      });
      return;
    }
    if (request.method === "POST" && url.pathname === "/api/audit/events") {
      respond(response, 200, {
        accepted: Array.isArray(body.events) ? body.events.length : 0,
        duplicate: 0,
        session_id: "e2e-audit-session",
      });
      return;
    }

    state.unexpectedRequests.push({ method: request.method, path: url.pathname });
    respond(response, 404, { detail: "E2E fixture route not found" });
  });

  function send(webSocket, event) {
    if (webSocket.readyState === webSocket.OPEN) {
      webSocket.send(JSON.stringify(event));
    }
  }

  function persistUserTurn() {
    if (state.messages.some((message) => message.id === FIXTURE_USER_MESSAGE_ID)) {
      return;
    }
    state.messages.push({
      id: FIXTURE_USER_MESSAGE_ID,
      project_id: FIXTURE_PROJECT_ID,
      voice_session_id: state.sessionId,
      role: "user",
      content_text: FIXTURE_USER_TEXT,
      content_status: "final",
      audio_file_id: "e2e-user-audio",
      transcript_version_id: "e2e-transcript-version",
      parent_message_id: "e2e-opening-message",
      guidance_intent: null,
      turn_index: 1,
      metadata_json: {},
      created_at: timestamp(),
      updated_at: timestamp(),
    });
    state.utterances.push({
      id: FIXTURE_UTTERANCE_ID,
      voice_session_id: state.sessionId,
      chat_message_id: FIXTURE_USER_MESSAGE_ID,
      audio_file_id: "e2e-user-audio",
      transcript_raw: FIXTURE_USER_TEXT,
      transcript_edited: FIXTURE_USER_TEXT,
      start_ms: 0,
      end_ms: 1200,
      provider: "e2e-asr",
      provider_payload_json: { fixture: true },
      completion_issue_id: null,
      created_at: timestamp(),
    });
  }

  function persistAssistantTurn() {
    if (state.messages.some((message) => message.id === FIXTURE_ASSISTANT_MESSAGE_ID)) {
      return;
    }
    state.messages.push({
      id: FIXTURE_ASSISTANT_MESSAGE_ID,
      project_id: FIXTURE_PROJECT_ID,
      voice_session_id: state.sessionId,
      role: "assistant",
      content_text: FIXTURE_ASSISTANT_TEXT,
      content_status: "final",
      audio_file_id: "e2e-assistant-audio",
      transcript_version_id: null,
      parent_message_id: FIXTURE_USER_MESSAGE_ID,
      guidance_intent: "acknowledge",
      turn_index: 2,
      metadata_json: {},
      created_at: timestamp(),
      updated_at: timestamp(),
    });
  }

  function handleGateway(webSocket) {
    const connection = {
      id: `gateway-${state.gatewayConnections.length + 1}`,
      events: [],
    };
    state.gatewayConnections.push(connection);
    setTimeout(() => send(webSocket, { type: "session.ready", state: "ready" }), 10);
    let partialSent = false;
    webSocket.on("message", (raw) => {
      const event = JSON.parse(raw.toString("utf8"));
      connection.events.push(event);
      state.gatewayClientEvents.push(event);
      if (event.type === "session.start") {
        state.activeGatewayClientEvents = connection.events;
        state.sessionStatus = "listening";
        send(webSocket, { type: "session.started", state: "listening" });
        return;
      }
      if (event.type === "audio.frame" && !partialSent) {
        partialSent = true;
        send(webSocket, {
          type: "asr.partial",
          state: "listening",
          text: "那年夏天，我第一次坐火车",
        });
        return;
      }
      if (event.type === "audio.end") {
        persistUserTurn();
        send(webSocket, {
          type: "asr.final",
          state: "ready",
          text: FIXTURE_USER_TEXT,
          message_id: FIXTURE_USER_MESSAGE_ID,
          utterance_id: FIXTURE_UTTERANCE_ID,
          audio_file_id: "e2e-user-audio",
          transcript_version_id: "e2e-transcript-version",
          start_ms: 0,
          end_ms: 1200,
          turn_final: true,
          speaker_role: "narrator",
        });
        send(webSocket, { type: "audio.ended", state: "ready" });
        return;
      }
      if (event.type === "session.stop") {
        state.sessionStatus = "stopped";
        send(webSocket, { type: "session.stopped", state: "stopped" });
      }
    });
  }

  function handleAssistant(webSocket) {
    setTimeout(() => send(webSocket, { type: "turn.ready" }), 10);
    webSocket.on("message", (raw) => {
      const event = JSON.parse(raw.toString("utf8"));
      state.assistantClientEvents.push(event);
      if (event.type !== "turn.start") {
        return;
      }
      persistAssistantTurn();
      const metadata = {
        turn_id: "e2e-turn",
        voice_session_id: state.sessionId,
      };
      send(webSocket, {
        ...metadata,
        type: "turn.started",
        state: "speaking",
        requested_ai_model: event.ai_model || null,
        resolved_ai_model: event.ai_model || "e2e-model",
        tts_provider: "e2e-tts",
        tts_model: "e2e-pcm",
        audio_protocol: "pcm_frame",
      });
      send(webSocket, {
        ...metadata,
        type: "text.delta",
        project_id: FIXTURE_PROJECT_ID,
        text_delta: FIXTURE_ASSISTANT_TEXT,
        text: FIXTURE_ASSISTANT_TEXT,
      });
      send(webSocket, {
        ...metadata,
        type: "audio.format",
        sequence: 0,
        mime_type: "audio/pcm",
        provider: "e2e-tts",
        model: "e2e-pcm",
        sample_rate: 16000,
        channels: 1,
        bits_per_sample: 16,
      });
      send(webSocket, {
        ...metadata,
        type: "audio.frame",
        sequence: 0,
        mime_type: "audio/pcm",
        audio: Buffer.alloc(3200).toString("base64"),
        sample_rate: 16000,
        channels: 1,
        bits_per_sample: 16,
      });
      send(webSocket, {
        ...metadata,
        type: "audio.done",
        frame_count: 1,
        duration_seconds: 0.1,
        sample_rate: 16000,
        channels: 1,
        bits_per_sample: 16,
      });
      send(webSocket, {
        ...metadata,
        type: "turn.completed",
        project_id: FIXTURE_PROJECT_ID,
        state: "completed",
        message_id: FIXTURE_ASSISTANT_MESSAGE_ID,
        audio_file_id: "e2e-assistant-audio",
        text: FIXTURE_ASSISTANT_TEXT,
        primary_intent: "acknowledge",
        intent_tags: ["e2e"],
        expects_answer: false,
        requested_ai_model: event.ai_model || null,
        resolved_ai_model: event.ai_model || "e2e-model",
        progress_voice_selected_event_ids: [],
        turn_pipeline: "e2e-fixture",
      });
    });
  }

  server.on("upgrade", (request, socket, head) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    const gatewayPath = `/api/mobile/memoir/voice-sessions/${state.sessionId}/gateway`;
    const assistantPath = `/api/mobile/memoir/voice-sessions/${state.sessionId}/assistant-turn-stream`;
    if (url.pathname !== gatewayPath && url.pathname !== assistantPath) {
      state.unexpectedRequests.push({ method: "WS", path: url.pathname });
      socket.destroy();
      return;
    }
    webSockets.handleUpgrade(request, socket, head, (webSocket) => {
      clients.add(webSocket);
      webSocket.once("close", () => clients.delete(webSocket));
      if (url.pathname === gatewayPath) {
        handleGateway(webSocket);
      } else {
        handleAssistant(webSocket);
      }
    });
  });

  return {
    state,
    reset() {
      state.activeGatewayClientEvents = [];
      state.assistantClientEvents.length = 0;
      state.gatewayClientEvents.length = 0;
      state.gatewayConnections.length = 0;
      state.httpRequests.length = 0;
      state.createdSessions = 0;
      state.sessionId = sessionPrefix;
      state.sessionConfig = {};
      state.unexpectedRequests.length = 0;
      state.sessionStatus = "created";
      state.messages = [initialAssistantMessage(state.sessionId)];
      state.utterances = [];
    },
    async start() {
      await new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(0, "127.0.0.1", resolve);
      });
      const address = server.address();
      if (!address || typeof address === "string") {
        throw new Error("E2E voice fixture did not bind a TCP port");
      }
      return {
        httpBaseUrl: `http://127.0.0.1:${address.port}`,
        wsBaseUrl: `ws://127.0.0.1:${address.port}`,
      };
    },
    async close() {
      for (const client of clients) {
        client.terminate();
      }
      await new Promise((resolve) => webSockets.close(resolve));
      if (server.listening) {
        await new Promise((resolve, reject) => {
          server.close((error) => (error ? reject(error) : resolve()));
        });
      }
    },
  };
}

module.exports = {
  FIXTURE_ASSISTANT_TEXT,
  FIXTURE_PROJECT_ID,
  FIXTURE_SESSION_ID,
  FIXTURE_USER_TEXT,
  createVoiceFixtureServer,
};
