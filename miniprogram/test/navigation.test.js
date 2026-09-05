require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { ApiError } = require("../services/request.ts");
const {
  isAuthError,
  missingPageRecoveryUrl,
  navigateToBook,
  navigateToLogin,
  navigateToTranscripts,
  recoverMissingPage,
  redirectToBook,
  redirectToTranscripts,
  relaunchHome,
  returnToBook,
  routeAuthError,
  returnToHome,
  returnToStory,
  consumeHomeStoryTarget,
  navigateToMe,
} = require("../services/navigation.ts");

test("navigation helper identifies backend auth errors", () => {
  assert.equal(isAuthError(new ApiError(401, "login required", null)), true);
  assert.equal(isAuthError(new ApiError(403, "forbidden", null)), true);
  assert.equal(isAuthError(new ApiError(404, "missing", null)), false);
  assert.equal(isAuthError(new Error("network")), false);
});

test("home is the single root and settings remains a regular page", () => {
  const config = require("../app.json");
  assert.equal(config.pages[0], "pages/home/index");
  assert.equal(config.tabBar, undefined);
  assert.equal(config.pages.includes("pages/story/index"), false);
  assert.equal(config.pages.includes("pages/me/index"), true);
  assert.equal(missingPageRecoveryUrl("pages/story/index", { projectId: "project-1" }), "/pages/home/index?projectId=project-1&section=story");
});

test("returning to stories keeps the existing home and consumes the target once", () => {
  const calls = [];
  global.getCurrentPages = () => [{ route: "pages/home/index" }, { route: "pages/book/index" }, { route: "pages/chapter-workspace/index" }];
  global.wx = { navigateBack: ({ delta }) => calls.push(delta) };
  returnToStory("project-1");
  assert.deepEqual(calls, [2]);
  assert.equal(consumeHomeStoryTarget(), "project-1");
  assert.equal(consumeHomeStoryTarget(), null);
  global.getCurrentPages = () => [{ route: "pages/home/index" }];
  returnToHome();
  assert.deepEqual(calls, [2]);
  delete global.getCurrentPages;
  delete global.wx;
});

test("navigation helper returns to the existing book page without stacking a duplicate", () => {
  const calls = [];
  global.getCurrentPages = () => [
    { route: "pages/story/index" },
    { route: "pages/book/index" },
    { route: "pages/chapter-workspace/index" },
    { route: "pages/book-completion/index" },
  ];
  global.wx = {
    navigateBack(options) {
      calls.push({ method: "navigateBack", delta: options.delta });
    },
    redirectTo(options) {
      calls.push({ method: "redirectTo", url: options.url });
    },
  };

  returnToBook();

  assert.deepEqual(calls, [{ method: "navigateBack", delta: 2 }]);
  delete global.getCurrentPages;
  delete global.wx;
});

test("navigation helper replaces the current page when no book page exists", () => {
  const calls = [];
  global.getCurrentPages = () => [
    { route: "pages/story/index" },
    { route: "pages/book-completion/index" },
  ];
  global.wx = {
    navigateBack(options) {
      calls.push({ method: "navigateBack", delta: options.delta });
    },
    redirectTo(options) {
      calls.push({ method: "redirectTo", url: options.url });
    },
  };

  returnToBook();

  assert.deepEqual(calls, [
    { method: "redirectTo", url: "/pages/book/index" },
  ]);
  delete global.getCurrentPages;
  delete global.wx;
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
    redirectTo(options) {
      calls.push({ method: "redirectTo", url: options.url });
    },
    switchTab(options) {
      calls.push({ method: "switchTab", url: options.url });
    },
  };

  assert.equal(routeAuthError(new ApiError(401, "login required", null)), true);
  assert.equal(routeAuthError(new ApiError(403, "forbidden", null), "me"), true);
  assert.equal(routeAuthError(new ApiError(404, "missing", null)), false);
  navigateToLogin();
  navigateToBook();
  navigateToTranscripts();
  redirectToBook();
  redirectToTranscripts();
  relaunchHome();
  returnToHome();
  navigateToMe();

  assert.deepEqual(calls, [
    { method: "reLaunch", url: "/pages/login/index" },
    { method: "navigateTo", url: "/pages/me/index" },
    { method: "navigateTo", url: "/pages/login/index" },
    { method: "navigateTo", url: "/pages/book/index" },
    { method: "navigateTo", url: "/pages/transcripts/index" },
    { method: "redirectTo", url: "/pages/book/index" },
    { method: "redirectTo", url: "/pages/transcripts/index" },
    { method: "reLaunch", url: "/pages/home/index" },
    { method: "reLaunch", url: "/pages/home/index" },
    { method: "navigateTo", url: "/pages/me/index" },
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
