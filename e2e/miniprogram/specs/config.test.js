const assert = require("node:assert/strict");
const { test } = require("node:test");
const {
  recentSlotEnvironmentValue,
  urlEnvironmentValue,
} = require("../support/config");

function withEnvironment(name, value, callback) {
  const original = process.env[name];
  if (value === undefined) {
    delete process.env[name];
  } else {
    process.env[name] = value;
  }
  try {
    return callback();
  } finally {
    if (original === undefined) {
      delete process.env[name];
    } else {
      process.env[name] = original;
    }
  }
}

test("iOS recent slot stays within the four visible WeChat entries", () => {
  withEnvironment("PAWTALE_E2E_IOS_RECENT_SLOT", undefined, () => {
    assert.equal(recentSlotEnvironmentValue(), 1);
  });
  withEnvironment("PAWTALE_E2E_IOS_RECENT_SLOT", "4", () => {
    assert.equal(recentSlotEnvironmentValue(), 4);
  });
  withEnvironment("PAWTALE_E2E_IOS_RECENT_SLOT", "5", () => {
    assert.throws(recentSlotEnvironmentValue, /1 到 4/);
  });
});

test("Appium URL settings reject unsafe values", () => {
  withEnvironment("E2E_URL", "http://127.0.0.1:4723/", () => {
    assert.equal(urlEnvironmentValue("E2E_URL", "http://unused"), "http://127.0.0.1:4723");
  });
  withEnvironment("E2E_URL", "file:///tmp/appium", () => {
    assert.throws(
      () => urlEnvironmentValue("E2E_URL", "http://unused"),
      /http 或 https/,
    );
  });
});
