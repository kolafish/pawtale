const assert = require("node:assert/strict");
const test = require("node:test");

const {
  audioFormat,
  buildPayload,
  createStepFunASRService,
  parseSseResponse,
} = require("../src/asr/stepfun");

test("builds StepFun ASR payload for mp3 audio", () => {
  const payload = buildPayload(Buffer.from("audio-bytes"), {
    mime_type: "audio/mpeg",
    cloud_path: "memoir/audio/sample.mp3",
  });

  assert.equal(payload.audio.data, Buffer.from("audio-bytes").toString("base64"));
  assert.equal(payload.audio.input.transcription.model, "stepaudio-2.5-asr");
  assert.deepEqual(payload.audio.input.format, { type: "mp3" });
});

test("infers supported audio format from cloud path", () => {
  assert.deepEqual(audioFormat({
    mime_type: "",
    cloud_path: "memoir/audio/sample.wav",
  }), { type: "wav" });
});

test("parses StepFun ASR SSE done event", () => {
  const result = parseSseResponse([
    'data: {"type":"transcript.text.delta","delta":"我的"}',
    "",
    'data: {"type":"transcript.text.done","text":"我的爸爸","usage":{"total_tokens":8}}',
    "",
  ].join("\n"));

  assert.equal(result.text, "我的爸爸");
  assert.deepEqual(result.usage, { total_tokens: 8 });
});

test("calls StepFun ASR with auth headers", async () => {
  const service = createStepFunASRService({
    apiKey: "test-key",
    transport: async (url, headers, payload) => {
      assert.equal(url, "https://api.stepfun.com/v1/audio/asr/sse");
      assert.equal(headers.Authorization, "Bearer test-key");
      assert.equal(headers.Accept, "text/event-stream");
      assert.equal(payload.audio.input.transcription.language, "zh");
      return 'data: {"type":"transcript.text.done","text":"我爸爸"}\n\n';
    },
  });

  const result = await service.transcribe(Buffer.from("audio"), {
    mime_type: "audio/mpeg",
    cloud_path: "memoir/audio/sample.mp3",
  });

  assert.equal(result.text, "我爸爸");
});
