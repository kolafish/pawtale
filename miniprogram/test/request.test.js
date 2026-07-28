require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  ApiError,
  optionalApiResource,
} = require("../services/request.ts");

test("optional API resources map only HTTP 404 to null", async () => {
  assert.equal(await optionalApiResource(Promise.resolve("value")), "value");
  assert.equal(
    await optionalApiResource(Promise.reject(new ApiError(404, "missing", {}))),
    null,
  );
  await assert.rejects(
    optionalApiResource(Promise.reject(new ApiError(500, "failed", {}))),
    /failed/,
  );
});
