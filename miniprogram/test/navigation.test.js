require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { ApiError } = require("../services/request.ts");
const {
  isAuthError,
  missingPageRecoveryUrl,
  navigateToLogin,
  recoverMissingPage,
  relaunchHome,
  routeAuthError,
  switchToBook,
  switchToHome,
  switchToMe,
} = require("../services/navigation.ts");

test("navigation helper identifies backend auth errors", () => {
  assert.equal(isAuthError(new ApiError(401, "login required", null)), true);
  assert.equal(isAuthError(new ApiError(403, "forbidden", null)), true);
  assert.equal(isAuthError(new ApiError(404, "missing", null)), false);
  assert.equal(isAuthError(new Error("network")), false);
});

test("navigation helper routes auth errors to login or me", () => {
  const calls = [];
  global.wx = {
    navigateTo(options) {
      calls.push({ method: "navigateTo", url: options.url });
    },
    reLaunch(options) {
      calls.push({ method: "reLaunch", url: options.url });
    },
    switchTab(options) {
      calls.push({ method: "switchTab", url: options.url });
    },
  };

  assert.equal(routeAuthError(new ApiError(401, "login required", null)), true);
  assert.equal(routeAuthError(new ApiError(403, "forbidden", null), "me"), true);
  assert.equal(routeAuthError(new ApiError(404, "missing", null)), false);
  navigateToLogin();
  relaunchHome();
  switchToHome();
  switchToBook();
  switchToMe();

  assert.deepEqual(calls, [
    { method: "reLaunch", url: "/pages/login/index" },
    { method: "switchTab", url: "/pages/me/index" },
    { method: "navigateTo", url: "/pages/login/index" },
    { method: "reLaunch", url: "/pages/home/index" },
    { method: "switchTab", url: "/pages/home/index" },
    { method: "switchTab", url: "/pages/book/index" },
    { method: "switchTab", url: "/pages/me/index" },
  ]);
  delete global.wx;
});

test("navigation helper recovers removed page routes", () => {
  const calls = [];
  global.wx = {
    reLaunch(options) {
      calls.push(options.url);
    },
  };

  assert.equal(
    missingPageRecoveryUrl("pages/book-chapter/index", {
      projectId: "project 1",
      chapterId: "chapter/2",
    }),
    "/pages/chapter-workspace/index?projectId=project%201&chapterId=chapter%2F2",
  );
  assert.equal(
    missingPageRecoveryUrl("/pages/source-review/index", { eventId: "event-1" }),
    "/pages/home/index",
  );
  assert.equal(missingPageRecoveryUrl("pages/home/index"), null);

  recoverMissingPage("pages/book-chapter/index", {
    projectId: "project-1",
    chapterId: "chapter-2",
  });
  recoverMissingPage("pages/removed/index");
  recoverMissingPage("pages/home/index");

  assert.deepEqual(calls, [
    "/pages/chapter-workspace/index?projectId=project-1&chapterId=chapter-2",
    "/pages/home/index",
  ]);
  delete global.wx;
});
