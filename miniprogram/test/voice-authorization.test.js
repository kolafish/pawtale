require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadVoiceAuthorizationService({ authExports, requestExports } = {}) {
  const servicePath = require.resolve("../services/voice-authorization.ts");
  const authPath = require.resolve("../services/auth.ts");
  const requestPath = require.resolve("../services/request.ts");
  delete require.cache[servicePath];
  delete require.cache[authPath];
  delete require.cache[requestPath];
  if (authExports) {
    require.cache[authPath] = {
      id: authPath,
      filename: authPath,
      loaded: true,
      exports: authExports,
    };
  }
  if (requestExports) {
    require.cache[requestPath] = {
      id: requestPath,
      filename: requestPath,
      loaded: true,
      exports: requestExports,
    };
  }
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

test("voice authorization consent exposes the active consent record", () => {
  global.wx = {
    getStorageSync() {
      return {
        agreed: true,
        version: "2026-06-18",
        agreedAt: "2026-06-18T00:00:00.000Z",
      };
    },
    setStorageSync() {},
  };
  const service = loadVoiceAuthorizationService();

  assert.deepEqual(service.readVoiceAuthorizationConsentRecord(), {
    agreed: true,
    version: service.VOICE_AUTHORIZATION_VERSION,
    agreedAt: "2026-06-18T00:00:00.000Z",
  });
  delete global.wx;
});

test("voice recording authorization syncs consent before requesting recorder privacy", async () => {
  const requests = [];
  let ensuredSession = false;
  let privacyAuthorized = false;
  const storage = {};
  global.wx = {
    getStorageSync(key) {
      return storage[key];
    },
    setStorageSync(key, value) {
      storage[key] = value;
    },
    getPrivacySetting({ success }) {
      success({ needAuthorization: true });
    },
    requirePrivacyAuthorize({ success }) {
      privacyAuthorized = true;
      success();
    },
    getSetting({ success }) {
      success({ authSetting: {} });
    },
    authorize({ scope, success }) {
      assert.equal(scope, "scope.record");
      success();
    },
  };
  const service = loadVoiceAuthorizationService({
    authExports: {
      ensureBackendSession: async () => {
        ensuredSession = true;
      },
    },
    requestExports: {
      request: async (path, options) => {
        requests.push({ path, options });
        return {
          id: "consent-1",
          agreed: options.data.agreed,
        };
      },
    },
  });
  service.saveVoiceAuthorizationConsent(true);

  const authorized = await service.ensureVoiceRecordingAuthorization("voice-1");

  assert.equal(authorized, true);
  assert.equal(ensuredSession, true);
  assert.equal(privacyAuthorized, true);
  assert.equal(requests[0].path, "/api/mobile/memoir/voice-authorization-consent");
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(requests[0].options.data, {
    version: service.VOICE_AUTHORIZATION_VERSION,
    agreed: true,
    source: "miniprogram",
    source_session_id: "voice-1",
    metadata_json: {
      agreed_at: storage[service.VOICE_AUTHORIZATION_STORAGE_KEY].agreedAt,
    },
  });
  delete global.wx;
});

test("voice recording authorization opens the consent page before using the recorder", async () => {
  const navigations = [];
  global.wx = {
    getStorageSync() {},
    navigateTo({ url }) {
      navigations.push(url);
    },
  };
  const service = loadVoiceAuthorizationService();

  assert.equal(await service.ensureVoiceRecordingAuthorization(), false);
  assert.deepEqual(navigations, [service.VOICE_AUTHORIZATION_PAGE_URL]);
  delete global.wx;
});
