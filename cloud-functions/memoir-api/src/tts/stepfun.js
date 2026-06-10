const crypto = require("node:crypto");
const https = require("node:https");

const { ApiError } = require("../http");

const DEFAULT_BASE_URL = "https://api.stepfun.com";
const DEFAULT_MODEL = "stepaudio-2.5-tts";
const DEFAULT_VOICE = "cixingnansheng";
const DEFAULT_FORMAT = "mp3";
const DEFAULT_SAMPLE_RATE = 24000;
const DEFAULT_CHUNK_LIMIT = 900;
const SUPPORTED_FORMATS = new Set(["mp3"]);

function apiKeyFromEnv(env = process.env) {
  return env.STEPFUN_API_KEY || env.STEP_API_KEY || "";
}

function normalizeFormat(value) {
  const format = String(value || DEFAULT_FORMAT).trim().toLowerCase();
  if (!SUPPORTED_FORMATS.has(format)) {
    throw new ApiError(400, "朗读音频格式暂不支持");
  }
  return format;
}

function normalizeSpeed(value, fallback = 1) {
  if (value === undefined || value === null || value === "") {
    return fallback;
  }
  const speed = Number(value);
  if (!Number.isFinite(speed)) {
    return fallback;
  }
  return Math.max(0.5, Math.min(2, speed));
}

function normalizeTtsInputText(text) {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/\(/g, "（")
    .replace(/\)/g, "）")
    .replace(/[ \t]+\n/g, "\n")
    .trim();
}

function pushTextChunk(chunks, text, limit) {
  let rest = text.trim();
  while (rest.length > limit) {
    chunks.push(rest.slice(0, limit).trim());
    rest = rest.slice(limit).trim();
  }
  if (rest) {
    chunks.push(rest);
  }
}

function splitTextForTts(text, limit = DEFAULT_CHUNK_LIMIT) {
  const normalized = normalizeTtsInputText(text);
  if (!normalized) {
    return [];
  }

  const chunks = [];
  const paragraphs = normalized.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean);
  const sentencePattern = /[^。！？!?；;\n]+[。！？!?；;]?/g;
  for (const paragraph of paragraphs.length ? paragraphs : [normalized]) {
    if (paragraph.length <= limit) {
      chunks.push(paragraph);
      continue;
    }

    let current = "";
    const sentences = paragraph.match(sentencePattern) || [paragraph];
    for (const sentence of sentences) {
      const next = `${current}${sentence}`.trim();
      if (next.length <= limit) {
        current = next;
        continue;
      }
      if (current) {
        chunks.push(current);
      }
      if (sentence.length > limit) {
        pushTextChunk(chunks, sentence, limit);
        current = "";
      } else {
        current = sentence.trim();
      }
    }
    if (current) {
      chunks.push(current);
    }
  }

  return chunks.filter(Boolean);
}

function buildSpeechPayload(input, options = {}) {
  return {
    model: options.model || DEFAULT_MODEL,
    input: normalizeTtsInputText(input),
    voice: options.voice || DEFAULT_VOICE,
    response_format: normalizeFormat(options.format),
    speed: normalizeSpeed(options.speed),
    sample_rate: options.sampleRate || DEFAULT_SAMPLE_RATE,
  };
}

function parseError(buffer) {
  const text = buffer.toString("utf8");
  try {
    const parsed = JSON.parse(text);
    return parsed?.error?.message || parsed?.message || text;
  } catch {
    return text;
  }
}

function postSpeech(url, headers, payload) {
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
        const audioBytes = Buffer.concat(chunks);
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new ApiError(503, "语音朗读服务暂时不可用，请稍后重试。", {
            upstream_status: response.statusCode,
            upstream_message: parseError(audioBytes).slice(0, 200),
          }));
          return;
        }
        resolve({
          audioBytes,
          contentType: response.headers["content-type"] || "audio/mpeg",
        });
      });
    });

    request.on("error", () => {
      reject(new ApiError(503, "语音朗读服务暂时不可用，请稍后重试。"));
    });
    request.write(body);
    request.end();
  });
}

function createStepFunTTSService(options = {}) {
  return {
    async synthesize({ text, voice, model, format, speed } = {}) {
      const env = options.env || process.env;
      const apiKey = options.apiKey || apiKeyFromEnv(env);
      if (!apiKey) {
        throw new ApiError(500, "缺少 StepFun TTS API Key");
      }

      const cleanText = normalizeTtsInputText(text);
      if (!cleanText) {
        throw new ApiError(400, "朗读内容不能为空");
      }

      const baseUrl = options.baseUrl || env.STEPFUN_TTS_BASE_URL || env.STEPFUN_BASE_URL || DEFAULT_BASE_URL;
      const targetFormat = normalizeFormat(format || env.STEPFUN_TTS_FORMAT || DEFAULT_FORMAT);
      const chunkLimit = options.chunkLimit || DEFAULT_CHUNK_LIMIT;
      const chunks = splitTextForTts(cleanText, chunkLimit);
      const transport = options.transport || postSpeech;
      const buffers = [];
      let contentType = "audio/mpeg";

      for (const chunk of chunks) {
        const payload = buildSpeechPayload(chunk, {
          model: model || env.STEPFUN_TTS_MODEL || DEFAULT_MODEL,
          voice: voice || env.STEPFUN_TTS_VOICE || DEFAULT_VOICE,
          format: targetFormat,
          speed: speed || env.STEPFUN_TTS_SPEED || 1,
          sampleRate: options.sampleRate || env.STEPFUN_TTS_SAMPLE_RATE || DEFAULT_SAMPLE_RATE,
        });
        const result = await transport(`${baseUrl}/v1/audio/speech`, {
          Authorization: `Bearer ${apiKey}`,
          "Content-Type": "application/json",
          Accept: "audio/*",
        }, payload);
        const audioBytes = Buffer.isBuffer(result) ? result : result.audioBytes;
        if (!Buffer.isBuffer(audioBytes) || audioBytes.length === 0) {
          throw new ApiError(503, "语音朗读服务返回了空音频");
        }
        contentType = result.contentType || contentType;
        buffers.push(audioBytes);
      }

      return {
        audioBytes: Buffer.concat(buffers),
        mime_type: contentType.split(";")[0] || "audio/mpeg",
        chunk_count: chunks.length,
        model: model || env.STEPFUN_TTS_MODEL || DEFAULT_MODEL,
        voice: voice || env.STEPFUN_TTS_VOICE || DEFAULT_VOICE,
        format: targetFormat,
        speed: normalizeSpeed(speed || env.STEPFUN_TTS_SPEED || 1),
      };
    },
  };
}

function textHash(text) {
  return crypto.createHash("sha256").update(normalizeTtsInputText(text)).digest("hex");
}

module.exports = {
  DEFAULT_BASE_URL,
  DEFAULT_FORMAT,
  DEFAULT_MODEL,
  DEFAULT_VOICE,
  apiKeyFromEnv,
  buildSpeechPayload,
  createStepFunTTSService,
  normalizeFormat,
  normalizeTtsInputText,
  splitTextForTts,
  textHash,
};
