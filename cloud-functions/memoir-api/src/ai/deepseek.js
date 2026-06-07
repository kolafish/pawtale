const https = require("node:https");

const { ApiError } = require("../http");

const DEFAULT_BASE_URL = "https://api.deepseek.com";
const DEFAULT_MODEL = "deepseek-chat";
const SYSTEM_PROMPT = "你是一位谨慎的中文口述稿校对助手，只做忠于原话的转写校正和口头语清理。";
const MEMORY_SYSTEM_PROMPT = "你是一位谨慎的中文人生访谈记忆整理员，只根据给定问答提取事实和待确认问题。";

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

function buildMemoirMemoryPrompt(session, turns) {
  const qaText = turns.map((turn, index) => (
    `Q${index + 1}: ${turn.question}\n`
    + `A${index + 1}: ${turn.answer_text}\n`
    + `question_id: ${turn.question_id}\n`
    + `answer_id: ${turn.answer_id}`
  )).join("\n\n");

  return `请从这次回忆录访谈中提取长期结构化记忆。只允许使用问答里出现的信息，不要编造。

访谈标题：${session.title}

请输出严格 JSON，不要 Markdown，不要解释。格式：
{
  "people": [{"canonical_name": "人物名", "aliases": ["别名"], "relationship": "关系", "description": "说明", "confidence": 0.0, "evidence_quote": "原文证据", "answer_id": "来源回答 id"}],
  "events": [{"title": "事件标题", "life_stage": "童年/青年/成年/晚年等", "time_text": "原文时间", "place_text": "原文地点", "cause": "起因", "process": "经过", "result": "结果", "emotion": "情绪", "importance": 0.0, "confidence": 0.0, "evidence_quote": "原文证据", "answer_id": "来源回答 id"}],
  "places": [{"name": "地点名", "elder_alias": "受访者说法", "modern_name": "可确定的现代地名，否则为空", "place_type": "家乡/学校/工作地点等", "description": "地点说明", "confidence": 0.0, "evidence_quote": "原文证据", "answer_id": "来源回答 id"}],
  "themes": [{"name": "反复主题", "description": "主题说明", "strength": 0.0, "mention_count": 1, "confidence": 0.0, "evidence_quote": "原文证据", "answer_id": "来源回答 id"}],
  "open_questions": [{"question": "下次需要确认或追问的问题", "question_type": "clarification/depth/scene/person/emotion/connection/closure", "reason": "为什么要问", "sensitivity": "low/medium/high", "priority": 0.0, "target_memory_type": "person/event/place/theme", "target_title": "刚提取出的目标记忆标题或名称", "evidence_quote": "触发该问题的原文证据", "answer_id": "来源回答 id"}],
  "session_summary": "本次访谈摘要",
  "story_fragment": "可直接回读给家人看的故事片段草稿",
  "next_question_suggestions": []
}

要求：
- 不确定的信息必须降低 confidence，并优先生成 open_questions。
- evidence_quote 必须来自回答原文。
- 没有内容的数组返回空数组。
- 故事片段可以润色，但不得新增事实。

访谈问答：
${qaText}
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

function extractJsonObject(content) {
  let value = String(content || "").trim();
  const fenced = /```(?:json)?\s*(\{[\s\S]*?\})\s*```/.exec(value);
  if (fenced) {
    value = fenced[1];
  } else if (value.includes("{") && value.includes("}")) {
    value = value.slice(value.indexOf("{"), value.lastIndexOf("}") + 1);
  }

  try {
    const payload = JSON.parse(value);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("invalid payload");
    }
    return payload;
  } catch {
    throw new ApiError(502, "AI未返回有效的结构化记忆JSON");
  }
}

function parseMemoryExtraction(text) {
  let payload;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new ApiError(502, "AI未返回有效的结构化记忆JSON");
  }

  const content = payload.choices?.[0]?.message?.content;
  return {
    payload: extractJsonObject(content),
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

function createDeepSeekMemoryExtractionService(options = {}) {
  return {
    async extract({ session, turns }) {
      const env = options.env || process.env;
      const apiKey = options.apiKey || apiKeyFromEnv(env);
      if (!apiKey) {
        throw new ApiError(500, "缺少 DeepSeek API Key");
      }

      const model = options.model || env.DEEPSEEK_MODEL || DEFAULT_MODEL;
      const prompt = buildMemoirMemoryPrompt(session, turns);
      const payload = {
        model,
        messages: [
          { role: "system", content: MEMORY_SYSTEM_PROMPT },
          { role: "user", content: prompt },
        ],
        temperature: 0.2,
        max_tokens: 5000,
        stream: false,
      };
      const transport = options.transport || postJson;
      const responseText = await transport(chatCompletionUrl(options.baseUrl || env.DEEPSEEK_BASE_URL), {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      }, payload);
      const result = parseMemoryExtraction(responseText);
      return {
        ...result,
        model,
        prompt,
      };
    },
  };
}

module.exports = {
  buildMemoirDraftPrompt,
  buildMemoirMemoryPrompt,
  chatCompletionUrl,
  createDeepSeekDraftService,
  createDeepSeekMemoryExtractionService,
  extractJsonObject,
  parseChatCompletion,
  parseMemoryExtraction,
};
