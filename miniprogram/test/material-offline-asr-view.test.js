require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { materialOfflineASRView } = require("../services/material-offline-asr-view.ts");


function state(overrides = {}) {
  return {
    status: "idle",
    job_id: null,
    can_start: true,
    blocked_reason: "",
    providers: ["volc", "stepfun"],
    narrator_origin: "",
    dialect_code: "",
    dialect_label: "",
    dialect_strategy: "none",
    dialect_enhancement_enabled: false,
    last_applied_at: null,
    error_message: "",
    ...overrides,
  };
}

test("material offline ASR view distinguishes dialect enhancement from generic correction", () => {
  const sichuan = materialOfflineASRView(state({
    narrator_origin: "四川成都",
    dialect_code: "zh-CN-sichuanese",
    dialect_label: "四川话",
    dialect_strategy: "sichuanese",
    dialect_enhancement_enabled: true,
  }));
  const generic = materialOfflineASRView(state({ narrator_origin: "浙江宁波" }));

  assert.equal(sichuan.methodText, "火山 + StepFun · 籍贯 四川成都 · 方言 四川话");
  assert.equal(generic.methodText, "火山 + StepFun · 籍贯 浙江宁波 · 通用口音校对");
  assert.equal(sichuan.canStart, true);
  assert.equal(generic.canStart, true);
});

test("material offline ASR view disables duplicate starts while a job is active", () => {
  const view = materialOfflineASRView(state({
    status: "running",
    can_start: false,
    blocked_reason: "离线识别正在处理中",
  }));

  assert.equal(view.active, true);
  assert.equal(view.canStart, false);
  assert.equal(view.statusText, "识别中");
  assert.equal(view.actionText, "识别中");
});
