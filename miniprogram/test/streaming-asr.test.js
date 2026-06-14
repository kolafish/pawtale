require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function streamingAsrModule() {
  global.wx = {
    getRecorderManager() {
      return {
        start() {},
        stop() {},
        onFrameRecorded() {},
        onError() {},
        onStop() {},
      };
    },
  };
  delete require.cache[require.resolve("../services/memoir-streaming-asr.ts")];
  return require("../services/memoir-streaming-asr.ts");
}

test("encodes ArrayBuffer audio frames as base64", () => {
  const { arrayBufferToBase64 } = streamingAsrModule();

  assert.equal(arrayBufferToBase64(Uint8Array.from([0, 1, 2, 253, 254, 255]).buffer), "AAEC/f7/");
  assert.equal(arrayBufferToBase64(Uint8Array.from([255]).buffer), "/w==");
  assert.equal(arrayBufferToBase64(Uint8Array.from([255, 238]).buffer), "/+4=");

  delete global.wx;
});

test("normalizes invalid gateway payloads into session errors", () => {
  const { normalizeVoiceGatewayEvent } = streamingAsrModule();

  assert.deepEqual(normalizeVoiceGatewayEvent("bad"), {
    type: "session.error",
    error: "invalid_gateway_event",
  });
  assert.deepEqual(normalizeVoiceGatewayEvent({ text: "missing type" }), {
    type: "session.error",
    text: "missing type",
  });

  delete global.wx;
});

test("normalizes set-cookie values before reusing them as Cookie headers", () => {
  const { cookieHeaderFromSetCookie } = require("../services/memoir-voice-api.ts");

  assert.equal(
    cookieHeaderFromSetCookie(
      ["pawtale_session=abc; Path=/; HttpOnly; SameSite=lax"],
      { "set-cookie": "another=value; Path=/" },
    ),
    "pawtale_session=abc; another=value",
  );
});
