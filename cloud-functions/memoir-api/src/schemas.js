const { ApiError } = require("./http");

function objectData(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new ApiError(400, "请求体必须是 JSON 对象");
  }
  return data;
}

function requiredString(data, field, message) {
  const value = data[field];
  if (typeof value !== "string" || !value.trim()) {
    throw new ApiError(400, message);
  }
  return value;
}

function optionalString(data, field) {
  const value = data[field];
  if (value === undefined || value === null || value === "") {
    return undefined;
  }
  if (typeof value !== "string") {
    throw new ApiError(400, `${field} 必须是字符串`);
  }
  return value;
}

function updateWechatUserRequest(data) {
  const body = objectData(data);
  return {
    display_name: optionalString(body, "display_name"),
    wechat_nickname: optionalString(body, "wechat_nickname"),
    wechat_avatar_url: optionalString(body, "wechat_avatar_url"),
    unionid: optionalString(body, "unionid"),
  };
}

function createProjectRequest(data) {
  const body = objectData(data);
  return {
    title: optionalString(body, "title"),
    description: optionalString(body, "description"),
    elder_name: optionalString(body, "elder_name"),
  };
}

function updateProjectRequest(data) {
  const body = objectData(data);
  return {
    title: optionalString(body, "title"),
    description: optionalString(body, "description"),
    elder_name: optionalString(body, "elder_name"),
  };
}

module.exports = {
  createProjectRequest,
  updateProjectRequest,
  updateWechatUserRequest,
};
