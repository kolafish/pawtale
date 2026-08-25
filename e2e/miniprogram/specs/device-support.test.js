const assert = require("node:assert/strict");
const { test } = require("node:test");
const {
  assertIOSPlatform,
  connectIOSDevice,
} = require("../support/ios-device");

test("iOS platform guard rejects simulator and Android runtimes", () => {
  assert.doesNotThrow(() => assertIOSPlatform({ platform: "ios" }));
  assert.throws(
    () => assertIOSPlatform({ platform: "devtools" }),
    /实际 platform=devtools/,
  );
  assert.throws(
    () => assertIOSPlatform({ platform: "android" }),
    /实际 platform=android/,
  );
});

test("device connector pairs before preparing isolated iOS storage", async () => {
  const calls = [];
  const stages = [];
  const session = {
    artifacts: { mark: (stage) => stages.push(stage) },
    config: { iosAutoConnect: true, iosConnectTimeoutMs: 1000 },
    miniProgram: {
      async remote(auto) {
        calls.push(["remote", auto]);
      },
      async systemInfo() {
        calls.push(["systemInfo"]);
        return { platform: "ios", SDKVersion: "3.9.0" };
      },
    },
  };
  const systemInfo = await connectIOSDevice(session, {
    async prepareRuntime(receivedSession) {
      assert.equal(receivedSession, session);
      calls.push(["prepareRuntime"]);
    },
    writeLine() {},
  });

  assert.equal(systemInfo.platform, "ios");
  assert.deepEqual(calls, [
    ["remote", true],
    ["systemInfo"],
    ["prepareRuntime"],
  ]);
  assert.deepEqual(stages, [
    "ios:pairing:start",
    "ios:pairing:connected",
    "ios:platform:verified",
    "ios:runtime:prepared",
  ]);
});

test("device connector reports pairing timeout without simulator fallback", async () => {
  let prepared = false;
  const session = {
    artifacts: { mark() {} },
    config: { iosAutoConnect: false, iosConnectTimeoutMs: 10 },
    miniProgram: {
      remote() {
        return new Promise(() => undefined);
      },
    },
  };

  await assert.rejects(
    connectIOSDevice(session, {
      async prepareRuntime() {
        prepared = true;
      },
      writeLine() {},
    }),
    /请确认 iPhone 已登录有调试权限的微信/,
  );
  assert.equal(prepared, false);
});
