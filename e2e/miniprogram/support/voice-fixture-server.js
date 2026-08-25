const http = require("node:http");
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

function initialAssistantMessage() {
  return {
    id: "e2e-opening-message",
    project_id: FIXTURE_PROJECT_ID,
    voice_session_id: FIXTURE_SESSION_ID,
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
  const server = http.createServer();
  const webSockets = new WebSocketServer({ noServer: true });
  const clients = new Set();
  const state = {
    assistantClientEvents: [],
    gatewayClientEvents: [],
    httpRequests: [],
    sessionStatus: "created",
    unexpectedRequests: [],
    messages: [initialAssistantMessage()],
    utterances: [],
  };

  function sessionDetail() {
    return {
      session: {
        id: FIXTURE_SESSION_ID,
        project_id: FIXTURE_PROJECT_ID,
        status: state.sessionStatus,
        guidance_mode: "free_narration",
        interview_type: "ai_interview",
        session_goal: "open_narration",
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

    if (
      request.method === "GET"
      && url.pathname === `/api/mobile/memoir/voice-sessions/${FIXTURE_SESSION_ID}`
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
      voice_session_id: FIXTURE_SESSION_ID,
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
      voice_session_id: FIXTURE_SESSION_ID,
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
      voice_session_id: FIXTURE_SESSION_ID,
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
    setTimeout(() => send(webSocket, { type: "session.ready", state: "ready" }), 10);
    let partialSent = false;
    webSocket.on("message", (raw) => {
      const event = JSON.parse(raw.toString("utf8"));
      state.gatewayClientEvents.push(event);
      if (event.type === "session.start") {
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
        voice_session_id: FIXTURE_SESSION_ID,
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
    const gatewayPath = `/api/mobile/memoir/voice-sessions/${FIXTURE_SESSION_ID}/gateway`;
    const assistantPath = `/api/mobile/memoir/voice-sessions/${FIXTURE_SESSION_ID}/assistant-turn-stream`;
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
      state.assistantClientEvents.length = 0;
      state.gatewayClientEvents.length = 0;
      state.httpRequests.length = 0;
      state.unexpectedRequests.length = 0;
      state.sessionStatus = "created";
      state.messages = [initialAssistantMessage()];
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
  FIXTURE_SESSION_ID,
  FIXTURE_USER_TEXT,
  createVoiceFixtureServer,
};
