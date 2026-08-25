const assert = require("node:assert/strict");
const { test } = require("node:test");
const {
  booleanEnvironmentValue,
  durationEnvironmentValue,
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

test("iOS connection boolean accepts explicit values and rejects ambiguity", () => {
  withEnvironment("E2E_BOOLEAN", "yes", () => {
    assert.equal(booleanEnvironmentValue("E2E_BOOLEAN"), true);
  });
  withEnvironment("E2E_BOOLEAN", "0", () => {
    assert.equal(booleanEnvironmentValue("E2E_BOOLEAN", true), false);
  });
  withEnvironment("E2E_BOOLEAN", "sometimes", () => {
    assert.throws(
      () => booleanEnvironmentValue("E2E_BOOLEAN"),
      /必须是 1\/0、true\/false 或 yes\/no/,
    );
  });
});

test("iOS connection timeout stays within the interactive pairing window", () => {
  withEnvironment("E2E_DURATION", undefined, () => {
    assert.equal(durationEnvironmentValue("E2E_DURATION", 180000), 180000);
  });
  withEnvironment("E2E_DURATION", "90000", () => {
    assert.equal(durationEnvironmentValue("E2E_DURATION", 180000), 90000);
  });
  withEnvironment("E2E_DURATION", "999", () => {
    assert.throws(
      () => durationEnvironmentValue("E2E_DURATION", 180000),
      /1000 到 600000/,
    );
  });
});
