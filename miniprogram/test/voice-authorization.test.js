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
  const storage = { pawtale_memoir_session: { cache_identity: { user_id: "user-1" } } };
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
    getStorageSync(key) {
      if (key === "pawtale_memoir_session") return { cache_identity: { user_id: "user-1" } };
      return { userId: "user-1", consent: {
        agreed: true, version: "2026-01-01", agreedAt: "2026-01-01T00:00:00.000Z",
      } };
    },
    setStorageSync() {},
  };
  const service = loadVoiceAuthorizationService();

  assert.equal(service.readVoiceAuthorizationConsent(), false);
  delete global.wx;
});

test("voice authorization consent exposes the active consent record", () => {
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_memoir_session") return { cache_identity: { user_id: "user-1" } };
      return { userId: "user-1", consent: {
        agreed: true, version: "2026-09-21", agreedAt: "2026-09-21T00:00:00.000Z",
      } };
    },
    setStorageSync() {},
  };
  const service = loadVoiceAuthorizationService();

  assert.deepEqual(service.readVoiceAuthorizationConsentRecord(), {
    agreed: true,
    version: service.VOICE_AUTHORIZATION_VERSION,
    agreedAt: "2026-09-21T00:00:00.000Z",
  });
  delete global.wx;
});

test("voice recording authorization syncs consent before requesting recorder privacy", async () => {
  const requests = [];
  let ensuredSession = false;
  let privacyAuthorized = false;
  const storage = { pawtale_memoir_session: { cache_identity: { user_id: "user-1" } } };
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
      getMemoirIdentity: async () => ({ id: "user-1" }),
    },
    requestExports: {
      request: async (path, options) => {
        requests.push({ path, options });
        if (options?.method !== "POST") {
          return { has_current_consent: true, consent: { id: "consent-1", agreed: true } };
        }
        return {
          id: "consent-1",
          agreed: options.data.agreed,
        };
      },
    },
  });
  const consent = service.saveVoiceAuthorizationConsent(true);
  await service.syncVoiceAuthorizationConsent({ consent, sourceSessionId: "voice-1" });

  const authorized = await service.ensureVoiceRecordingAuthorization("voice-1");

  assert.equal(authorized, true);
  assert.equal(ensuredSession, true);
  assert.equal(privacyAuthorized, true);
  assert.equal(requests[0].path, "/api/mobile/memoir/voice-authorization-consent");
  assert.equal(requests[0].options.method, "POST");
  assert.match(requests[1].path, /voice-authorization-consent\?version=/);
  assert.deepEqual(requests[0].options.data, {
    version: service.VOICE_AUTHORIZATION_VERSION,
    agreed: true,
    source: "miniprogram",
    source_session_id: "voice-1",
    metadata_json: {
      agreed_at: consent.agreedAt,
    },
  });
  delete global.wx;
});

test("consent from one account is not visible after switching accounts", () => {
  const storage = { pawtale_memoir_session: { cache_identity: { user_id: "user-A" } } };
  global.wx = {
    getStorageSync: (key) => storage[key],
    setStorageSync: (key, value) => { storage[key] = value; },
  };
  const service = loadVoiceAuthorizationService();
  service.saveVoiceAuthorizationConsent(true);
  assert.equal(service.readVoiceAuthorizationConsent(), true);

  storage.pawtale_memoir_session = { cache_identity: { user_id: "user-B" } };
  assert.equal(service.readVoiceAuthorizationConsent(), false);
  delete global.wx;
});

test("server revocation overrides a locally cached agreement before recording", async () => {
  const navigations = [];
  const storage = { pawtale_memoir_session: { cache_identity: { user_id: "user-1" } } };
  global.wx = {
    getStorageSync: (key) => storage[key],
    setStorageSync: (key, value) => { storage[key] = value; },
    navigateTo: ({ url }) => navigations.push(url),
  };
  const service = loadVoiceAuthorizationService({
    authExports: {
      ensureBackendSession: async () => {},
      getMemoirIdentity: async () => ({ id: "user-1" }),
    },
    requestExports: {
      request: async () => ({ has_current_consent: false, consent: null }),
    },
  });
  service.saveVoiceAuthorizationConsent(true);

  assert.equal(await service.ensureVoiceRecordingAuthorization(), false);
  assert.equal(service.readVoiceAuthorizationConsent(), false);
  assert.deepEqual(navigations, [service.VOICE_AUTHORIZATION_PAGE_URL]);
  delete global.wx;
});

test("voice recording authorization opens the consent page before using the recorder", async () => {
  const navigations = [];
  const storage = { pawtale_memoir_session: { cache_identity: { user_id: "user-1" } } };
  global.wx = {
    getStorageSync: (key) => storage[key],
    setStorageSync: (key, value) => { storage[key] = value; },
    navigateTo({ url }) {
      navigations.push(url);
    },
  };
  const service = loadVoiceAuthorizationService({
    authExports: {
      ensureBackendSession: async () => {},
      getMemoirIdentity: async () => ({ id: "user-1" }),
    },
    requestExports: {
      request: async () => ({ has_current_consent: false, consent: null }),
    },
  });

  assert.equal(await service.ensureVoiceRecordingAuthorization(), false);
  assert.deepEqual(navigations, [service.VOICE_AUTHORIZATION_PAGE_URL]);
  delete global.wx;
});

test("server agreement restores authorization on a new device", async () => {
  const storage = { pawtale_memoir_session: { cache_identity: { user_id: "user-1" } } };
  global.wx = {
    getStorageSync: (key) => storage[key],
    setStorageSync: (key, value) => { storage[key] = value; },
    getPrivacySetting: ({ success }) => success({ needAuthorization: false }),
  };
  const service = loadVoiceAuthorizationService({
    authExports: {
      ensureBackendSession: async () => {},
      getMemoirIdentity: async () => ({ id: "user-1" }),
    },
    requestExports: {
      request: async () => ({ has_current_consent: true, consent: {
        id: "consent-1", agreed: true, created_at: "2026-09-21T00:00:00.000Z",
      } }),
    },
  });

  assert.equal(service.readVoiceAuthorizationConsent(), false);
  assert.equal(await service.ensureVoiceRecordingAuthorization(), true);
  assert.equal(service.readVoiceAuthorizationConsent(), true);
  delete global.wx;
});
