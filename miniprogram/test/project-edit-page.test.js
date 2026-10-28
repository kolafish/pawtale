require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { ApiError } = require("../services/request.ts");
const {
  loadPageModule,
  mockModule,
  pageContext,
} = require("./page-harness");

function loadProjectEditPage({
  project,
  updateMemoirProject,
  createMemoirProject = async () => project,
  activateMemoirProject = async () => {},
  getMemoirBookShareStatus = async () => ({ active: false }),
  updateMemoirBookShare = async () => {},
  revokeMemoirBookShare = async () => {},
}) {
  mockModule("../services/memoir-book-api.ts", {
    getMemoirBookShareStatus,
    updateMemoirBookShare,
    revokeMemoirBookShare,
  });
  mockModule("../services/auth.ts", {
    getMemoirIdentity: async () => ({
      id: "user-1",
      active_project_id: project.id,
      active_project: project,
      projects: [project],
    }),
  });
  mockModule("../services/audit-tracker.ts", { trackAuditAction: () => {} });
  mockModule("../services/home-launch-state.ts", { markHomeLaunchProjectCreated: () => {} });
  mockModule("../services/memoir.ts", {
    activateMemoirProject,
    createMemoirProject,
    listMemoirProjects: async () => ({
      active_project_id: project.id,
      active_project: project,
      projects: [project],
    }),
    updateMemoirProject,
  });
  return loadPageModule("../pages/project-edit/index.ts");
}

test("project edit loads and saves memoir-level interview and polish settings", async () => {
  const updates = [];
  const project = {
    id: "project-1",
    title: "爷爷的回忆",
    metadata_json: {
      transcript_polish_level: "high",
      share_video_style: "uploaded",
      memoir_profile: {
        narrator_name: "爷爷",
        narrator_gender: "male",
        narrator_origin: "四川",
        narrator_age: 97,
        default_guidance_mode: "free_narration",
        default_interview_type: "human_interview",
        default_session_goal: "open_narration",
      },
    },
  };
  global.wx = {
    navigateBack() {},
    setNavigationBarTitle() {},
    showToast() {},
  };
  const { page } = loadProjectEditPage({
    project,
    updateMemoirProject: async (projectId, payload) => updates.push({ projectId, payload }),
  });
  const context = pageContext(page);

  await context.onLoad({ projectId: project.id });
  assert.equal(context.data.narratorName, "爷爷");
  assert.equal(context.data.defaultNarrationMode, "human_interview");
  assert.equal(context.data.transcriptPolishLevel, "high");
  assert.equal(context.data.shareVideoStyle, "uploaded");
  assert.deepEqual(context.data.narrationOptions.map(option => option.value), [
    "guided_interview",
    "book_completion",
    "human_interview",
  ]);
  assert.deepEqual(context.data.transcriptPolishOptions.map(option => option.value), [
    "low",
    "medium",
    "high",
  ]);

  context.selectNarrationMode({ currentTarget: { dataset: { mode: "book_completion" } } });
  context.selectTranscriptPolishLevel({ currentTarget: { dataset: { polish: "medium" } } });
  context.selectShareVideoStyle({ currentTarget: { dataset: { videoStyle: "uploaded" } } });
  await context.saveProject();

  assert.equal(updates.length, 1);
  assert.equal(updates[0].projectId, project.id);
  assert.equal(updates[0].payload.elder_name, "爷爷");
  assert.equal(updates[0].payload.default_guidance_mode, "guided_interview");
  assert.equal(updates[0].payload.default_interview_type, "ai_interview");
  assert.equal(updates[0].payload.default_session_goal, "book_completion");
  assert.equal(updates[0].payload.transcript_polish_level, "medium");
  assert.equal(updates[0].payload.share_video_style, "uploaded");
});

test("whole-book sharing setting uses existing authorization and refreshes after returning", async () => {
  const project = { id: "project-1", title: "爷爷的回忆" };
  const changes = [];
  let active = true;
  global.wx = { setNavigationBarTitle() {} };
  const { page } = loadProjectEditPage({
    project,
    getMemoirBookShareStatus: async (id) => {
      assert.equal(id, project.id);
      return { active };
    },
    updateMemoirBookShare: async (...args) => { changes.push(["enable", ...args]); active = true; },
    revokeMemoirBookShare: async (...args) => { changes.push(["disable", ...args]); active = false; },
  });
  const context = pageContext(page);
  await context.onLoad({ projectId: project.id });
  assert.equal(context.data.bookShareEnabled, true);
  await context.toggleBookShare({ detail: { value: false } });
  assert.equal(context.data.bookShareEnabled, false);
  await context.toggleBookShare({ detail: { value: true } });
  assert.equal(context.data.bookShareEnabled, true);
  assert.deepEqual(changes, [["disable", project.id], ["enable", project.id]]);

  active = false; // Revoked from the existing book sharing menu.
  await context.onShow();
  assert.equal(context.data.bookShareEnabled, false);
});

