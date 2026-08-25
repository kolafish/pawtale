const assert = require("node:assert/strict");
const { after, before, test } = require("node:test");
const {
  activateProject,
  cleanupSession,
  elementByDataId,
  elementById,
  launchE2ESession,
  loginWithTestAccount,
  openProjects,
  shutdownE2ESession,
} = require("../support/session");
const { waitForCurrentPage, waitForPageData } = require("../support/wait");

let session;
let failed = false;

before(async () => {
  session = await launchE2ESession();
});

after(async () => {
  if (!session) {
    return;
  }
  try {
    await cleanupSession(session);
  } catch (error) {
    failed = true;
    await session.artifacts.captureFailure(session.miniProgram, error);
    throw error;
  } finally {
    await session.artifacts.finish({
      createdProjectIds: session.createdProjectIds,
      failed,
      target: "devtools",
    });
    await shutdownE2ESession(session);
  }
});

test("developer tools runtime opens the memoir mini program", async () => {
  try {
    const systemInfo = await session.miniProgram.systemInfo();
    assert.equal(systemInfo.platform, "devtools");

    const page = await session.miniProgram.reLaunch("/pages/login/index");
    assert.equal(page.path, "pages/login/index");
    assert.equal(
      (await (await elementById(page, "account-login-toggle")).text()).trim(),
      "使用 Pawtale 账号登录",
    );
  } catch (error) {
    failed = true;
    await session.artifacts.captureFailure(session.miniProgram, error);
    throw error;
  }
});

test("test account completes the project create, rename, switch, and delete journey", async () => {
  try {
    session.artifacts.mark("login:start");
    const homePage = await loginWithTestAccount(session);
    session.artifacts.mark("login:complete");
    assert.equal(homePage.path, "pages/home/index");

    for (const tabPath of [
      "/pages/transcripts/index",
      "/pages/book/index",
      "/pages/me/index",
      "/pages/home/index",
    ]) {
      session.artifacts.mark(`tab:${tabPath}:start`);
      const page = await session.miniProgram.switchTab(tabPath);
      assert.equal(page.path, tabPath.slice(1));
      session.artifacts.mark(`tab:${tabPath}:complete`);
    }

    session.artifacts.mark("projects:open:start");
    let projectsPage = await openProjects(session);
    session.artifacts.mark("projects:open:complete");
    const initial = await projectsPage.data();
    assert.ok(
      initial.projects.length > 0,
      "测试账号需要预先保留一本基线回忆录，自动化不会创建后无法安全删除的唯一回忆录",
    );
    session.initialActiveProjectId = initial.activeProjectId;

    session.artifacts.mark("project:create:start");
    await (await elementById(projectsPage, "create-project-action")).tap();
    let editPage = await waitForCurrentPage(session.miniProgram, "pages/project-edit/index");
    const runSuffix = `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    const initialTitle = `E2E-${runSuffix}`;
    const renamedTitle = `${initialTitle}-已重命名`;
    session.createdProjectTitles.push(initialTitle);
    await (await elementById(editPage, "project-title-input")).input(initialTitle);
    await (await elementById(editPage, "project-origin-input")).input("自动化测试");
    await (await elementById(editPage, "project-age-input")).input("72");
    await (await elementById(editPage, "project-save-action")).tap();

    projectsPage = await waitForCurrentPage(session.miniProgram, "pages/projects/index", 30000);
    const createdState = await waitForPageData(
      projectsPage,
      (data) => data.loading === false && data.projects.some((project) => project.title === initialTitle),
      "新建回忆录显示在列表",
      30000,
    );
    const createdProject = createdState.projects.find((project) => project.title === initialTitle);
    session.createdProjectIds.push(createdProject.id);
    session.artifacts.mark("project:create:complete");
    assert.equal(createdState.activeProjectId, createdProject.id);

    await (await elementByDataId(projectsPage, ".ghost-button", createdProject.id)).tap();
    session.artifacts.mark("project:rename:start");
    editPage = await waitForCurrentPage(session.miniProgram, "pages/project-edit/index");
    await waitForPageData(
      editPage,
      (data) => data.loading === false && data.projectId === createdProject.id,
      "编辑回忆录加载完成",
      30000,
    );
    await (await elementById(editPage, "project-title-input")).input(renamedTitle);
    await (await elementById(editPage, "project-save-action")).tap();

    projectsPage = await waitForCurrentPage(session.miniProgram, "pages/projects/index", 30000);
    await waitForPageData(
      projectsPage,
      (data) => data.loading === false
        && data.projects.some(
          (project) => project.id === createdProject.id && project.title === renamedTitle,
        ),
      "重命名结果显示在列表",
      30000,
    );
    session.artifacts.mark("project:rename:complete");

    session.artifacts.mark("project:switch:start");
    await activateProject(projectsPage, session.initialActiveProjectId);
    await activateProject(projectsPage, createdProject.id);
    await activateProject(projectsPage, session.initialActiveProjectId);
    session.artifacts.mark("project:switch:complete");

    session.artifacts.mark("project:delete:start");
    await session.miniProgram.mockWxMethod("showActionSheet", { tapIndex: 0 });
    await session.miniProgram.mockWxMethod("showModal", { confirm: true, cancel: false });
    try {
      await (await elementByDataId(projectsPage, ".more-button", createdProject.id)).tap();
      await waitForPageData(
        projectsPage,
        (data) => data.busy === false
          && !data.projects.some((project) => project.id === createdProject.id),
        "本轮创建的回忆录已删除",
        30000,
      );
      session.createdProjectIds = session.createdProjectIds.filter(
        (id) => id !== createdProject.id,
      );
      session.createdProjectTitles = session.createdProjectTitles.filter(
        (title) => title !== initialTitle,
      );
      session.artifacts.mark("project:delete:complete");
    } finally {
      await session.miniProgram.restoreWxMethod("showActionSheet");
      await session.miniProgram.restoreWxMethod("showModal");
    }
  } catch (error) {
    failed = true;
    await session.artifacts.captureFailure(session.miniProgram, error);
    throw error;
  }
});
