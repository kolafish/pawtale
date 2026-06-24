require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  applyAssistantTTSResponse,
  assistantTurnQueueItem,
} = require("../services/voice-assistant-turn-queue.ts");

test("assistant turn queue item preserves response intent metadata", () => {
  const item = assistantTurnQueueItem({
    follow_up_text: "您刚才说的院子，我听到了。",
    primary_intent: "acknowledge",
    intent_tags: ["acknowledge"],
    expects_answer: false,
    message: {
      id: "assistant-1",
      role: "assistant",
      content_text: "您刚才说的院子，我听到了。",
      audio_file_id: "",
      transcript_version_id: "",
      metadata_json: {},
    },
  });

  assert.equal(item.expectsAnswer, false);
  assert.equal(item.autoListenAfterPlayback, false);
  assert.deepEqual(item.line.metadata_json, {
    primary_intent: "acknowledge",
    intent_tags: ["acknowledge"],
    expects_answer: false,
  });
});

test("assistant TTS response keeps existing metadata when backend omits it", () => {
  const { lines, prepared } = applyAssistantTTSResponse([
    {
      id: "assistant-1",
      role: "assistant",
      text: "那时候谁最常陪着您？",
      message_id: "assistant-1",
      metadata_json: {
        primary_intent: "follow_up",
        expects_answer: true,
      },
    },
  ], {
    audio_file_id: "audio-1",
    provider: "fake-tts",
    model: "fake-tts",
    message: {
      id: "assistant-1",
      role: "assistant",
      content_text: "那时候谁最常陪着您？",
      audio_file_id: "audio-1",
      transcript_version_id: "",
      metadata_json: undefined,
    },
  });

  assert.deepEqual(lines[0].metadata_json, {
    primary_intent: "follow_up",
    expects_answer: true,
  });
  assert.deepEqual(prepared.metadata, {
    primary_intent: "follow_up",
    expects_answer: true,
  });
});
