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

function turnPlan({
  acknowledgement = response("ack-1", "这段院子的记忆很具体，也很有生活气。", false),
  shouldPlayAck = true,
} = {}) {
  return {
    plan: {
      user_signal: "memory_detail",
      response_strategy: shouldPlayAck ? "ack_then_follow" : "direct_follow",
      should_play_ack: shouldPlayAck,
      acknowledgement_text: shouldPlayAck ? acknowledgement.follow_up_text : "",
      next_question_brief: "追问院子里谁最常陪着叙述者",
    },
    acknowledgement: shouldPlayAck ? acknowledgement : null,
  };
}

test("assistant turn pipeline emits planned acknowledgement before question", async () => {
  const requests = [];
  mockModule("../services/memoir-voice-api.ts", {
    createMemoirVoiceTurnPlan: async (voiceSessionId, data) => {
      requests.push({ type: "turn_plan", voiceSessionId, data });
      return turnPlan();
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
    { type: "turn_plan", voiceSessionId: "voice-1", data: {} },
    {
      type: "follow_up",
      voiceSessionId: "voice-1",
      data: {
        acknowledgement_message_id: "ack-1",
        turn_plan: {
          user_signal: "memory_detail",
          response_strategy: "ack_then_follow",
          should_play_ack: true,
          acknowledgement_text: "这段院子的记忆很具体，也很有生活气。",
          next_question_brief: "追问院子里谁最常陪着叙述者",
        },
      },
    },
  ]);
  assert.deepEqual(steps.map((step) => ({
    stage: step.stage,
    messageId: step.item.messageId,
    autoListenAfterPlayback: step.item.autoListenAfterPlayback,
    expectsAnswer: step.item.expectsAnswer,
    waitForNextTurnBeforePlaybackMs: step.item.waitForNextTurnBeforePlaybackMs,
  })), [
    {
      stage: "acknowledgement",
      messageId: "ack-1",
      autoListenAfterPlayback: false,
      expectsAnswer: false,
      waitForNextTurnBeforePlaybackMs: 900,
    },
    {
      stage: "question",
      messageId: "question-1",
      autoListenAfterPlayback: true,
      expectsAnswer: true,
      waitForNextTurnBeforePlaybackMs: 0,
    },
  ]);
});

test("assistant turn pipeline skips acknowledgement when plan says direct follow", async () => {
  const requests = [];
  mockModule("../services/memoir-voice-api.ts", {
    createMemoirVoiceTurnPlan: async (voiceSessionId, data) => {
      requests.push({ type: "turn_plan", voiceSessionId, data });
      return {
        ...turnPlan({ shouldPlayAck: false }),
        acknowledgement: response("unexpected-ack", "这句不应该被播放。", false),
      };
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
  assert.equal(result.acknowledgement, undefined);
  assert.deepEqual(requests, [
    { type: "turn_plan", voiceSessionId: "voice-1", data: {} },
    {
      type: "follow_up",
      voiceSessionId: "voice-1",
      data: {
        turn_plan: {
          user_signal: "memory_detail",
          response_strategy: "direct_follow",
          should_play_ack: false,
          acknowledgement_text: "",
          next_question_brief: "追问院子里谁最常陪着叙述者",
        },
      },
    },
  ]);
  assert.deepEqual(steps.map((step) => ({
    stage: step.stage,
    messageId: step.item.messageId,
  })), [
    { stage: "question", messageId: "question-1" },
  ]);
});

test("assistant turn pipeline stops before question when cancelled after turn plan", async () => {
  const requests = [];
  mockModule("../services/memoir-voice-api.ts", {
    createMemoirVoiceTurnPlan: async (voiceSessionId, data) => {
      requests.push({ type: "turn_plan", voiceSessionId, data });
      return turnPlan();
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
    { type: "turn_plan", voiceSessionId: "voice-1", data: {} },
  ]);
});
