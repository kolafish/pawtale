require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function resetModules() {
  for (const modulePath of [
    "../services/auth.ts",
    "../services/request.ts",
    "../services/ai-model-settings.ts",
    "../services/memoir-api-client.ts",
    "../services/memoir-book-api.ts",
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
      if (key === "pawtale_ai_model_settings") {
        return { model: "deepseek-v4-flash" };
      }
      return "";
    },
    getExtConfigSync() { return {}; },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      if (options.url.endsWith("/api/auth/me")) {
        options.success({ statusCode: 200, data: { id: "user-1" }, cookies: [], header: {} });
        return;
      }
      options.success({ statusCode: 200, data: {}, cookies: [], header: {} });
    },
  };
}

test("book consistency API uses narrative routes and explicit formal version ids", async () => {
  const requests = [];
  installWx(requests);
  resetModules();
  const {
    enqueueMemoirBookConsistencyAudit,
    formalizeMemoirBookOutput,
    getMemoirBookConsistencyAudit,
    getMemoirBookReadiness,
  } = require("../services/memoir-book-api.ts");

  await getMemoirBookReadiness("project/1");
  await enqueueMemoirBookConsistencyAudit("project/1");
  await getMemoirBookConsistencyAudit("project/1", "job/1");
  await formalizeMemoirBookOutput("project/1", {
    audit_job_id: "job-1",
    output_version_id: "version-1",
  });

  const apiRequests = requests.filter((request) => request.url.includes("/api/memoir/projects/"));
  assert.equal(
    apiRequests[0].url,
    "http://backend.local:8000/api/memoir/projects/project%2F1/book-readiness",
  );
  assert.equal(
    apiRequests[1].url,
    "http://backend.local:8000/api/memoir/projects/project%2F1/book-consistency-audits",
  );
  assert.deepEqual(apiRequests[1].data, {
    ai_model: "deepseek-v4-flash",
  });
  assert.equal(
    apiRequests[2].url,
    "http://backend.local:8000/api/memoir/projects/project%2F1/book-consistency-audits/job%2F1",
  );
  assert.equal(
    apiRequests[3].url,
    "http://backend.local:8000/api/memoir/projects/project%2F1/book-output/formalize",
  );
  assert.deepEqual(apiRequests[3].data, {
    audit_job_id: "job-1",
    output_version_id: "version-1",
  });
  delete global.wx;
});
