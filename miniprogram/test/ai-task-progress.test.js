require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { aiTaskProgressPercent } = require("../services/ai-task-progress.ts");

test("AI task progress converts persisted counts into a bounded percentage", () => {
  assert.equal(aiTaskProgressPercent(3, 9), 33);
  assert.equal(aiTaskProgressPercent(12, 9), 100);
  assert.equal(aiTaskProgressPercent(-1, 9), 0);
});

test("AI task progress stays at zero when no real total is available", () => {
  assert.equal(aiTaskProgressPercent(0, 0), 0);
  assert.equal(aiTaskProgressPercent(Number.NaN, 9), 0);
});
