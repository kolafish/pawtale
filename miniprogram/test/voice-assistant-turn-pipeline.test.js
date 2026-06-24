require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { clearModule, mockModule } = require("./page-harness");

function response(id, text, expectsAnswer, primaryIntent = expectsAnswer ? "follow_up" : "acknowledge") {
  return {
    follow_up_text: text,
    primary_intent: primaryIntent,
    intent_tags: [primaryIntent],
    expects_answer: expectsAnswer,
    message: {
      id,
      role: "assistant",
      content_text: text,
      audio_file_id: "",
      transcript_version_id: "",
      metadata_json: {
        primary_intent: primaryIntent,
        expects_answer: expectsAnswer,
      },
    },
  };
}

test("assistant turn pipeline emits acknowledgement before question", async () => {
  const requests = [];
  mockModule("../services/memoir-voice-api.ts", {
    createMemoirVoiceAcknowledgement: async (voiceSessionId, data) => {
      requests.push({ type: "acknowledgement", voiceSessionId, data });
      return response("ack-1", "这段院子的记忆很具体，也很有生活气。", false);
    },
    createMemoirVoiceFollowUp: async (voiceSessionId, data) => {
      requests.push({ type: "follow_up", voiceSessionId, data });
      return response("question-1", "那时候在院子里，谁最常陪着您？", true);
    },
  });
  clearModule("../services/voice-assistant-turn-queue.ts");
  clearModule("../services/voice-chat-state.ts");
  clearModule("../services/voice-assistant-turn-pipeline.ts");
  const { runAcknowledgedQuestionPipeline } = require("../services/voice-assistant-turn-pipeline.ts");
  const steps = [];

  const result = await runAcknowledgedQuestionPipeline({
    voiceSessionId: "voice-1",
    onStep: (step) => steps.push(step),
  });

  assert.equal(result.cancelled, false);
  assert.equal(result.acknowledgement.message.id, "ack-1");
  assert.equal(result.followUp.message.id, "question-1");
  assert.deepEqual(requests, [
    { type: "acknowledgement", voiceSessionId: "voice-1", data: {} },
    { type: "follow_up", voiceSessionId: "voice-1", data: { acknowledgement_message_id: "ack-1" } },
  ]);
  assert.deepEqual(steps.map((step) => ({
    stage: step.stage,
    messageId: step.item.messageId,
    autoListenAfterPlayback: step.item.autoListenAfterPlayback,
    expectsAnswer: step.item.expectsAnswer,
  })), [
    {
      stage: "acknowledgement",
      messageId: "ack-1",
      autoListenAfterPlayback: false,
      expectsAnswer: false,
    },
    {
      stage: "question",
      messageId: "question-1",
      autoListenAfterPlayback: true,
      expectsAnswer: true,
    },
  ]);
});

test("assistant turn pipeline stops before question when cancelled after acknowledgement", async () => {
  const requests = [];
  mockModule("../services/memoir-voice-api.ts", {
    createMemoirVoiceAcknowledgement: async (voiceSessionId, data) => {
      requests.push({ type: "acknowledgement", voiceSessionId, data });
      return response("ack-1", "您刚才说的那一幕，我听得很清楚。", false);
    },
    createMemoirVoiceFollowUp: async (voiceSessionId, data) => {
      requests.push({ type: "follow_up", voiceSessionId, data });
      return response("question-1", "那后来呢？", true);
    },
  });
  clearModule("../services/voice-assistant-turn-queue.ts");
  clearModule("../services/voice-chat-state.ts");
  clearModule("../services/voice-assistant-turn-pipeline.ts");
  const { runAcknowledgedQuestionPipeline } = require("../services/voice-assistant-turn-pipeline.ts");
  const steps = [];

  const result = await runAcknowledgedQuestionPipeline({
    voiceSessionId: "voice-1",
    isCancelled: () => true,
    onStep: (step) => steps.push(step),
  });

  assert.equal(result.cancelled, true);
  assert.deepEqual(steps, []);
  assert.deepEqual(requests, [
    { type: "acknowledgement", voiceSessionId: "voice-1", data: {} },
  ]);
});
