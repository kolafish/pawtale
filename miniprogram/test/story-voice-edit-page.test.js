require("./register-ts");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext, flushAsyncWork } = require("./page-harness");

const proposal = {
  signature: "sig",
  reply: "将修改这两处。", questions: ["测试开场白后的童年回忆是否保留？"],
  operations: [
    { action: "delete_chapter", target_ref: "c1", new_title: null, label: "删除测试录音", detail: "保留 1 条讲述" },
    { action: "rename_section", target_ref: "s1", new_title: "外婆家的时光", label: "小节改名", detail: "" },
  ],
};

function setup({ preview, apply } = {}) {
  const calls = { previews: [], applies: [], back: 0 };
  mockModule("../services/accessibility-settings.ts", { accessibilityPageClass: c => `${c} large-text` });
  mockModule("../services/memoir-api-client.ts", { ensureMemoirBackendSession: async () => {}, memoirProjectDictationUrl: id => `wss://test/projects/${id}/dictation` });
  mockModule("../services/memoir-voice-edit.ts", {
    previewVoiceEdit: async (...args) => { calls.previews.push(args); return preview ? preview(...args) : proposal; },
    applyVoiceEdit: async (...args) => { calls.applies.push(args); return apply ? apply(...args) : { message: "修改已保存" }; },
  });
  global.wx = { navigateBack: () => calls.back++ };
  const context = pageContext(loadPageModule("../pages/story-voice-edit/index.ts").page);
  context.onLoad({ projectId: "p1", projectTitle: encodeURIComponent("我的回忆录") });
  return { context, calls };
}

test("story voice edit opts into the shared automatic dictation flow", () => {
  const template = fs.readFileSync(
    path.join(__dirname, "../pages/story-voice-edit/index.wxml"),
    "utf8",
  );
  assert.match(template, /automatic-voice-command="\{\{true\}\}"/);
  assert.match(template, /给童年回忆重新整理小节/);
});

test("raw transcribed request reaches the model; selected existing operations execute only on tap", async () => {
  const { context, calls } = setup();
  const raw = "童年记忆这一张换个名字，那个小杰也换个名字。";
  await context.submitInstruction({ detail: { value: raw } });
  assert.deepEqual(calls.previews, [["p1", raw]]);
  assert.equal(calls.applies.length, 0);
  assert.equal(context.data.instruction, raw);
  assert.match(context.data.gatewayUrl, /p1\/dictation$/);
  assert.equal(context.data.items[0].detail, "保留 1 条讲述");
  assert.equal(context.data.preview.questions.length, 1);
  context.selectOperations({ detail: { value: ["s1"] } });
  await context.apply();
  assert.deepEqual(calls.applies, [["p1", "sig", [{ action: "rename_section", target_ref: "s1", new_title: "外婆家的时光" }]]]);
  assert.equal(context.data.preview, null);
  assert.equal(context.data.message, "修改已保存");
  assert.equal(context.data.instruction, "");
});

test("chapter section regeneration is shown as a confirmable operation", async () => {
  const regeneration = {
    signature: "section-sig",
    reply: "可以根据这一章的讲述重新整理小节。",
    questions: [],
    operations: [{
      action: "regenerate_chapter_sections",
      target_ref: "c1",
      new_title: null,
      label: "重新整理章节「童年回忆」的小节分组",
      detail: "原始讲述和现有正文版本会保留。",
    }],
  };
  const { context, calls } = setup({
    preview: async () => regeneration,
    apply: async () => ({ message: "已开始重新整理小节，可在章节工作台查看进度。" }),
  });
  await context.submitInstruction({
    detail: { value: "看看是不是可以给第一章生成一些小节。" },
  });

  assert.equal(context.data.items[0].label, "重新整理章节「童年回忆」的小节分组");
  assert.equal(context.data.selectedCount, 1);
  await context.apply();
  assert.deepEqual(calls.applies, [["p1", "section-sig", [{
    action: "regenerate_chapter_sections",
    target_ref: "c1",
    new_title: null,
  }]]]);
  assert.equal(context.data.message, "已开始重新整理小节，可在章节工作台查看进度。");
});

