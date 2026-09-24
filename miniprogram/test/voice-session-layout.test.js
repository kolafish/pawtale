const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const template = fs.readFileSync(
  path.join(__dirname, "../components/voice-session/index.wxml"),
  "utf8",
);

test("voice live transcript uses one measured scroll path", () => {
  assert.match(template, /scroll-top="\{\{liveScrollTop\}\}"/);
  assert.match(template, /class="live-text-content"/);
  assert.doesNotMatch(template, /scroll-into-view/);
  assert.doesNotMatch(template, /liveTextEndId/);
});

test("book completion recording removes panels that crowd the transcript", () => {
  assert.match(template, /wx:if="\{\{!recording \|\| sessionGoal !== 'book_completion'\}\}" class="topic-panel"/);
  assert.match(template, /wx:if="\{\{!embedded && !recording && \(progressNoticeTitle/);
  assert.match(template, /wx:if="\{\{!recording\}\}" class="completion-issue-head"/);
  assert.match(template, /wx:if="\{\{showCompletionIssueActions && !recording\}\}"/);
  assert.match(template, /class="completion-issue-question"/);
  assert.match(template, /class="transcript-stage"/);
});

test("voice status pills separate control geometry from label typography", () => {
  for (const [containerClass, labelClass] of [
    ["assistant-label", "assistant-label-text"],
    ["stageStatusClass", "stage-status-text"],
    ["session-state-error", "session-state-error-text"],
    ["line-role", "line-role-text"],
  ]) {
    assert.match(template, new RegExp(`<view[^>]+class="[^\"]*${containerClass}[^\"]*"[^>]*>`));
    assert.match(template, new RegExp(`<text[^>]+class="[^\"]*${labelClass}[^\"]*"[^>]*>`));
  }
});
