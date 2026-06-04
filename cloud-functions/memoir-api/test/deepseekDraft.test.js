const assert = require("node:assert/strict");
const test = require("node:test");

const {
  buildMemoirDraftPrompt,
  chatCompletionUrl,
  createDeepSeekDraftService,
  parseChatCompletion,
} = require("../src/ai/deepseek");

test("builds a faithful memoir drafting prompt", () => {
  const prompt = buildMemoirDraftPrompt("你小时候最常去哪里？", "嗯，我去外婆家啊。");

  assert.match(prompt, /不扩写、不总结、不文学化/);
  assert.match(prompt, /输出长度应接近用户回答/);
  assert.match(prompt, /你小时候最常去哪里？/);
  assert.match(prompt, /嗯，我去外婆家啊。/);
});

test("normalizes DeepSeek chat completion URL", () => {
  assert.equal(chatCompletionUrl("https://api.deepseek.com"), "https://api.deepseek.com/chat/completions");
  assert.equal(chatCompletionUrl("https://api.deepseek.com/v1"), "https://api.deepseek.com/v1/chat/completions");
});

test("parses DeepSeek chat completion content", () => {
  const result = parseChatCompletion(JSON.stringify({
    choices: [
      {
        message: {
          content: "我去外婆家。",
        },
      },
    ],
    usage: {
      total_tokens: 32,
    },
  }));

  assert.deepEqual(result, {
    text: "我去外婆家。",
    usage: { total_tokens: 32 },
  });
});

test("calls DeepSeek with faithful prompt settings", async () => {
  const service = createDeepSeekDraftService({
    apiKey: "deepseek-key",
    env: {
      DEEPSEEK_MODEL: "deepseek-chat",
    },
    transport: async (url, headers, payload) => {
      assert.equal(url, "https://api.deepseek.com/chat/completions");
      assert.equal(headers.Authorization, "Bearer deepseek-key");
      assert.equal(payload.model, "deepseek-chat");
      assert.equal(payload.temperature, 0.2);
      assert.equal(payload.max_tokens, 1200);
      assert.match(payload.messages[1].content, /不新增事实/);
      return JSON.stringify({
        choices: [{ message: { content: "我去外婆家。" } }],
      });
    },
  });

  const result = await service.generate({
    questionPrompt: "你小时候最常去哪里？",
    answerText: "嗯，我去外婆家啊。",
  });

  assert.equal(result.text, "我去外婆家。");
});
