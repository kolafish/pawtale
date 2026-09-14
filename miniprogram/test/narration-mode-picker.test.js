require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { componentContext, loadComponentModule } = require("./page-harness");
const { narrationModeForSession, NARRATION_MODE_OPTIONS } = require("../domain/memoir-narration-mode.ts");
const { initialVoiceChatData, voiceChatViewPatch } = require("../components/voice-session/voice-chat-page-state.ts");
const modes = ["free_narration", "guided_interview", "book_completion", "human_interview"];

function fields(mode) {
  return { guidanceMode: mode === "guided_interview" || mode === "book_completion" ? "guided_interview" : "free_narration",
    interviewType: mode === "human_interview" ? "human_interview" : "ai_interview",
    sessionGoal: mode === "book_completion" ? "book_completion" : "open_narration" };
}

test("all four choices remain available regardless of selection, with distinct explanations", () => {
  const component = loadComponentModule("../components/narration-mode-picker/index.ts").component;
  for (const mode of modes) {
    const context = componentContext(component, { selected: mode });
    assert.deepEqual(context.data.options.map(option => option.value), modes);
    assert.ok(context.data.options.every(option => option.label && option.description && option.action));
    for (const value of modes) context.choose({ currentTarget: { dataset: { mode: value } } });
    assert.deepEqual(context.events.map(event => event.detail.value), modes.filter(value => value !== mode));
    assert.equal(context.properties.selected, mode, "only the host commits a selection");
  }
  assert.equal(new Set(NARRATION_MODE_OPTIONS.map(option => option.description)).size, 4);
});

test("recording and preparing retain the choices without changing an active session", () => {
  const component = loadComponentModule("../components/narration-mode-picker/index.ts").component;
  for (const props of [{ locked: true }, { disabled: true }]) {
    const context = componentContext(component, { selected: "human_interview", ...props });
    context.choose({ currentTarget: { dataset: { mode: "guided_interview" } } });
    assert.deepEqual(context.events, []);
    assert.equal(context.data.options.length, 4);
  }
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
