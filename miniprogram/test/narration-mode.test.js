require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const {
  DEFAULT_NARRATION_MODE_OPTIONS,
  narrationModeForSession,
  NARRATION_MODE_OPTIONS,
} = require("../domain/memoir-narration-mode.ts");
const { initialVoiceChatData, voiceChatViewPatch } = require("../components/voice-session/voice-chat-page-state.ts");
const modes = ["free_narration", "guided_interview", "book_completion", "human_interview"];

function fields(mode) {
  return { guidanceMode: mode === "guided_interview" || mode === "book_completion" ? "guided_interview" : "free_narration",
    interviewType: mode === "human_interview" ? "human_interview" : "ai_interview",
    sessionGoal: mode === "book_completion" ? "book_completion" : "open_narration" };
}

test("project defaults preserve all four interview modes", () => {
  assert.deepEqual(DEFAULT_NARRATION_MODE_OPTIONS.map(option => option.value), [
    "free_narration",
    "guided_interview",
    "book_completion",
    "human_interview",
  ]);
  assert.ok(DEFAULT_NARRATION_MODE_OPTIONS.every(option => option.label && option.description));
  assert.equal(new Set(NARRATION_MODE_OPTIONS.map(option => option.description)).size, 4);
});

test("actual session fields determine selection through ready, recording, playback and saved states", () => {
  for (const mode of modes) {
    for (const patch of [{}, { recording: true, liveText: "那一年" }, { followUpTTSPlaying: true }, { status: "closed" }, { largeTextEnabled: true }]) {
      const view = voiceChatViewPatch({ ...initialVoiceChatData(), ...fields(mode), ...patch });
      assert.equal(view.narrationMode, mode);
      assert.equal(view.topModeLabel, NARRATION_MODE_OPTIONS.find(option => option.value === mode).label);
    }
  }
  assert.equal(narrationModeForSession({ guidance_mode: "guided_interview", interview_type: "human_interview", session_goal: "book_completion" }), "book_completion");
});
