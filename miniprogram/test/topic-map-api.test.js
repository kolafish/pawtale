require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function resetModules() {
  for (const modulePath of [
    "../services/auth.ts",
    "../services/request.ts",
    "../services/ai-model-settings.ts",
    "../services/memoir-api-client.ts",
    "../services/memoir-topic-map-api.ts",
  ]) {
    delete require.cache[require.resolve(modulePath)];
  }
}

function installWx(requests) {
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return {
          httpBaseUrl: "http://backend.local:8000/",
          devEmail: "local@example.com",
          devPassword: "secret",
        };
      }
      if (key === "pawtale_backend_cookie") {
        return "pawtale_session=abc";
      }
      return "";
    },
    getExtConfigSync() { return {}; },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({
          statusCode: 200,
          data: { id: "user-1" },
          cookies: [],
          header: {},
        });
        return;
      }
      options.success({
        statusCode: 200,
        data: {},
        cookies: [],
        header: {},
      });
    },
  };
}

test("topic map API encodes project and item ids and sends a controlled resolution", async () => {
  const requests = [];
  installWx(requests);
  resetModules();
  const {
    getMemoirTopicMap,
    updateMemoirTopicMapResolution,
  } = require("../services/memoir-topic-map-api.ts");

  await getMemoirTopicMap("project/1");
  await updateMemoirTopicMapResolution(
    "project/1",
    "item/1",
    "do_not_discuss",
  );

  const apiRequests = requests.filter((request) => (
    request.url.includes("/api/mobile/memoir/projects/")
  ));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/topic-map",
  );
  assert.equal(
    apiRequests[1].url,
    (
      "http://backend.local:8000/api/mobile/memoir/projects/project%2F1/"
      + "topic-map/items/item%2F1/resolution"
    ),
  );
  assert.equal(apiRequests[1].method, "PATCH");
  assert.deepEqual(apiRequests[1].data, {
    user_resolution: "do_not_discuss",
  });
  delete global.wx;
});
