require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function loadHomeLaunchState() {
  const modulePath = require.resolve("../services/home-launch-state.ts");
  delete require.cache[modulePath];
  return require(modulePath);
}

test("home launch decision shows first visit once", () => {
  const { decideHomeLaunch } = loadHomeLaunchState();
  const first = decideHomeLaunch({
    state: {},
    hasProject: false,
    projectId: "",
    nowMs: Date.parse("2026-07-01T00:00:00.000Z"),
  });
  const second = decideHomeLaunch({
    state: first.state,
    hasProject: false,
    projectId: "",
    nowMs: Date.parse("2026-07-01T00:05:00.000Z"),
  });

  assert.equal(first.variant, "first_visit");
  assert.equal(second.variant, "none");
});

test("home launch decision prioritizes a newly created active project", () => {
  const { decideHomeLaunch } = loadHomeLaunchState();
  const decision = decideHomeLaunch({
    state: {
      first_seen_at: "2026-06-01T00:00:00.000Z",
      last_opened_at: "2026-06-30T00:00:00.000Z",
      pending_new_project: {
        project_id: "project-1",
        project_title: "爷爷的故事",
        marked_at: "2026-07-01T00:00:00.000Z",
      },
    },
    hasProject: true,
    projectId: "project-1",
    nowMs: Date.parse("2026-07-01T00:00:00.000Z"),
  });

  assert.equal(decision.variant, "new_project");
  assert.equal(decision.state.pending_new_project.project_id, "project-1");
});

test("home launch decision shows a light return after a long absence", () => {
  const { decideHomeLaunch } = loadHomeLaunchState();
  const first = decideHomeLaunch({
    state: {
      first_seen_at: "2026-06-01T00:00:00.000Z",
      last_opened_at: "2026-06-01T00:00:00.000Z",
    },
    hasProject: true,
    projectId: "project-1",
    nowMs: Date.parse("2026-07-01T00:00:00.000Z"),
  });
  const second = decideHomeLaunch({
    state: first.state,
    hasProject: true,
    projectId: "project-1",
    nowMs: Date.parse("2026-07-01T00:05:00.000Z"),
  });

  assert.equal(first.variant, "returning_soft");
  assert.equal(second.variant, "none");
});

test("home launch project marker is dismissed after the new-project launch", () => {
  const storage = {};
  global.wx = {
    getStorageSync(key) {
      return storage[key];
    },
    setStorageSync(key, value) {
      storage[key] = value;
    },
  };
  const {
    dismissHomeLaunch,
    markHomeLaunchProjectCreated,
    resolveHomeLaunch,
  } = loadHomeLaunchState();

  markHomeLaunchProjectCreated({
    userId: "user-1",
    projectId: "project-1",
    projectTitle: "爷爷的故事",
    nowMs: Date.parse("2026-07-01T00:00:00.000Z"),
  });

  assert.equal(resolveHomeLaunch("user-1", {
    hasProject: true,
    projectId: "project-1",
    nowMs: Date.parse("2026-07-01T00:01:00.000Z"),
  }), "new_project");

  dismissHomeLaunch("user-1", "new_project", "project-1");

  assert.equal(resolveHomeLaunch("user-1", {
    hasProject: true,
    projectId: "project-1",
    nowMs: Date.parse("2026-07-01T00:02:00.000Z"),
  }), "none");

  delete global.wx;
});

test("home launch storage failures do not block the main flow", () => {
  const warnings = [];
  const originalWarn = console.warn;
  console.warn = (...args) => warnings.push(args);
  global.wx = {
    getStorageSync() {
      throw new Error("storage read failed");
    },
    setStorageSync() {
      throw new Error("storage write failed");
    },
  };
  const {
    dismissHomeLaunch,
    markHomeLaunchProjectCreated,
    resolveHomeLaunch,
  } = loadHomeLaunchState();

  assert.doesNotThrow(() => {
    markHomeLaunchProjectCreated({
      userId: "user-1",
      projectId: "project-1",
      projectTitle: "爷爷的故事",
    });
  });
  assert.doesNotThrow(() => {
    dismissHomeLaunch("user-1", "new_project", "project-1");
  });
  assert.equal(resolveHomeLaunch("user-1", {
    hasProject: true,
    projectId: "project-1",
    nowMs: Date.parse("2026-07-01T00:00:00.000Z"),
  }), "first_visit");
  assert.ok(warnings.length >= 1);

  console.warn = originalWarn;
  delete global.wx;
});