test("failed sharing changes restore the switch and concurrent changes cannot reverse an in-flight request", async () => {
  const project = { id: "project-1", title: "爷爷的回忆" };
  let failChange;
  let mutations = 0;
  let reads = 0;
  global.wx = { setNavigationBarTitle() {} };
  const { page } = loadProjectEditPage({
    project,
    getMemoirBookShareStatus: async () => { reads += 1; return { active: false }; },
    updateMemoirBookShare: () => {
      mutations += 1;
      return new Promise((_, reject) => { failChange = reject; });
    },
    revokeMemoirBookShare: () => assert.fail("must not revoke during an in-flight change"),
    updateMemoirProject: () => assert.fail("must not save while sharing is changing"),
  });
  const context = pageContext(page);
  await context.onLoad({ projectId: project.id });
  const change = context.toggleBookShare({ detail: { value: true } });
  assert.equal(context.data.bookShareBusy, true);
  await context.toggleBookShare({ detail: { value: false } });
  await context.onShow();
  await context.saveProject();
  failChange(new ApiError(409, "书稿正在更新，请完成更新后再分享", null));
  await change;
  assert.equal(mutations, 1);
  assert.equal(reads, 1);
  assert.equal(context.data.bookShareEnabled, false);
  assert.equal(context.data.bookShareBusy, false);
  assert.match(context.data.bookShareError, /书稿正在更新/);
});

test("a committed sharing change with a lost response is reconciled with the server instead of reverted", async () => {
  for (const statusCode of [0, 502]) for (const initialActive of [false, true]) {
    const project = { id: "project-1", title: "爷爷的回忆" };
    let active = initialActive;
    let reads = 0;
    let writes = 0;
    const committedChange = async () => {
      writes += 1;
      active = !initialActive;
      throw new ApiError(statusCode, "响应丢失，操作结果待确认", null);
    };
    global.wx = { setNavigationBarTitle() {} };
    const { page } = loadProjectEditPage({
      project,
      getMemoirBookShareStatus: async () => { reads += 1; return { active }; },
      updateMemoirBookShare: committedChange,
      revokeMemoirBookShare: committedChange,
    });
    const context = pageContext(page);
    await context.onLoad({ projectId: project.id });
    await context.toggleBookShare({ detail: { value: !initialActive } });
    assert.equal(context.data.bookShareEnabled, active);
    assert.equal(context.data.bookShareState, "ready");
    assert.equal(context.data.bookShareBusy, false);
    assert.equal(reads, 2);
    assert.equal(writes, 1);
  }
});

test("an unconfirmed sharing change stays unknown until a successful status read", async () => {
  const project = { id: "project-1", title: "爷爷的回忆" };
  let active = false;
  let statusUnavailable = false;
  let writes = 0;
  global.wx = { setNavigationBarTitle() {} };
  const { page } = loadProjectEditPage({
    project,
    getMemoirBookShareStatus: async () => {
      if (statusUnavailable) throw new ApiError(0, "网络连接失败", null);
      return { active };
    },
    updateMemoirBookShare: async () => {
      writes += 1;
      active = true;
      statusUnavailable = true;
      throw new ApiError(0, "请求超时，操作结果可能仍在处理中", null);
    },
    revokeMemoirBookShare: () => assert.fail("an unknown result must not be reversed by another write"),
  });
  const context = pageContext(page);
  await context.onLoad({ projectId: project.id });
  await context.toggleBookShare({ detail: { value: true } });
  assert.equal(context.data.bookShareState, "error");
  assert.equal(context.data.bookShareBusy, false);
  assert.ok(context.data.bookShareError);
  await context.toggleBookShare({ detail: { value: false } });
  assert.equal(writes, 1);
  statusUnavailable = false;
  await context.loadBookShareSetting();
  assert.equal(context.data.bookShareEnabled, true);
  assert.equal(context.data.bookShareState, "ready");
  assert.equal(context.data.bookShareError, "");
});

test("a timed-out sharing write that is still pending keeps its notice until refreshed state matches the intent", async () => {
  for (const initialActive of [false, true]) {
    const project = { id: "project-1", title: "爷爷的回忆" };
    let active = initialActive;
    let writes = 0;
    let completeOriginal;
    const pendingChange = (enabled) => async () => {
      writes += 1;
      completeOriginal = () => { active = enabled; };
      throw new ApiError(0, "请求超时，操作结果可能仍在处理中", null);
    };
    global.wx = { setNavigationBarTitle() {} };
    const { page } = loadProjectEditPage({
      project,
      getMemoirBookShareStatus: async () => ({ active }),
      updateMemoirBookShare: pendingChange(true),
      revokeMemoirBookShare: pendingChange(false),
    });
    const context = pageContext(page);
    await context.onLoad({ projectId: project.id });
    await context.toggleBookShare({ detail: { value: !initialActive } });
    assert.equal(context.data.bookShareEnabled, initialActive);
    assert.equal(context.data.bookSharePendingTarget, !initialActive);
    assert.equal(writes, 1);
    await context.onShow();
    assert.equal(context.data.bookSharePendingTarget, !initialActive);
    assert.equal(writes, 1);
    completeOriginal();
    await context.loadBookShareSetting();
    assert.equal(context.data.bookShareEnabled, !initialActive);
    assert.equal(context.data.bookSharePendingTarget, null);
    assert.equal(writes, 1);
  }
});

