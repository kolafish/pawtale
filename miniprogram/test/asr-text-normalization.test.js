require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const { normalizeMemoirAsrText } = require("../services/asr-text-normalization.ts");

test("normalizeMemoirAsrText corrects chapter homophones in book context", () => {
  assert.equal(normalizeMemoirAsrText("现在有几个张杰啊？"), "现在有几个章节啊？");
  assert.equal(normalizeMemoirAsrText("这条还没收入张杰。"), "这条还没收入章节。");
  assert.equal(
    normalizeMemoirAsrText("你不是一个人名的张杰，是书的张杰。"),
    "你不是一个人名的章节，是书的章节。",
  );
  assert.equal(normalizeMemoirAsrText("把第一张改名为童年往事。"), "把第一章改名为童年往事。");
});

test("normalizeMemoirAsrText preserves likely person names", () => {
  assert.equal(
    normalizeMemoirAsrText("张杰这个人，在书里单独占了一章。"),
    "张杰这个人，在书里单独占了一章。",
  );
});
