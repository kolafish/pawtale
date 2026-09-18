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

test("voice permission recovery exposes the microphone settings action", () => {
  assert.match(template, /wx:if="\{\{recorderSettingsRequired\}\}"/);
  assert.match(template, /bindtap="openRecorderSettings"/);
  assert.match(template, />去设置麦克风<\/button>/);
});
