require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  DEFAULT_ACCESSIBILITY_SETTINGS,
  normalizeAccessibilitySettings,
  readAccessibilitySettings,
  readAutoBargeInEnabled,
  readLargeTextEnabled,
  saveAutoBargeInEnabled,
  saveLargeTextEnabled,
} = require("../services/accessibility-settings.ts");

test("normalizes accessibility settings with safe defaults", () => {
  assert.deepEqual(normalizeAccessibilitySettings("bad"), DEFAULT_ACCESSIBILITY_SETTINGS);
  assert.deepEqual(normalizeAccessibilitySettings({
    largeTextEnabled: true,
    autoBargeInEnabled: false,
  }), {
    largeTextEnabled: true,
    autoBargeInEnabled: false,
  });
  assert.deepEqual(normalizeAccessibilitySettings({
    largeTextEnabled: "yes",
    autoBargeInEnabled: "no",
  }), DEFAULT_ACCESSIBILITY_SETTINGS);
});

test("reads and saves accessibility settings locally", () => {
  let stored = {};
  global.wx = {
    getStorageSync() {
      return stored;
    },
    setStorageSync(_key, value) {
      stored = value;
    },
  };

  assert.deepEqual(readAccessibilitySettings(), DEFAULT_ACCESSIBILITY_SETTINGS);
  assert.equal(readLargeTextEnabled(), false);
  assert.equal(readAutoBargeInEnabled(), true);

  assert.deepEqual(saveLargeTextEnabled(true), {
    largeTextEnabled: true,
    autoBargeInEnabled: true,
  });
  assert.deepEqual(saveAutoBargeInEnabled(false), {
    largeTextEnabled: true,
    autoBargeInEnabled: false,
  });
  assert.deepEqual(stored, {
    largeTextEnabled: true,
    autoBargeInEnabled: false,
  });

  delete global.wx;
});
