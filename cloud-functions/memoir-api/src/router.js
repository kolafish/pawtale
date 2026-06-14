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
  createProjectRequest,
  updateProjectRequest,
  updateWechatUserRequest,
} = require("./schemas");

function requireStore(request) {
  if (!request.store) {
    throw new ApiError(500, "云数据库未配置");
  }
  return request.store;
}

function memoirService(request) {
  return createMemoirService(requireStore(request), request.context || {}, {
    memoryExtraction: request.memoryExtraction,
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
      await memoirService(request).getMe(),
    )),
    route("/api/mobile/memoir/projects", async (request) => jsonResponse(
      200,
      await memoirService(request).listProjects(),
    )),
    route("/api/mobile/memoir/project", async (request) => jsonResponse(
      200,
      await memoirService(request).getProject(),
    )),
    route("/api/mobile/memoir/dashboard", async (request) => jsonResponse(
      200,
      await memoirService(request).getMemoryDashboard(),
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
    route("/api/mobile/memoir/sessions/:sessionId/summary", async (request) => jsonResponse(
      200,
      await memoirService(request).getSessionSummary(request.params.sessionId),
    )),
    route("/api/mobile/memoir/memories/people", async (request) => jsonResponse(
      200,
      await memoirService(request).listPersonMemories(),
    )),
    route("/api/mobile/memoir/memories/events", async (request) => jsonResponse(
      200,
      await memoirService(request).listEventMemories(),
    )),
    route("/api/mobile/memoir/memories/places", async (request) => jsonResponse(
      200,
      await memoirService(request).listPlaceMemories(),
    )),
    route("/api/mobile/memoir/memories/themes", async (request) => jsonResponse(
      200,
      await memoirService(request).listThemeMemories(),
    )),
    route("/api/mobile/memoir/open-questions", async (request) => jsonResponse(
      200,
      await memoirService(request).listOpenQuestions(),
    )),
    route("/api/mobile/memoir/coverage", async (request) => jsonResponse(
      200,
      await memoirService(request).getCoverageMap(),
    )),
    route("/api/mobile/memoir/question-candidates", async (request) => jsonResponse(
      200,
      await memoirService(request).listQuestionCandidates(),
    )),
    route("/api/mobile/memoir/outputs", async (request) => jsonResponse(
      200,
      await memoirService(request).listOutputs(),
    )),
    route("/api/mobile/memoir/outputs/:outputId", async (request) => jsonResponse(
      200,
      await memoirService(request).getOutput(request.params.outputId),
    )),
  ],
  POST: [
    route("/api/mobile/memoir/projects", async (request) => jsonResponse(
      201,
      await memoirService(request).createProject(createProjectRequest(request.data)),
    )),
    route("/api/mobile/memoir/projects/:projectId/activate", async (request) => jsonResponse(
      200,
      await memoirService(request).activateProject(request.params.projectId),
    )),
    route("/api/mobile/memoir/sessions/:sessionId/process", async (request) => jsonResponse(
      200,
      await memoirService(request).processSessionMemory(request.params.sessionId),
    )),
    route("/api/mobile/memoir/coverage/recalculate", async (request) => jsonResponse(
      200,
      await memoirService(request).recalculateCoverage(),
    )),
    route("/api/mobile/memoir/question-candidates/generate", async (request) => jsonResponse(
      200,
      await memoirService(request).generateQuestionCandidates(),
    )),
  ],
  PATCH: [
    route("/api/mobile/memoir/me", async (request) => jsonResponse(
      200,
      await memoirService(request).updateMe(updateWechatUserRequest(request.data)),
    )),
    route("/api/mobile/memoir/projects/:projectId", async (request) => jsonResponse(
      200,
      await memoirService(request).updateProject(request.params.projectId, updateProjectRequest(request.data)),
    )),
    route("/api/mobile/memoir/memories/:memoryType/:memoryId", async (request) => jsonResponse(
      200,
      await memoirService(request).updateMemory(request.params.memoryType, request.params.memoryId, request.data),
    )),
    route("/api/mobile/memoir/open-questions/:questionId", async (request) => jsonResponse(
      200,
      await memoirService(request).updateOpenQuestion(request.params.questionId, request.data),
    )),
  ],
  DELETE: [],
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
      memoryExtraction: dependencies.memoryExtraction,
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
