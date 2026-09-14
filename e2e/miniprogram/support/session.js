const { execFileSync } = require("node:child_process");
const automator = require("./automator");
const { createArtifactRecorder } = require("./artifacts");
const { readE2EConfig } = require("./config");
const { findAvailablePort } = require("./port");
const {
  isAutomatorResponseTimeout,
  readCurrentPage,
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
const RUNTIME_STORAGE_KEYS = [
  BACKEND_CONFIG_STORAGE_KEY,
  BACKEND_COOKIE_STORAGE_KEY,
  MEMOIR_SESSION_STORAGE_KEY,
];

async function captureStorageEntries(miniProgram, keys) {
  const info = await miniProgram.callWxMethod("getStorageInfoSync");
  const existingKeys = new Set(Array.isArray(info?.keys) ? info.keys : []);
  const entries = [];
  for (const key of keys) {
    entries.push({
      key,
      exists: existingKeys.has(key),
      value: existingKeys.has(key)
        ? await miniProgram.callWxMethod("getStorageSync", key)
        : undefined,
    });
  }
  return entries;
}

async function restoreStorageEntries(miniProgram, entries) {
  for (const entry of entries) {
    if (entry.exists) {
      await miniProgram.callWxMethod("setStorageSync", entry.key, entry.value);
    } else {
      await miniProgram.callWxMethod("removeStorageSync", entry.key);
    }
  }
}

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

async function prepareE2ERuntime(session) {
  const { artifacts, config, miniProgram } = session;
  const originalRuntimeStorage = await captureStorageEntries(
    miniProgram,
    RUNTIME_STORAGE_KEYS,
  );
  session.originalRuntimeStorage = originalRuntimeStorage;
  session.runtimeStoragePrepared = true;
  artifacts.mark("setup:runtime-storage-captured");
  try {
    await configureRuntime(miniProgram, config);
    artifacts.mark("setup:runtime-configured");
    try {
      await miniProgram.reLaunch("/pages/login/index");
    } catch (error) {
      if (!isAutomatorResponseTimeout(error)) {
        throw error;
      }
    }
    await waitForCurrentPage(miniProgram, "pages/login/index", 30000);
    artifacts.mark("setup:neutral-page-ready");
  } catch (error) {
    try {
      await restoreStorageEntries(miniProgram, originalRuntimeStorage);
      session.runtimeStoragePrepared = false;
    } catch (restoreError) {
      throw new AggregateError(
        [error, restoreError],
        "E2E 运行时配置失败，且原存储未能恢复",
      );
    }
    throw error;
  }
}

async function launchE2ESession(options = {}) {
  const configured = readE2EConfig();
  const config = {
    ...configured,
    automationPort: configured.automationPort || await findAvailablePort(),
  };
  const artifacts = await createArtifactRecorder(config);
  let miniProgram;
  let session;
  try {
    artifacts.mark("setup:window-close:start");
    closeDeveloperToolsProject(config);
    artifacts.mark("setup:window-close:complete");
    miniProgram = await automator.launch({
      cliPath: config.cliPath,
      port: config.automationPort,
      projectPath: config.projectPath,
      timeout: 60000,
    });
    artifacts.mark("setup:automator-launch:complete");
    artifacts.attach(miniProgram);
    if (options.initialPage) await miniProgram.callWxMethod("reLaunch", { url: options.initialPage });
    await waitForCurrentPageReady(miniProgram);
    artifacts.mark("setup:first-page-ready");
    session = {
      artifacts,
      config,
      createdProjectIds: [],
      createdProjectTitles: [],
      initialActiveProjectId: "",
      miniProgram,
      originalRuntimeStorage: [],
      runtimeStoragePrepared: false,
    };
    if (!options.deferRuntimePreparation) {
      await prepareE2ERuntime(session);
    }
    return session;
  } catch (error) {
    if (miniProgram && session?.runtimeStoragePrepared) {
      try {
        await restoreStorageEntries(miniProgram, session.originalRuntimeStorage);
      } catch {
        // Preserve the setup error; a closed automation connection cannot restore storage.
      }
    }
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
  let restoreError = null;
  if (session.runtimeStoragePrepared) {
    try {
      await restoreStorageEntries(
        session.miniProgram,
        session.originalRuntimeStorage || [],
      );
      session.artifacts.mark("teardown:runtime-storage-restored");
    } catch (error) {
      restoreError = error;
    }
  }
  try {
    await withTimeout(session.miniProgram.close(), 8000, "关闭自动化窗口");
  } catch {
    session.miniProgram.disconnect();
    closeDeveloperToolsProject(session.config);
  }
  if (restoreError) {
    throw restoreError;
  }
}

async function loginWithTestAccount(session) {
  const { miniProgram, config } = session;
  session.artifacts.mark("login:relaunch:start");
  let page = await miniProgram.reLaunch("/pages/login/index");
  if (!page) {
    page = await waitForCurrentPage(miniProgram, "pages/login/index");
  }
  session.artifacts.mark("login:relaunch:complete");
  await (await elementById(page, "account-login-toggle")).tap();
  session.artifacts.mark("login:account-form:visible");
  await (await elementById(page, "account-email-input")).input(config.account.email);
  session.artifacts.mark("login:email:complete");
  await (await elementById(page, "account-password-input")).input(config.account.password);
  session.artifacts.mark("login:password:complete");
  const loginAction = await elementById(page, "account-login-action");
  session.artifacts.mark("login:submit:start");
  await loginAction.tap();
  session.artifacts.mark("login:submit:complete");
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const currentPage = await readCurrentPage(miniProgram);
    if (currentPage?.path === "pages/home/index") {
      return currentPage;
    }
    if (currentPage?.path === "pages/login/index") {
      let data;
      try {
        data = await currentPage.data();
      } catch (error) {
        if (isAutomatorResponseTimeout(error)) {
          await sleep(250);
          continue;
        }
        throw error;
      }
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
    for (const title of [...session.createdProjectTitles].reverse()) {
      const data = await page.data();
      const project = data.projects.find((item) => item.title === title);
      if (project) {
        await deleteProject(page, project.id);
      }
    }
  } finally {
    try {
      await miniProgram.restoreWxMethod("showModal");
    } catch {
      // The automation connection may already be closing.
    }
  }
}

async function cleanupProjectsThroughApi(session) {
  const ids = new Set(session.createdProjectIds);
  const titles = new Set(session.createdProjectTitles);
  if (!ids.size && !titles.size) {
    return;
  }
  const { account, httpBaseUrl } = session.config;
  const login = await fetch(`${httpBaseUrl}/api/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(account),
  });
  if (!login.ok) {
    throw new Error(`API 清理登录失败：HTTP ${login.status}`);
  }
  const cookie = String(login.headers.get("set-cookie") || "").split(";")[0];
  if (!cookie) {
    throw new Error("API 清理登录未返回会话 cookie");
  }
  const headers = { Cookie: cookie, "content-type": "application/json" };
  const listProjects = async () => {
    const response = await fetch(`${httpBaseUrl}/api/mobile/memoir/projects`, { headers });
    if (!response.ok) {
      throw new Error(`API 清理读取回忆录失败：HTTP ${response.status}`);
    }
    return response.json();
  };
  const before = await listProjects();
  for (const project of before.projects) {
    if (titles.has(project.title)) {
      ids.add(project.id);
    }
  }
  if (
    session.initialActiveProjectId
    && before.active_project_id !== session.initialActiveProjectId
    && before.projects.some((project) => project.id === session.initialActiveProjectId)
  ) {
    const response = await fetch(
      `${httpBaseUrl}/api/mobile/memoir/projects/${encodeURIComponent(session.initialActiveProjectId)}/activate`,
      { method: "POST", headers, body: "{}" },
    );
    if (!response.ok) {
      throw new Error(`API 清理恢复原回忆录失败：HTTP ${response.status}`);
    }
  }
  for (const id of ids) {
    if (!before.projects.some((project) => project.id === id)) {
      continue;
    }
    const response = await fetch(
      `${httpBaseUrl}/api/mobile/memoir/projects/${encodeURIComponent(id)}`,
      { method: "DELETE", headers },
    );
    if (!response.ok) {
      throw new Error(`API 清理删除测试回忆录失败：HTTP ${response.status}`);
    }
  }
  const after = await listProjects();
  const residue = after.projects.filter(
    (project) => ids.has(project.id) || titles.has(project.title),
  );
  if (residue.length) {
    throw new Error(`API 清理后仍残留 ${residue.length} 本测试回忆录`);
  }
  if (
    session.initialActiveProjectId
    && after.projects.some((project) => project.id === session.initialActiveProjectId)
    && after.active_project_id !== session.initialActiveProjectId
  ) {
    throw new Error("API 清理后未恢复原活动回忆录");
  }
}

module.exports = {
  activateProject,
  captureStorageEntries,
  cleanupProjectsThroughApi,
  cleanupSession,
  deleteProject,
  elementByDataId,
  elementById,
  launchE2ESession,
  loginWithTestAccount,
  openProjects,
  prepareE2ERuntime,
  restoreStorageEntries,
  shutdownE2ESession,
};
