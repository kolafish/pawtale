require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { loadPageModule, pageContext } = require("./page-harness");

test("voice page forwards entry and returns a completed supplement to its chapter", () => {
  const modals = [], returns = [];
  global.wx = { showModal: modal => modals.push(modal), navigateBack: options => returns.push(options) };
  const context = pageContext(loadPageModule("../pages/voice-chat/index.ts").page);
  const entry = { voiceSessionId: "voice-1", sessionGoal: "book_completion" };
  context.onLoad(entry);
  assert.equal(context.data.entry.voiceSessionId, entry.voiceSessionId);
  assert.equal(context.data.entry.sessionGoal, entry.sessionGoal);
  context.exitSession({ detail: { destination: "completion", explainSaved: true } });
  assert.equal(modals[0].title, "本次补充已保存");
  assert.equal(modals[0].confirmText, "返回本章");
  assert.deepEqual(returns, []);
  modals[0].success();
  assert.deepEqual(returns, [{ delta: 1 }]);
});

test("voice page returns a confirmed ordinary narration to the home story", () => {
  const routes = [];
  global.wx = { reLaunch: options => routes.push(options.url) };
  const context = pageContext(loadPageModule("../pages/voice-chat/index.ts").page);
  context.exitSession({ detail: { destination: "story", projectId: "project-1", narrationSaved: true } });
  assert.deepEqual(routes, ["/pages/home/index"]);
  assert.deepEqual(require("../services/navigation.ts").consumeHomeStoryTarget(), { projectId: "project-1", narrationSaved: true });
});
