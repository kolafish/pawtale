const { execFileSync } = require("node:child_process");
const automator = require("./automator");
const { createArtifactRecorder } = require("./artifacts");
const { readE2EConfig } = require("./config");
const { findAvailablePort } = require("./port");
const {
  sleep,
  waitForCurrentPage,
  waitForCurrentPageReady,
  waitForPageData,
  waitForValue,
  withTimeout,
} = require("./wait");

const BACKEND_CONFIG_STORAGE_KEY = "pawtale_backend_config";
const BACKEND_COOKIE_STORAGE_KEY = "pawtale_backend_cookie";
const MEMOIR_SESSION_STORAGE_KEY = "pawtale_memoir_session";

async function elementById(page, id) {
  return waitForValue(
    () => page.$(`#${id}`),
    Boolean,
    {
      description: `页面 ${page.path} 渲染元素 #${id}`,
      timeoutMs: 10000,
    },
  );
}

async function elementByDataId(page, selector, id) {
  const elements = await page.$$(selector);
  for (const element of elements) {
    if (await element.attribute("data-id") === id) {
      return element;
    }
  }
  throw new Error(`页面 ${page.path} 的 ${selector} 中未找到 data-id=${id}`);
}

async function configureRuntime(miniProgram, config) {
  await miniProgram.callWxMethod("setStorageSync", BACKEND_CONFIG_STORAGE_KEY, {
    httpBaseUrl: config.httpBaseUrl,
    wsBaseUrl: config.wsBaseUrl,
  });
  await miniProgram.callWxMethod("removeStorageSync", BACKEND_COOKIE_STORAGE_KEY);
  await miniProgram.callWxMethod("removeStorageSync", MEMOIR_SESSION_STORAGE_KEY);
}

async function launchE2ESession() {
  const configured = readE2EConfig();
  const config = {
    ...configured,
    automationPort: configured.automationPort || await findAvailablePort(),
  };
  const artifacts = await createArtifactRecorder(config);
  let miniProgram;
  try {
    closeDeveloperToolsProject(config);
    miniProgram = await automator.launch({
      cliPath: config.cliPath,
      port: config.automationPort,
      projectPath: config.projectPath,
      timeout: 60000,
    });
    artifacts.attach(miniProgram);
    await configureRuntime(miniProgram, config);
    await waitForCurrentPageReady(miniProgram);
    return {
      artifacts,
      config,
      createdProjectIds: [],
      initialActiveProjectId: "",
      miniProgram,
    };
  } catch (error) {
    miniProgram?.disconnect();
    await artifacts.finish({
      failed: true,
      setupError: error,
      target: "devtools",
    });
    closeDeveloperToolsProject(config);
    throw error;
  }
}

function closeDeveloperToolsProject(config) {
  try {
    execFileSync(
      config.cliPath,
      ["close", "--project", config.projectPath],
      { stdio: "ignore", timeout: 30000 },
    );
  } catch {
    // The window may already be closed or Developer Tools may be exiting.
  }
}

async function shutdownE2ESession(session) {
  if (!session?.miniProgram) {
    return;
  }
  try {
    await withTimeout(session.miniProgram.close(), 8000, "关闭自动化窗口");
  } catch {
    session.miniProgram.disconnect();
    closeDeveloperToolsProject(session.config);
  }
}

async function loginWithTestAccount(session) {
  const { miniProgram, config } = session;
  let page = await miniProgram.reLaunch("/pages/login/index");
  if (!page) {
    page = await waitForCurrentPage(miniProgram, "pages/login/index");
  }
  await (await elementById(page, "account-login-toggle")).tap();
  await (await elementById(page, "account-email-input")).input(config.account.email);
  await (await elementById(page, "account-password-input")).input(config.account.password);
  await (await elementById(page, "account-login-action")).tap();
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const currentPage = await miniProgram.currentPage();
    if (currentPage?.path === "pages/home/index") {
      return currentPage;
    }
    if (currentPage?.path === "pages/login/index") {
      const data = await currentPage.data();
      if (data.error) {
        throw new Error(`测试账号登录失败：${data.error}`);
      }
    }
    await sleep(250);
  }
  throw new Error("测试账号登录后 30 秒内未进入首页");
}

async function openProjects(session) {
  let page = await session.miniProgram.navigateTo("/pages/projects/index");
  if (!page) {
    page = await waitForCurrentPage(session.miniProgram, "pages/projects/index");
  }
  await waitForPageData(
    page,
    (data) => data.loading === false,
    "回忆录列表加载完成",
    30000,
  );
  return page;
}

async function activateProject(page, id) {
  const data = await page.data();
  if (data.activeProjectId === id) {
    return;
  }
  const card = await elementByDataId(page, ".project-card", id);
  await card.tap();
  await waitForPageData(
    page,
    (next) => next.busy === false && next.activeProjectId === id,
    `激活回忆录 ${id}`,
    30000,
  );
}

async function deleteProject(page, id) {
  const data = await page.data();
  if (!data.projects.some((project) => project.id === id)) {
    return;
  }
  await page.callMethod("deleteProjectById", id);
  await waitForPageData(
    page,
    (next) => next.busy === false && !next.projects.some((project) => project.id === id),
    `删除回忆录 ${id}`,
    30000,
  );
}

async function cleanupSession(session) {
  const { miniProgram } = session;
  if (!miniProgram) {
    return;
  }
  try {
    await miniProgram.mockWxMethod("showModal", { confirm: true, cancel: false });
    const page = await openProjects(session);
    if (session.initialActiveProjectId) {
      const data = await page.data();
      if (data.projects.some((project) => project.id === session.initialActiveProjectId)) {
        await activateProject(page, session.initialActiveProjectId);
      }
    }
    for (const id of [...session.createdProjectIds].reverse()) {
      await deleteProject(page, id);
    }
  } finally {
    try {
      await miniProgram.restoreWxMethod("showModal");
    } catch {
      // The automation connection may already be closing.
    }
  }
}

module.exports = {
  activateProject,
  cleanupSession,
  deleteProject,
  elementByDataId,
  elementById,
  launchE2ESession,
  loginWithTestAccount,
  openProjects,
  shutdownE2ESession,
};
