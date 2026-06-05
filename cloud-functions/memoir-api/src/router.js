const {
  ApiError,
  errorResponse,
  jsonResponse,
  normalizeMethod,
  normalizePath,
  route,
} = require("./http");
const { createMemoirService } = require("./memoirService");
const {
  createTextAnswerRequest,
  registerAudioFileRequest,
  updateAnswerTextRequest,
  updateDraftContentRequest,
} = require("./schemas");

function requireStore(request) {
  if (!request.store) {
    throw new ApiError(500, "云数据库未配置");
  }
  return request.store;
}

function safePathSegment(value) {
  return String(value || "").replace(/[^a-zA-Z0-9_-]/g, "_");
}

function cloudUserResponse(context = {}) {
  if (!context.openid) {
    throw new ApiError(401, "缺少微信登录身份");
  }
  const now = new Date().toISOString();
  return {
    id: context.openid,
    email: "",
    display_name: "微信用户",
    storage_owner_id: safePathSegment(context.openid),
    created_at: now,
  };
}

function memoirService(request) {
  return createMemoirService(requireStore(request), request.context || {}, {
    asr: request.asr,
    drafting: request.drafting,
    storage: request.storage,
  });
}

const routes = {
  GET: [
    route("/api/mobile/memoir/health", async () => jsonResponse(200, {
      ok: true,
      service: "memoir-api",
    })),
    route("/api/mobile/memoir/me", async (request) => jsonResponse(
      200,
      cloudUserResponse(request.context),
    )),
    route("/api/mobile/memoir/project", async (request) => jsonResponse(
      200,
      await memoirService(request).getProject(),
    )),
    route("/api/mobile/memoir/sessions/current", async (request) => jsonResponse(
      200,
      await memoirService(request).getCurrentSession(),
    )),
    route("/api/mobile/memoir/questions/next", async (request) => jsonResponse(
      200,
      await memoirService(request).getNextQuestion(),
    )),
    route("/api/mobile/memoir/summary", async (request) => jsonResponse(
      200,
      await memoirService(request).getSummary(),
    )),
    route("/api/mobile/memoir/stories", async (request) => jsonResponse(
      200,
      await memoirService(request).listStoryCards(),
    )),
    route("/api/mobile/memoir/stories/:answerId", async (request) => jsonResponse(
      200,
      await memoirService(request).getStoryDetail(request.params.answerId),
    )),
  ],
  POST: [
    route("/api/mobile/memoir/questions/:questionId/skip", async (request) => jsonResponse(
      200,
      await memoirService(request).skipQuestion(request.params.questionId),
    )),
    route("/api/mobile/memoir/answers", async (request) => jsonResponse(
      201,
      await memoirService(request).createTextAnswer(createTextAnswerRequest(request.data)),
    )),
    route("/api/mobile/memoir/audio-files", async (request) => jsonResponse(
      201,
      await memoirService(request).registerAudioFile(registerAudioFileRequest(request.data)),
    )),
    route("/api/mobile/memoir/audio-files/:audioFileId/transcribe", async (request) => jsonResponse(
      200,
      await memoirService(request).transcribeAudioFile(request.params.audioFileId),
    )),
    route("/api/mobile/memoir/answers/:answerId/draft", async (request) => jsonResponse(
      200,
      await memoirService(request).generateDraft(request.params.answerId),
    )),
    route("/api/mobile/memoir/drafts/:draftId/accept", async (request) => jsonResponse(
      200,
      await memoirService(request).acceptDraft(request.params.draftId),
    )),
  ],
  PATCH: [
    route("/api/mobile/memoir/answers/:answerId", async (request) => jsonResponse(
      200,
      await memoirService(request).updateAnswerText(request.params.answerId, updateAnswerTextRequest(request.data)),
    )),
    route("/api/mobile/memoir/drafts/:draftId", async (request) => jsonResponse(
      200,
      await memoirService(request).updateDraftContent(request.params.draftId, updateDraftContentRequest(request.data)),
    )),
  ],
};

async function handleMemoirApiRequest(event = {}, context = {}, dependencies = {}) {
  try {
    const method = normalizeMethod(event.method);
    const path = normalizePath(event.path);
    const request = {
      method,
      path,
      data: event.data || {},
      context,
      event,
      asr: dependencies.asr,
      drafting: dependencies.drafting,
      store: dependencies.store,
      storage: dependencies.storage,
    };

    for (const routeConfig of routes[method] || []) {
      const params = routeConfig.match(path);
      if (params) {
        return await routeConfig.handler({ ...request, params });
      }
    }

    throw new ApiError(404, "接口未实现", { path, method });
  } catch (error) {
    return errorResponse(error);
  }
}

module.exports = {
  handleMemoirApiRequest,
};
