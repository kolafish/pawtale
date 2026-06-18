require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadVoiceAuthorizationService() {
  const servicePath = require.resolve("../services/voice-authorization.ts");
  delete require.cache[servicePath];
  return require(servicePath);
}

test("voice authorization consent is false without mini program storage", () => {
  const previousWx = global.wx;
  delete global.wx;
  const service = loadVoiceAuthorizationService();

  assert.equal(service.readVoiceAuthorizationConsent(), false);

  if (previousWx === undefined) {
    delete global.wx;
  } else {
    global.wx = previousWx;
  }
});

test("voice authorization consent persists agreed state for the active version", () => {
  const storage = {};
  global.wx = {
    getStorageSync(key) {
      return storage[key];
    },
    setStorageSync(key, value) {
      storage[key] = value;
    },
  };
  const service = loadVoiceAuthorizationService();

  assert.equal(service.readVoiceAuthorizationConsent(), false);
  const consent = service.saveVoiceAuthorizationConsent(true);

  assert.equal(consent.agreed, true);
  assert.equal(consent.version, service.VOICE_AUTHORIZATION_VERSION);
  assert.equal(service.readVoiceAuthorizationConsent(), true);

  service.saveVoiceAuthorizationConsent(false);
  assert.equal(service.readVoiceAuthorizationConsent(), false);
  delete global.wx;
});

test("voice authorization consent ignores stale versions", () => {
  global.wx = {
    getStorageSync() {
      return {
        agreed: true,
        version: "2026-01-01",
        agreedAt: "2026-01-01T00:00:00.000Z",
      };
    },
    setStorageSync() {},
  };
  const service = loadVoiceAuthorizationService();

  assert.equal(service.readVoiceAuthorizationConsent(), false);
  delete global.wx;
});
