require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  loadPageModule,
  mockModule,
  pageContext,
} = require("./page-harness");

function loadProjectEditPage({ project, updateMemoirProject }) {
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
    activateMemoirProject: async () => {},
    createMemoirProject: async () => project,
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
  assert.deepEqual(context.data.narrationOptions.map(option => option.value), [
    "free_narration",
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
  await context.saveProject();

  assert.equal(updates.length, 1);
  assert.equal(updates[0].projectId, project.id);
  assert.equal(updates[0].payload.elder_name, "爷爷");
  assert.equal(updates[0].payload.default_guidance_mode, "guided_interview");
  assert.equal(updates[0].payload.default_interview_type, "ai_interview");
  assert.equal(updates[0].payload.default_session_goal, "book_completion");
  assert.equal(updates[0].payload.transcript_polish_level, "medium");
});
