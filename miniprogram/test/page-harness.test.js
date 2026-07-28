require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  mockModule,
  unmockedAsync,
} = require("./page-harness");

test("page harness tracks globals and module fakes for teardown", () => {
  global.wx = { marker: "test-only" };
  mockModule("../services/time.ts", { marker: "test-only" });

  assert.equal(require("../services/time.ts").marker, "test-only");
});

test("page harness restores globals and module fakes after each test", () => {
  assert.equal(global.wx, undefined);
  assert.equal(require("../services/time.ts").marker, undefined);
});

test("unmocked async methods identify the missing API fake", async () => {
  await assert.rejects(
    unmockedAsync("loadMemoir")(),
    /loadMemoir was not mocked/,
  );
});
