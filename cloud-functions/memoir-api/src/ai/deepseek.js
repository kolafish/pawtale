const https = require("node:https");

const { ApiError } = require("../http");

const DEFAULT_BASE_URL = "https://api.deepseek.com";
const DEFAULT_MODEL = "deepseek-chat";
const SYSTEM_PROMPT = "你是一位谨慎的中文口述稿校对助手，只做忠于原话的转写校正和口头语清理。";

function apiKeyFromEnv(env = process.env) {
  return env.DEEPSEEK_API_KEY || "";
}

function chatCompletionUrl(baseUrl) {
  const cleanBaseUrl = String(baseUrl || DEFAULT_BASE_URL).replace(/\/$/, "");
  return cleanBaseUrl.endsWith("/chat/completions")
    ? cleanBaseUrl
    : `${cleanBaseUrl}/chat/completions`;
}

function buildMemoirDraftPrompt(questionPrompt, answerText) {
  return `请根据访谈问题上下文，对用户回答做一次忠于原文的口述稿校正。

要求：
- 只修正 ASR 可能因方言、口音、同音词造成的明显错字或错词；无法确定时保留原文
- 去掉明显的口头填充词，比如“嗯”“啊”“呃”“那个”“就是”等不承载信息的重复语气词
- 可以整理基础标点和极少量语序，使句子可读
- 不新增事实、人物、时间、地点、事件或情绪
- 不扩写、不总结、不文学化、不替用户补充细节
- 输出长度应接近用户回答；短回答保持短
- 直接输出清理后的第一人称回答正文，不要标题、列表、解释或引号

访谈问题（仅用于判断转写纠错上下文，不用于扩写）：
${questionPrompt}

用户回答：
${answerText}
`;
}

function postJson(url, headers, payload) {
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
          reject(new ApiError(503, "AI 服务暂时不可用，请稍后重试。", {
            upstream_status: response.statusCode,
          }));
          return;
        }
        resolve(responseText);
      });
    });

    request.on("error", () => {
      reject(new ApiError(503, "AI 服务暂时不可用，请稍后重试。"));
    });
    request.write(body);
    request.end();
  });
}

function parseChatCompletion(text) {
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new ApiError(502, "AI未返回可用整理稿");
  }

  const content = payload.choices?.[0]?.message?.content;
  const draftText = String(content || "").trim();
  if (!draftText) {
    throw new ApiError(502, "AI未返回可用整理稿");
  }
  return {
    text: draftText,
    usage: payload.usage || null,
  };
}

function createDeepSeekDraftService(options = {}) {
  return {
    async generate({ questionPrompt, answerText }) {
      const env = options.env || process.env;
      const apiKey = options.apiKey || apiKeyFromEnv(env);
      if (!apiKey) {
        throw new ApiError(500, "缺少 DeepSeek API Key");
      }

      const model = options.model || env.DEEPSEEK_MODEL || DEFAULT_MODEL;
      const payload = {
        model,
        messages: [
          { role: "system", content: SYSTEM_PROMPT },
          { role: "user", content: buildMemoirDraftPrompt(questionPrompt, answerText) },
        ],
        temperature: 0.2,
        max_tokens: 1200,
        stream: false,
      };
      const transport = options.transport || postJson;
      const responseText = await transport(chatCompletionUrl(options.baseUrl || env.DEEPSEEK_BASE_URL), {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      }, payload);
      return parseChatCompletion(responseText);
    },
  };
}

module.exports = {
  buildMemoirDraftPrompt,
  chatCompletionUrl,
  createDeepSeekDraftService,
  parseChatCompletion,
};
