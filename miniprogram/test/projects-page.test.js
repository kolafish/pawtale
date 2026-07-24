require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { loadPageModule, mockModule, pageContext } = require("./page-harness");

function project(id, title) {
  return {
    id,
    title,
    metadata_json: {},
    total_word_count: 0,
    created_at: "2026-07-20T00:00:00.000Z",
    updated_at: "2026-07-20T00:00:00.000Z",
    last_opened_at: "2026-07-20T00:00:00.000Z",
  };
}

function loadProjectsPage({ memoirExports = {}, authExports = {} } = {}) {
  mockModule("../services/auth.ts", {
    getMemoirIdentity: async () => ({}),
    ...authExports,
  });
  mockModule("../services/audit-tracker.ts", {
    trackAuditAction() {},
  });
  mockModule("../services/memoir-project-profile.ts", {
    projectProfileSummary: () => "资料已填写",
  });
  mockModule("../services/memoir.ts", {
    activateMemoirProject: async () => {},
    deleteMemoirProject: async () => {},
    listMemoirProjects: async () => ({
      active_project_id: "project-1",
      projects: [project("project-1", "外婆回忆录"), project("project-2", "爷爷回忆录")],
    }),
    ...memoirExports,
  });

  return loadPageModule("../pages/projects/index.ts");
}

function projectItem(id, title, { active = false, canDelete = true } = {}) {
  return {
    ...project(id, title),
    active,
    canDelete,
    profileSummary: "资料已填写",
    updatedText: "2026-07-20",
    wordCountText: "0 字",
  };
}

test("new memoir action stays inactive while another project action is busy", () => {
  const navigations = [];
  global.wx = {
    navigateTo(options) {
      navigations.push(options.url);
    },
  };
  const { page } = loadProjectsPage();
  const context = pageContext(page, { busy: true });

  page.createProject.call(context);

  assert.deepEqual(navigations, []);
  delete global.wx;
});

test("project cards switch the active memoir directly", async () => {
  const activations = [];
  const toasts = [];
  const { page } = loadProjectsPage({
    memoirExports: {
      activateMemoirProject: async (id) => {
        activations.push(id);
      },
      listMemoirProjects: async () => ({
        active_project_id: "project-2",
        projects: [project("project-1", "外婆回忆录"), project("project-2", "爷爷回忆录")],
      }),
    },
  });
  global.wx = {
    showToast(options) {
      toasts.push(options.title);
    },
  };
  const context = pageContext(page, {
    activeProjectId: "project-1",
    projects: [
      projectItem("project-1", "外婆回忆录", { active: true }),
      projectItem("project-2", "爷爷回忆录"),
    ],
  });

  await page.activateProject.call(context, {
    currentTarget: { dataset: { id: "project-2" } },
  });

  assert.deepEqual(activations, ["project-2"]);
  assert.equal(context.data.activeProjectId, "project-2");
  assert.deepEqual(toasts, ["已切换"]);
  assert.equal(context.data.busy, false);
  delete global.wx;
});

test("tapping the current project card does not repeat activation", async () => {
  let activations = 0;
  const { page } = loadProjectsPage({
    memoirExports: {
      activateMemoirProject: async () => {
        activations += 1;
      },
    },
  });
  const context = pageContext(page, {
    activeProjectId: "project-1",
    projects: [projectItem("project-1", "外婆回忆录", { active: true })],
  });

  await page.activateProject.call(context, {
    currentTarget: { dataset: { id: "project-1" } },
  });

  assert.equal(activations, 0);
  assert.equal(context.data.busy, false);
});

test("project deletion stays behind the more menu and confirmation", async () => {
  const deleted = [];
  const dialogs = [];
  const { page } = loadProjectsPage({
    memoirExports: {
      deleteMemoirProject: async (id) => {
        deleted.push(id);
      },
      listMemoirProjects: async () => ({
        active_project_id: "project-1",
        projects: [project("project-1", "外婆回忆录")],
      }),
    },
  });
  global.wx = {
    showActionSheet(options) {
      dialogs.push("actions");
      options.success({ tapIndex: 0 });
    },
    showModal(options) {
      dialogs.push("confirm");
      options.success({ confirm: true, cancel: false });
    },
    showToast() {},
  };
  const context = pageContext(page, {
    activeProjectId: "project-1",
    projects: [
      projectItem("project-1", "外婆回忆录", { active: true }),
      projectItem("project-2", "爷爷回忆录"),
    ],
  });

  await page.openProjectMore.call(context, {
    currentTarget: { dataset: { id: "project-2" } },
  });

  assert.deepEqual(dialogs, ["actions", "confirm"]);
  assert.deepEqual(deleted, ["project-2"]);
  assert.equal(context.data.deletingId, "");
  assert.equal(context.data.busy, false);
  delete global.wx;
});
