require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function trackerModule() {
  delete require.cache[require.resolve("../services/request.ts")];
  delete require.cache[require.resolve("../services/audit-tracker.ts")];
  return require("../services/audit-tracker.ts");
}

function installWxMock(handler, cookie = "pawtale_session=abc") {
  const requests = [];
  global.wx = {
    getStorageSync(key) {
      if (key === "pawtale_backend_config") {
        return { httpBaseUrl: "https://api.example.com" };
      }
      if (key === "pawtale_backend_cookie") {
        return cookie;
      }
      return "";
    },
    getExtConfigSync() {
      return {};
    },
    setStorageSync() {},
    removeStorageSync() {},
    request(options) {
      requests.push(options);
      handler(options);
    },
  };
  return requests;
}

function successResponse(options) {
  options.success({
    statusCode: 200,
    data: {
      accepted: options.data.events.length,
      duplicate: 0,
      session_id: "audit-session-1",
    },
    cookies: [],
    header: {},
  });
}

test("audit tracker maps the finished-book reader to book analytics", () => {
  const tracker = trackerModule();

  assert.equal(tracker.auditFeatureForPage("/pages/book-reader/index"), "book.view");
  tracker.__resetAuditTrackerForTest();
});

test("audit tracker batches page view and leave events", async () => {
  const requests = installWxMock(successResponse);
  const tracker = trackerModule();
  let currentTime = new Date("2026-07-01T12:00:00Z");
  tracker.__setAuditNowForTest(() => currentTime);

  tracker.trackAuditPageShow("/pages/home/index");
  currentTime = new Date("2026-07-01T12:00:12Z");
  await tracker.trackAuditPageLeave("app_background");

  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "https://api.example.com/api/audit/events");
  assert.equal(requests[0].method, "POST");
  assert.equal(requests[0].timeout, 8000);
  assert.equal(requests[0].data.platform, "miniprogram");
  assert.equal(requests[0].data.events.length, 2);
  assert.deepEqual(
    requests[0].data.events.map((event) => event.event_type),
    ["page_view", "page_leave"],
  );
  assert.equal(requests[0].data.events[0].page_path, "/pages/home/index");
  assert.equal(requests[0].data.events[0].feature_code, "home.view");
  assert.equal(requests[0].data.events[1].duration_ms, 12000);
  assert.equal(requests[0].data.events[1].exit_reason, "app_background");

  tracker.__resetAuditTrackerForTest();
  delete global.wx;
});

test("audit tracker requeues events after transient upload failure", async () => {
  let failNext = true;
  const requests = installWxMock((options) => {
    if (failNext) {
      failNext = false;
      options.fail({ errMsg: "request:fail timeout" });
      return;
    }
    successResponse(options);
  });
  const tracker = trackerModule();
  let currentTime = new Date("2026-07-01T12:00:00Z");
  tracker.__setAuditNowForTest(() => currentTime);

  tracker.trackAuditPageShow("/pages/book/index");
  currentTime = new Date("2026-07-01T12:00:05Z");
  await tracker.trackAuditPageLeave("page_unload");
  assert.equal(requests.length, 1);

  await tracker.flushAuditEvents();
  assert.equal(requests.length, 2);
  assert.deepEqual(
    requests[1].data.events.map((event) => event.event_type),
    ["page_view", "page_leave"],
  );
  assert.equal(requests[1].data.events[0].feature_code, "book.view");

  tracker.__resetAuditTrackerForTest();
  delete global.wx;
});

test("audit tracker wraps Page lifecycle without replacing page handlers", async () => {
  const requests = installWxMock(successResponse);
  const tracker = trackerModule();
  let currentTime = new Date("2026-07-01T12:00:00Z");
  tracker.__setAuditNowForTest(() => currentTime);

  let capturedPage = null;
  const calls = [];
  global.Page = (definition) => {
    capturedPage = definition;
  };

  tracker.installAuditPageTracker();
  global.Page({
    data: {},
    onShow() {
      calls.push("show");
    },
    onHide() {
      calls.push("hide");
    },
  });

  assert.ok(capturedPage);
  capturedPage.onShow.call({ route: "pages/transcripts/index" });
  currentTime = new Date("2026-07-01T12:00:04Z");
  capturedPage.onHide.call({ route: "pages/transcripts/index" });
  await tracker.flushAuditEvents();

  assert.deepEqual(calls, ["show", "hide"]);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].data.events[0].page_path, "/pages/transcripts/index");
  assert.equal(requests[0].data.events[0].feature_code, "transcript.view");
  assert.equal(requests[0].data.events[1].duration_ms, 4000);

  tracker.__resetAuditTrackerForTest();
  delete global.Page;
  delete global.wx;
});

test("audit action events include project and voice session context", async () => {
  const requests = installWxMock(successResponse);
  const tracker = trackerModule();
  tracker.trackAuditAction(
    "voice_chat.start",
    { guidance_mode: "guided_interview" },
    { projectId: "project-1", voiceSessionId: "voice-1" },
  );
  await tracker.flushAuditEvents();

  assert.equal(requests.length, 1);
  assert.equal(requests[0].data.events.length, 1);
  assert.equal(requests[0].data.events[0].event_type, "action");
  assert.equal(requests[0].data.events[0].feature_code, "voice_chat.start");
  assert.equal(requests[0].data.events[0].project_id, "project-1");
  assert.equal(requests[0].data.events[0].voice_session_id, "voice-1");
  assert.deepEqual(requests[0].data.events[0].metadata_json, {
    guidance_mode: "guided_interview",
  });

  tracker.__resetAuditTrackerForTest();
  delete global.wx;
});

test("audit tracker reports mini-program memory warnings", async () => {
  const requests = installWxMock(successResponse);
  let memoryWarningListener = null;
  global.wx.onMemoryWarning = (listener) => {
    memoryWarningListener = listener;
  };
  const tracker = trackerModule();

  tracker.installAuditMemoryWarningTracker();
  memoryWarningListener({ level: 10 });
  await tracker.flushAuditEvents();

  assert.equal(requests.length, 1);
  assert.equal(requests[0].data.events[0].feature_code, "runtime.memory_warning");
  assert.deepEqual(requests[0].data.events[0].metadata_json, { level: 10 });

  tracker.__resetAuditTrackerForTest();
  delete global.wx;
});

test("audit tracker drops queued events when storage APIs are unavailable", async () => {
  const tracker = trackerModule();
  global.wx = {};

  tracker.trackAuditAction("project.manage");
  await tracker.flushAuditEvents();

  tracker.__resetAuditTrackerForTest();
  delete global.wx;
});