test("a sharing request that never reached the server can be retried explicitly", async () => {
  const project = { id: "project-1", title: "爷爷的回忆" };
  let writes = 0;
  global.wx = { setNavigationBarTitle() {} };
  const { page } = loadProjectEditPage({
    project,
    updateMemoirBookShare: async () => {
      writes += 1;
      if (writes === 1) throw new ApiError(0, "网络连接失败", null);
    },
  });
  const context = pageContext(page);
  await context.onLoad({ projectId: project.id });
  await context.toggleBookShare({ detail: { value: true } });
  assert.equal(context.data.bookShareEnabled, false);
  assert.equal(writes, 1);
  await context.toggleBookShare({ detail: { value: true } });
  assert.equal(context.data.bookShareEnabled, true);
  assert.equal(context.data.bookSharePendingTarget, null);
  assert.equal(writes, 2);
});

test("reopening sharing settings reads the server state without requiring local persistence", async () => {
  const project = { id: "project-1", title: "爷爷的回忆" };
  let active = false;
  global.wx = {
    setNavigationBarTitle() {},
    getStorageSync: () => undefined,
    setStorageSync: () => { throw new Error("storage full"); },
  };
  const { page } = loadProjectEditPage({
    project,
    getMemoirBookShareStatus: async () => ({ active }),
    updateMemoirBookShare: async () => { throw new ApiError(0, "响应超时", null); },
    revokeMemoirBookShare: async () => { active = false; },
  });
  const first = pageContext(page);
  await first.onLoad({ projectId: project.id });
  await first.toggleBookShare({ detail: { value: true } });
  assert.equal(first.data.bookSharePendingTarget, true);
  active = true; // The timed-out request committed after leaving the first page.
  const reopened = pageContext(page);
  await reopened.onLoad({ projectId: project.id });
  assert.equal(reopened.data.bookShareEnabled, true);
  assert.equal(reopened.data.bookSharePendingTarget, null);
  await reopened.toggleBookShare({ detail: { value: false } });
  assert.equal(active, false);
  assert.equal(reopened.data.bookShareEnabled, false);
});

test("an unavailable or unknown share status cannot grant access, and new memoirs do not request sharing", async () => {
  const project = { id: "project-1", title: "爷爷的回忆" };
  let reads = 0;
  let failure = new Error("网络不可用");
  global.wx = { setNavigationBarTitle() {} };
  const { page } = loadProjectEditPage({
    project,
    getMemoirBookShareStatus: async () => { reads += 1; throw failure; },
    updateMemoirBookShare: () => assert.fail("unknown authorization cannot be changed"),
  });
  const context = pageContext(page);
  await context.onLoad({ projectId: project.id });
  assert.equal(context.data.title, project.title);
  assert.equal(context.data.bookShareState, "error");
  assert.match(context.data.bookShareError, /网络不可用/);
  await context.toggleBookShare({ detail: { value: true } });
  failure = new ApiError(404, "全书稿不存在", null);
  await context.loadBookShareSetting();
  assert.equal(context.data.bookShareState, "unavailable");
  assert.equal(context.data.bookShareEnabled, false);
  await context.toggleBookShare({ detail: { value: true } });
  const fresh = pageContext(page);
  await fresh.onLoad();
  fresh.onShow();
  assert.equal(reads, 2);
});

test("project creation retries activation without creating a duplicate", async () => {
  const destinations = [];
  const project = { id: "project-1", title: "第一版" };
  let creates = 0;
  let activations = 0;
  const updates = [];
  const createdInputs = [];
  global.wx = {
    navigateBack() { assert.fail("a new memoir should return home"); },
    reLaunch({url}) { destinations.push(url); },
    setNavigationBarTitle() {},
    showToast() {},
  };
  const { page } = loadProjectEditPage({
    project,
    createMemoirProject: async (input) => {
      creates += 1;
      createdInputs.push(input);
      return project;
    },
    activateMemoirProject: async () => {
      activations += 1;
      if (activations === 1) throw new Error("激活失败");
    },
    updateMemoirProject: async (id, input) => {
      updates.push({ id, input });
      return { ...project, title: input.title };
    },
  });
  const context = pageContext(page, { title: "第一版" });

  await context.saveProject();
  assert.equal(creates, 1);
  assert.equal(createdInputs[0].share_video_style, "illustrated");
  assert.equal(createdInputs[0].transcript_polish_level, "high");
  assert.match(context.data.error, /已创建.*激活失败/);
  assert.deepEqual(destinations, []);

  context.changeTitle({ detail: { value: "第二版" } });
  await context.saveProject();
  assert.equal(creates, 1);
  assert.equal(activations, 2);
  assert.deepEqual(updates.map(({ id, input }) => [id, input.title]), [["project-1", "第二版"]]);
  assert.equal(context.pendingCreatedProject, null);
  assert.deepEqual(destinations, ["/pages/home/index"]);
});
