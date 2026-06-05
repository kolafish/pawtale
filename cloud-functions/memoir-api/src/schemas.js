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

function optionalNumber(data, field) {
  const value = data[field];
  if (value === undefined || value === null || value === "") {
    return undefined;
  }
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) {
    throw new ApiError(400, `${field} 必须是数字`);
  }
  return numberValue;
}

function createTextAnswerRequest(data) {
  const body = objectData(data);
  return {
    question_id: requiredString(body, "question_id", "缺少问题 ID"),
    text: requiredString(body, "text", "回答不能为空"),
    audio_file_id: optionalString(body, "audio_file_id"),
    duration_seconds: optionalNumber(body, "duration_seconds"),
  };
}

function registerAudioFileRequest(data) {
  const body = objectData(data);
  return {
    file_id: requiredString(body, "file_id", "缺少云存储文件 ID"),
    cloud_path: requiredString(body, "cloud_path", "缺少云存储路径"),
    mime_type: optionalString(body, "mime_type"),
    size_bytes: optionalNumber(body, "size_bytes"),
    duration_seconds: optionalNumber(body, "duration_seconds"),
  };
}

function updateAnswerTextRequest(data) {
  const body = objectData(data);
  return {
    text: requiredString(body, "text", "回答不能为空"),
  };
}

function updateDraftContentRequest(data) {
  const body = objectData(data);
  return {
    content_edited: requiredString(body, "content_edited", "整理稿不能为空"),
  };
}

module.exports = {
  createTextAnswerRequest,
  registerAudioFileRequest,
  updateAnswerTextRequest,
  updateDraftContentRequest,
};
