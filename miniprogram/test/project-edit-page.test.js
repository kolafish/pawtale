require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
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
}) {
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
