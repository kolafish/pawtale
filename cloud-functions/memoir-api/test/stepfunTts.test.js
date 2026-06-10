const assert = require("node:assert/strict");
const test = require("node:test");

const {
  buildSpeechPayload,
  createStepFunTTSService,
  normalizeTtsInputText,
  splitTextForTts,
  textHash,
} = require("../src/tts/stepfun");

test("normalizes StepFun TTS input text so parentheses are spoken", () => {
  assert.equal(normalizeTtsInputText("我（中文）(English)"), "我（中文）（English）");
});

test("splits long TTS text by sentence boundaries", () => {
  const chunks = splitTextForTts("第一句很短。第二句也很短。第三句比较长一点。", 12);

  assert.deepEqual(chunks, [
    "第一句很短。",
    "第二句也很短。",
    "第三句比较长一点。",
  ]);
  assert.ok(chunks.every((chunk) => chunk.length <= 12));
});

test("builds StepFun TTS payload", () => {
  const payload = buildSpeechPayload("测试", {
    model: "stepaudio-2.5-tts",
    voice: "cixingnansheng",
    format: "mp3",
    speed: 1.25,
  });

  assert.deepEqual(payload, {
    model: "stepaudio-2.5-tts",
    input: "测试",
    voice: "cixingnansheng",
    response_format: "mp3",
    speed: 1.25,
    sample_rate: 24000,
  });
});

test("calls StepFun TTS with auth headers and concatenates chunks", async () => {
  const calls = [];
  const service = createStepFunTTSService({
    apiKey: "test-key",
    chunkLimit: 4,
    transport: async (url, headers, payload) => {
      calls.push({ url, headers, payload });
      return {
        audioBytes: Buffer.from(`audio:${payload.input}`),
        contentType: "audio/mpeg",
      };
    },
  });

  const result = await service.synthesize({
    text: "第一句。第二句。",
    model: "stepaudio-2.5-tts",
    voice: "cixingnansheng",
  });

  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, "https://api.stepfun.com/v1/audio/speech");
  assert.equal(calls[0].headers.Authorization, "Bearer test-key");
  assert.equal(calls[0].headers.Accept, "audio/*");
  assert.equal(result.audioBytes.toString(), "audio:第一句。audio:第二句。");
  assert.equal(result.mime_type, "audio/mpeg");
  assert.equal(result.chunk_count, 2);
});

test("hashes normalized TTS text", () => {
  assert.equal(textHash("我(测试)"), textHash("我（测试）"));
});
