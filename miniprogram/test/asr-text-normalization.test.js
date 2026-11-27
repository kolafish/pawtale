require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const { ASR_CORRECTION_RULES_CONFIG } = require("../services/asr-correction-rules-runtime.ts");
const { normalizeMemoirAsrText } = require("../services/asr-text-normalization.ts");

test("ASR correction rules stay in sync across backend and miniprogram", () => {
  const backendRules = require("../../backend/app/services/memoir/asr_correction_rules.json");
  const miniprogramRules = require("../services/asr-correction-rules.json");
  assert.deepEqual(
    miniprogramRules,
    backendRules,
  );
  assert.deepEqual(
    ASR_CORRECTION_RULES_CONFIG,
    miniprogramRules,
  );
});

test("ASR correction runtime avoids mini program JSON module loading", () => {
  const servicePath = path.join(__dirname, "../services/asr-text-normalization.ts");
  const serviceSource = fs.readFileSync(servicePath, "utf8");
  assert.equal(serviceSource.includes("asr-correction-rules.json"), false);
});

test("normalizeMemoirAsrText corrects chapter homophones in book context", () => {
  assert.equal(normalizeMemoirAsrText("现在有几个张杰啊？"), "现在有几个章节啊？");
  assert.equal(normalizeMemoirAsrText("这条还没收入张杰。"), "这条还没收入章节。");
  assert.equal(
    normalizeMemoirAsrText("你不是一个人名的张杰，是书的张杰。"),
    "你不是一个人名的章节，是书的章节。",
  );
});

test("normalizeMemoirAsrText preserves classifiers in narration", () => {
  for (const text of [
    "宿舍第一张床靠走道。",
    "我顺着自己的印象另画一张，把学校放到染坊旁边。",
    "摆着几张白椅，背后能看见船。",
    "桌上只留了几张纸。",
    "现在有几张啊？",
    "相册里有多少张照片？",
    "第一张和第二张照片都留着。",
  ]) {
    assert.equal(normalizeMemoirAsrText(text), text);
  }
});

test("normalizeMemoirAsrText preserves likely person names", () => {
  assert.equal(
    normalizeMemoirAsrText("张杰这个人，在书里单独占了一章。"),
    "张杰这个人，在书里单独占了一章。",
  );
});
