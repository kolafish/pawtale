require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { loadPageModule, pageContext, mockModule } = require("./page-harness");

test("completed supplement returns immediately to its caller with saved feedback", () => {
  const modals = [], returns = [], toasts = [];
  global.wx = {
    showModal: modal => modals.push(modal),
    showToast: toast => toasts.push(toast),
    navigateBack: options => returns.push(options),
  };
  const context = pageContext(loadPageModule("../pages/voice-chat/index.ts").page);
  const entry = { voiceSessionId: "voice-1", sessionGoal: "book_completion" };
  context.onLoad(entry);
  assert.equal(context.data.entry.voiceSessionId, entry.voiceSessionId);
  assert.equal(context.data.entry.sessionGoal, entry.sessionGoal);
  context.exitSession({ detail: { destination: "completion" } });
  assert.deepEqual(returns, [{ delta: 1 }]);
  assert.deepEqual(modals, [], "a saved answer must not require another confirmation or manual rewrite");
  assert.equal(toasts.length, 1);
});

test("voice page returns a confirmed ordinary narration to the home story", () => {
  const routes = [];
  global.wx = { reLaunch: options => routes.push(options.url) };
  const context = pageContext(loadPageModule("../pages/voice-chat/index.ts").page);
  context.exitSession({ detail: { destination: "story", projectId: "project-1", narrationSaved: true } });
  assert.deepEqual(routes, ["/pages/home/index"]);
  assert.deepEqual(require("../services/navigation.ts").consumeHomeStoryTarget(), { projectId: "project-1", narrationSaved: true });
});

test("failed supplement handoff leaves the stopped session and returns to a usable home", async () => {
  const routes = [], toasts = [];
  global.wx = { reLaunch: options => routes.push(options.url), showToast: options => toasts.push(options.title) };
  mockModule("../services/memoir-completion-interview.ts", { openOfferedCompletionInterview: async () => { throw new Error("补充访谈暂时不可用"); } });
  const context = pageContext(loadPageModule("../pages/voice-chat/index.ts").page);
  context.onLoad({ voiceSessionId: "stopped-session" });
  await context.exitSession({ detail: { destination: "completion_issue", projectId: "project-1", issueId: "issue-1", projectTitle: "回忆录" } });
  assert.equal(context.data.entry, null);
  assert.deepEqual(routes, ["/pages/home/index"]);
  assert.deepEqual(require("../services/navigation.ts").consumeHomeStoryTarget(), { projectId: "project-1", narrationSaved: false });
  assert.deepEqual(toasts, ["补充访谈暂时不可用"]);
});
