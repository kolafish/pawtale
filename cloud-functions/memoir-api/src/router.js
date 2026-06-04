const {
  ApiError,
  errorResponse,
  jsonResponse,
  normalizeMethod,
  normalizePath,
  route,
} = require("./http");

const routes = {
  GET: [
    route("/api/mobile/memoir/health", async () => jsonResponse(200, {
      ok: true,
      service: "memoir-api",
    })),
  ],
};

async function handleMemoirApiRequest(event = {}, context = {}) {
  try {
    const method = normalizeMethod(event.method);
    const path = normalizePath(event.path);
    const request = {
      method,
      path,
      data: event.data || {},
      context,
      event,
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
