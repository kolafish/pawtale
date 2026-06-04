const assert = require("node:assert/strict");
const test = require("node:test");

const { handleMemoirApiRequest } = require("../src/router");

test("returns the memoir cloud function health response", async () => {
  const response = await handleMemoirApiRequest({
    method: "GET",
    path: "/api/mobile/memoir/health",
  });

  assert.equal(response.statusCode, 200);
  assert.deepEqual(response.data, {
    ok: true,
    service: "memoir-api",
  });
});

test("normalizes method and path", async () => {
  const response = await handleMemoirApiRequest({
    method: "get",
    path: "api/mobile/memoir/health",
  });

  assert.equal(response.statusCode, 200);
});

test("returns 400 when path is missing", async () => {
  const response = await handleMemoirApiRequest({ method: "GET" });

  assert.equal(response.statusCode, 400);
  assert.equal(response.data.detail, "缺少请求路径");
});

test("returns 404 for routes that are not implemented yet", async () => {
  const response = await handleMemoirApiRequest({
    method: "GET",
    path: "/api/mobile/memoir/project",
  });

  assert.equal(response.statusCode, 404);
  assert.equal(response.data.detail, "接口未实现");
});
