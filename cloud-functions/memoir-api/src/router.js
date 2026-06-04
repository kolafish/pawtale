const {
  ApiError,
  errorResponse,
  jsonResponse,
  normalizeMethod,
  normalizePath,
  route,
} = require("./http");
const { createMemoirService } = require("./memoirService");

function requireStore(request) {
  if (!request.store) {
    throw new ApiError(500, "云数据库未配置");
  }
  return request.store;
}

function memoirService(request) {
  return createMemoirService(requireStore(request), request.context || {});
}

const routes = {
  GET: [
    route("/api/mobile/memoir/health", async () => jsonResponse(200, {
      ok: true,
      service: "memoir-api",
    })),
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
  ],
  POST: [
    route("/api/mobile/memoir/questions/:questionId/skip", async (request) => jsonResponse(
      200,
      await memoirService(request).skipQuestion(request.params.questionId),
    )),
    route("/api/mobile/memoir/answers", async (request) => jsonResponse(
      200,
      await memoirService(request).createTextAnswer(request.data),
    )),
  ],
  PATCH: [
    route("/api/mobile/memoir/answers/:answerId", async (request) => jsonResponse(
      200,
      await memoirService(request).updateAnswerText(request.params.answerId, request.data),
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
      store: dependencies.store,
    };

    for (const routeConfig of routes[method] || []) {
      const params = routeConfig.match(path);
      if (params) {
        return routeConfig.handler({ ...request, params });
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
