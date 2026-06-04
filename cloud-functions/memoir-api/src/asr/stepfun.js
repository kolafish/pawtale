const https = require("node:https");

const { ApiError } = require("../http");

const DEFAULT_BASE_URL = "https://api.stepfun.com";
const DEFAULT_MODEL = "stepaudio-2.5-asr";

const MIME_FORMATS = Object.freeze({
  "audio/mpeg": "mp3",
  "audio/mp3": "mp3",
  "audio/wav": "wav",
  "audio/x-wav": "wav",
  "audio/wave": "wav",
  "audio/ogg": "ogg",
});

function apiKeyFromEnv(env = process.env) {
  return env.STEPFUN_API_KEY || env.STEP_API_KEY || "";
}

function audioFormat(audioFile) {
  const mimeType = String(audioFile.mime_type || "").toLowerCase();
  const fromMime = MIME_FORMATS[mimeType];
  if (fromMime) {
    return { type: fromMime };
  }

  const path = String(audioFile.cloud_path || "").toLowerCase();
  const extension = path.match(/\.([a-z0-9]+)$/)?.[1];
  if (extension && ["mp3", "wav", "ogg"].includes(extension)) {
    return { type: extension };
  }

  throw new ApiError(400, "音频格式暂不支持转写");
}

function buildPayload(audioBytes, audioFile, options = {}) {
  return {
    audio: {
      data: audioBytes.toString("base64"),
      input: {
        transcription: {
          language: options.language || "zh",
          model: options.model || DEFAULT_MODEL,
          enable_itn: true,
          enable_timestamp: false,
        },
        format: audioFormat(audioFile),
      },
    },
  };
}

function parseSseResponse(text) {
  const events = String(text || "")
    .split(/\n\s*\n/)
    .map((block) => block
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim())
      .join(""))
    .filter(Boolean);

  let streamedText = "";
  let usage = null;
  for (const eventText of events) {
    if (eventText === "[DONE]") {
      continue;
    }

    let event;
    try {
      event = JSON.parse(eventText);
    } catch {
      continue;
    }

    if (event.type === "error") {
      throw new ApiError(503, event.message || "语音转写服务暂时不可用，请稍后重试。");
    }
    if (event.type === "transcript.text.delta") {
      streamedText += event.delta || "";
    }
    if (event.type === "transcript.text.done") {
      return {
        text: String(event.text || streamedText).trim(),
        usage: event.usage || null,
      };
    }
  }

  return {
    text: streamedText.trim(),
    usage,
  };
}

function postJsonSse(url, headers, payload) {
  const body = JSON.stringify(payload);
  const target = new URL(url);

  return new Promise((resolve, reject) => {
    const request = https.request({
      method: "POST",
      hostname: target.hostname,
      path: `${target.pathname}${target.search}`,
      headers: {
        ...headers,
        "Content-Length": Buffer.byteLength(body),
      },
    }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        const responseText = Buffer.concat(chunks).toString("utf8");
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new ApiError(503, "语音转写服务暂时不可用，请稍后重试。", {
            upstream_status: response.statusCode,
          }));
          return;
        }
        resolve(responseText);
      });
    });

    request.on("error", () => {
      reject(new ApiError(503, "语音转写服务暂时不可用，请稍后重试。"));
    });
    request.write(body);
    request.end();
  });
}

function createStepFunASRService(options = {}) {
  return {
    async transcribe(audioBytes, audioFile) {
      const env = options.env || process.env;
      const apiKey = options.apiKey || apiKeyFromEnv(env);
      if (!apiKey) {
        throw new ApiError(500, "缺少 StepFun ASR API Key");
      }

      const baseUrl = options.baseUrl || env.STEPFUN_BASE_URL || DEFAULT_BASE_URL;
      const payload = buildPayload(audioBytes, audioFile, {
        ...options,
        model: options.model || env.STEPFUN_ASR_MODEL || DEFAULT_MODEL,
      });
      const transport = options.transport || postJsonSse;
      const responseText = await transport(`${baseUrl}/v1/audio/asr/sse`, {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      }, payload);
      const result = parseSseResponse(responseText);
      if (!result.text) {
        throw new ApiError(422, "未识别到语音内容");
      }
      return result;
    },
  };
}

module.exports = {
  audioFormat,
  buildPayload,
  createStepFunASRService,
  parseSseResponse,
};
