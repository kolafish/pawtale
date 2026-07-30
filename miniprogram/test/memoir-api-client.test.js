require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { mockModule } = require("./page-harness");

test("memoir requests invalidate snapshots only for content mutations", async () => {
  let invalidations = 0;
  mockModule("../services/auth.ts", {
    ensureBackendSession: async () => {},
    requestWithBackendSession: async () => ({}),
  });
  mockModule("../services/ai-model-settings.ts", {
    readSelectedAIModel: () => "test-model",
  });
  mockModule("../stores/memoir-read-cache.ts", {
    markCurrentUserMemoirSnapshotsStale: () => {
      invalidations += 1;
    },
  });
  delete require.cache[require.resolve("../services/memoir-api-client.ts")];
  const { memoirRequest } = require("../services/memoir-api-client.ts");

  await memoirRequest("/preview", {
    method: "POST",
    invalidatesSnapshots: false,
  });
  await memoirRequest("/content", { method: "POST" });

  assert.equal(invalidations, 1);
});