test("chapter split keeps its selected utterances when the user confirms", async () => {
  const split = {
    signature: "split-sig", reply: "请确认要拆出的讲述。", questions: [],
    operations: [{
      action: "split_chapter", target_ref: "c3", new_title: "辞职后的日常",
      utterance_ids: ["u1", "u2"], label: "拆出两条讲述", detail: "运动、做饭",
    }],
  };
  const { context, calls } = setup({ preview: async () => split });
  await context.submitInstruction({ detail: { value: "把第三章的日常放到新章" } });
  assert.equal(context.data.items[0].detail, "运动、做饭");
  await context.apply();
  assert.deepEqual(calls.applies, [["p1", "split-sig", [{
    action: "split_chapter", target_ref: "c3", new_title: "辞职后的日常",
    utterance_ids: ["u1", "u2"],
  }]]]);
});

test("a specific clarification stays visible and can be answered with the original request", async () => {
  const { context, calls } = setup({ preview: async () => ({
    signature: "{}", reply: "还需要确定要修改的具体内容。", operations: [],
    questions: ["第三章里，您要拆出运动那条，还是做饭那条讲述？"],
  }) });
  const original = "把第三章的一些日常放到新章节";
  await context.submitInstruction({ detail: { value: original } });
  assert.equal(context.data.error, "");
  assert.equal(context.data.preview.questions.length, 1);
  assert.equal(context.data.selectedCount, 0);
  context.changeInstruction({ detail: { value: `${original}\n运动和做饭两条都要。` } });
  assert.equal(context.data.preview.questions.length, 1);
  await context.submitInstruction();
  assert.deepEqual(calls.previews[1], ["p1", `${original}\n运动和做饭两条都要。`]);
});

test("editing the command discards its previous operations", async () => {
  const { context, calls } = setup();
  context.changeInstruction({ detail: { value: "删掉测试录音" } });
  await context.submitInstruction();
  context.changeInstruction({ detail: { value: "先别删，换个名字" } });
  await context.apply();
  assert.equal(calls.applies.length, 0);
  assert.equal(context.data.preview, null);
});

test("hiding the page discards a late model result", async () => {
  let finish;
  const { context } = setup({ preview: () => new Promise(resolve => { finish = resolve; }) });
  context.changeInstruction({ detail: { value: "改名字" } });
  const request = context.submitInstruction();
  await flushAsyncWork();
  context.onHide();
  finish(proposal);
  await request;
  assert.equal(context.data.preview, null);
  assert.equal(context.data.loading, false);
});

test("double tap submits once and an unknown result cannot replay the old deletion", async () => {
  let fail;
  const { context, calls } = setup({ apply: () => new Promise((_, reject) => { fail = reject; }) });
  context.changeInstruction({ detail: { value: "删掉测试录音" } });
  await context.submitInstruction();
  const pending = context.apply();
  await context.apply();
  assert.equal(calls.applies.length, 1);
  fail(new Error("连接中断"));
  await pending;
  await context.apply();
  assert.equal(calls.applies.length, 1);
  assert.equal(context.data.preview, null);
  assert.match(context.data.error, /查看当前内容/);
});

test("a clarification without operations never enables execution", async () => {
  const { context, calls } = setup({ preview: async () => ({ ...proposal, operations: [] }) });
  context.changeInstruction({ detail: { value: "那一章" } });
  await context.submitInstruction();
  await context.apply();
  assert.equal(context.data.selectedCount, 0);
  assert.equal(calls.applies.length, 0);
});

test("voice edit gives the model a generation deadline, uses mutation writes and the shared preference", async () => {
  const calls = [];
  mockModule("../services/memoir-api-client.ts", {
    memoirAIRequest: async (...args) => { calls.push(["ai", ...args]); },
    memoirRequest: async (...args) => { calls.push(["write", ...args]); },
    memoirNarrativeProjectPath: (id, suffix) => `/api/memoir/projects/${id}${suffix}`,
  });
  const path = require.resolve("../services/memoir-voice-edit.ts");
  delete require.cache[path];
  const api = require(path);
  await api.previewVoiceEdit("p1", "原话");
  await api.applyVoiceEdit("p1", "sig", []);
  assert.equal(calls[0][0], "ai");
  assert.equal(calls[0][2].supports_chapter_split, true);
  assert.equal(calls[0][3].timeoutProfile, "generation");
  assert.equal(calls[0][3].invalidatesSnapshots, false);
  assert.equal(calls[1][2].timeoutProfile, "mutation");
});
