class ApiError extends Error {
  constructor(statusCode, detail, data = {}) {
    super(detail);
    this.statusCode = statusCode;
    this.detail = detail;
    this.data = data;
  }
}

function jsonResponse(statusCode, data) {
  return {
    statusCode,
    data,
  };
}

function errorResponse(error) {
  if (error instanceof ApiError) {
    return jsonResponse(error.statusCode, {
      detail: error.detail,
      ...error.data,
    });
  }

  console.error("[memoir-api] unhandled error", error);
  return jsonResponse(500, {
    detail: "云函数内部错误",
  });
}

function normalizeMethod(method) {
  return String(method || "GET").trim().toUpperCase();
}

function normalizePath(path) {
  if (typeof path !== "string" || !path.trim()) {
    throw new ApiError(400, "缺少请求路径");
  }

  const cleanPath = path.trim();
  return cleanPath.startsWith("/") ? cleanPath : `/${cleanPath}`;
}

function route(pattern, handler) {
  const keys = [];
  const source = pattern
    .split("/")
    .map((part) => {
      if (!part.startsWith(":")) {
        return part;
      }
      keys.push(part.slice(1));
      return "([^/]+)";
    })
    .join("/");
  const matcher = new RegExp(`^${source}$`);

  return {
    pattern,
    match(path) {
      const result = matcher.exec(path);
      if (!result) {
        return null;
      }
      return Object.fromEntries(keys.map((key, index) => [key, decodeURIComponent(result[index + 1])]));
    },
    handler,
  };
}

module.exports = {
  ApiError,
  errorResponse,
  jsonResponse,
  normalizeMethod,
  normalizePath,
  route,
};
