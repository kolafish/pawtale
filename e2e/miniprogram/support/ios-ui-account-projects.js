const { sleep } = require("./wait");
const {
  largestElement,
  hideIOSKeyboard,
  isDeleteConfirmationSource,
  loginIfNeeded,
  replaceTextField,
  tapLargestNamedElement,
  tapLargestNamedElementAndWait,
  verifyTab,
  waitForSource,
} = require("./ios-ui-smoke");

const EXPECTED_PROJECT_PROFILE = {
  default_guidance_mode: "guided_interview",
  narrator_age: 72,
  narrator_origin: "iOS",
};

function projectCardActionPoint(window, card, action) {
  if (!["edit", "more"].includes(action)) {
    throw new Error(`未知回忆录卡片操作：${action}`);
  }
  const rpx = window.width / 750;
  const padding = 30 * rpx;
  const buttonHeight = 88 * rpx;
  const gap = 18 * rpx;
  const contentWidth = card.width - 2 * padding;
  const buttonWidth = (contentWidth - gap) / 2;
  return {
    x: action === "more"
      ? card.x + card.width - padding - buttonWidth / 2
      : card.x + padding + buttonWidth / 2,
    y: card.y + card.height - padding - buttonHeight / 2,
  };
}

async function tapProjectCardAction(client, cardName, action) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const window = await client.windowRect();
    const card = await largestElement(client, "accessibility id", cardName);
    const point = projectCardActionPoint(window, card.rect, action);
    const safeTop = window.y + 95;
    const safeBottom = window.y + window.height - 80;
    if (
      point.x >= card.rect.x
      && point.x <= card.rect.x + card.rect.width
      && point.y >= Math.max(card.rect.y, safeTop)
      && point.y <= Math.min(card.rect.y + card.rect.height, safeBottom)
    ) {
      await client.tap(point.x, point.y);
      return point;
    }
    const movingUp = point.y > safeBottom;
    await client.swipe(
      window.x + window.width / 2,
      window.y + window.height * (movingUp ? 0.78 : 0.32),
      window.x + window.width / 2,
      window.y + window.height * (movingUp ? 0.32 : 0.78),
    );
    await sleep(500);
  }
  throw new Error(`滚动后仍无法安全点击回忆录卡片操作：${action}`);
}

async function visibleNamedElement(client, name) {
  const window = await client.windowRect();
  const elements = await client.findElements("accessibility id", name);
  const candidates = [];
  for (const element of elements) {
    const rect = await client.elementRect(element);
    if (
      rect.width > 0
      && rect.height > 0
      && rect.y >= window.y + 95
      && rect.y + rect.height <= window.y + window.height - 80
    ) {
      candidates.push({ element, rect });
    }
  }
  candidates.sort((left, right) => (
    (right.rect.width * right.rect.height) - (left.rect.width * left.rect.height)
  ));
  return candidates[0] || null;
}

async function tapScrollableNamedElement(client, name) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const visible = await visibleNamedElement(client, name);
    if (visible) {
      await client.clickElement(visible.element);
      return;
    }
    const window = await client.windowRect();
    await client.swipe(
      window.x + window.width / 2,
      window.y + window.height * 0.78,
      window.x + window.width / 2,
      window.y + window.height * 0.32,
    );
    await sleep(500);
  }
  throw new Error(`滚动后仍未找到可见真机元素：${name}`);
}

async function logoutAndLogin(client, config, artifacts, source) {
  let current = source;
  if (!current.includes("退出登录")) {
    current = await verifyTab(
      client,
      "我的",
      ["管理回忆录、语音工具和个人偏好", "退出登录"],
      artifacts,
    );
  }
  artifacts.mark("ios-ui:logout:start");
  await tapScrollableNamedElement(client, "退出登录");
  current = await waitForSource(
    client,
    (value) => value.includes("前往登录") && !value.includes("退出登录"),
    "退出测试账号",
    30000,
  );
  artifacts.mark("ios-ui:logout:complete");
  await tapLargestNamedElement(client, "前往登录");
  current = await waitForSource(
    client,
    (value) => value.includes("欢迎登录"),
    "退出后的登录页",
  );
  current = await loginIfNeeded(client, config, artifacts, current);
  if (!current.includes("首页")) {
    throw new Error("真机重新登录后未显示主 Tab");
  }
  artifacts.mark("ios-ui:reauthentication:complete");
  return current;
}

async function openProjects(client, artifacts) {
  await verifyTab(
    client,
    "我的",
    ["管理回忆录、语音工具和个人偏好", "管理回忆录"],
    artifacts,
  );
  await tapLargestNamedElement(client, "管理回忆录");
  const source = await waitForSource(
    client,
    (value) => value.includes("新建回忆录") && value.includes("当前回忆录"),
    "回忆录管理页",
    30000,
  );
  artifacts.mark("ios-ui:projects:opened");
  return source;
}

async function cancelProjectCreation(client, fixture, artifacts) {
  artifacts.mark("ios-ui:project:cancel:start");
  await tapLargestNamedElementAndWait(client, "新建回忆录", {
    description: "打开新建回忆录表单",
    finalPredicate: (value) => (
      value.includes("例如：母亲的回忆") && value.includes("取消")
    ),
    guardPredicate: (value) => (
      value.includes("新建回忆录") && value.includes("当前回忆录")
    ),
    onPointerFallback: () => artifacts.mark("ios-ui:project:new:pointer-fallback"),
  });
  await replaceTextField(client, "例如：母亲的回忆", fixture.initialTitle);
  await hideIOSKeyboard(client, "取消新建前的 iOS 键盘收起");
  await tapLargestNamedElementAndWait(client, "取消", {
    description: "取消新建回忆录",
    finalPredicate: (value) => (
      value.includes("新建回忆录") && !value.includes(fixture.initialTitle)
    ),
    guardPredicate: (value) => (
      value.includes("例如：母亲的回忆") && value.includes(fixture.initialTitle)
    ),
    requireKeyboardHidden: true,
    onPointerFallback: () => artifacts.mark("ios-ui:project:cancel:pointer-fallback"),
  });
  const snapshot = await fixture.api.listProjects();
  if (snapshot.projects.some((project) => project.title === fixture.initialTitle)) {
    throw new Error("取消新建后，后端仍创建了测试回忆录");
  }
  artifacts.mark("ios-ui:project:cancel:complete");
}

async function createProject(client, fixture, artifacts) {
  artifacts.mark("ios-ui:project:create:start");
  await tapLargestNamedElement(client, "新建回忆录");
  await waitForSource(
    client,
    (value) => value.includes("例如：母亲的回忆") && value.includes("自己慢慢讲"),
    "新建回忆录表单",
  );
  await replaceTextField(client, "例如：母亲的回忆", fixture.initialTitle);
  await hideIOSKeyboard(client, "回忆录名称输入后的 iOS 键盘收起");
  await replaceTextField(client, "例如：四川成都", "iOS");
  await hideIOSKeyboard(client, "籍贯输入后的 iOS 键盘收起");
  await replaceTextField(client, "例如：72", "72");
  await hideIOSKeyboard(client, "保存新建前的 iOS 键盘收起");
  await tapLargestNamedElement(client, "小余陪着问");
  await tapLargestNamedElement(client, "保存");
  const source = await waitForSource(
    client,
    (value) => value.includes(`当前回忆录，${fixture.initialTitle}`),
    "新建回忆录保存结果",
    30000,
  );
  const project = await fixture.captureCreatedProject(fixture.initialTitle);
  await fixture.assertProject(
    project.id,
    fixture.initialTitle,
    true,
    EXPECTED_PROJECT_PROFILE,
  );
  artifacts.mark("ios-ui:project:create:complete");
  await artifacts.screenshot(client, "project-created.png", source);
  return { project, source };
}

async function renameProject(client, fixture, project, artifacts) {
  artifacts.mark("ios-ui:project:rename:start");
  await tapProjectCardAction(
    client,
    `当前回忆录，${fixture.initialTitle}`,
    "edit",
  );
  await waitForSource(
    client,
    (value) => value.includes(fixture.initialTitle) && value.includes("编辑回忆录"),
    "编辑回忆录表单",
    30000,
  );
  await replaceTextField(client, "例如：母亲的回忆", fixture.renamedTitle);
  await hideIOSKeyboard(client, "保存编辑前的 iOS 键盘收起");
  await tapLargestNamedElement(client, "保存");
  const source = await waitForSource(
    client,
    (value) => (
      value.includes(`当前回忆录，${fixture.renamedTitle}`)
      && !value.includes(fixture.initialTitle)
    ),
    "回忆录重命名结果",
    30000,
  );
  await fixture.assertProject(
    project.id,
    fixture.renamedTitle,
    true,
    EXPECTED_PROJECT_PROFILE,
  );
  artifacts.mark("ios-ui:project:rename:complete");
  return source;
}

async function switchProjects(client, fixture, project, baselineTitle, artifacts) {
  artifacts.mark("ios-ui:project:switch:start");
  await tapLargestNamedElement(client, `切换到回忆录，${baselineTitle}`);
  await waitForSource(
    client,
    (value) => value.includes(`当前回忆录，${baselineTitle}`),
    "切换到基线回忆录",
    30000,
  );
  await fixture.assertProject(project.id, fixture.renamedTitle, false);
  await tapLargestNamedElement(client, `切换到回忆录，${fixture.renamedTitle}`);
  await waitForSource(
    client,
    (value) => value.includes(`当前回忆录，${fixture.renamedTitle}`),
    "切换到测试回忆录",
    30000,
  );
  await fixture.assertProject(project.id, fixture.renamedTitle, true);
  await tapLargestNamedElement(client, `切换到回忆录，${baselineTitle}`);
  await waitForSource(
    client,
    (value) => value.includes(`当前回忆录，${baselineTitle}`),
    "恢复基线回忆录",
    30000,
  );
  await fixture.assertProject(fixture.initialActiveProjectId, baselineTitle, true);
  artifacts.mark("ios-ui:project:switch:complete");
}

async function deleteProject(client, fixture, project, artifacts) {
  artifacts.mark("ios-ui:project:delete:start");
  await tapProjectCardAction(
    client,
    `切换到回忆录，${fixture.renamedTitle}`,
    "more",
  );
  await waitForSource(
    client,
    (value) => value.includes("删除回忆录") && value.includes("取消"),
    "回忆录更多操作",
  );
  await tapLargestNamedElement(client, "删除回忆录");
  await waitForSource(
    client,
    isDeleteConfirmationSource,
    "删除回忆录确认框",
  );
  await tapLargestNamedElementAndWait(client, "删除", {
    description: "删除测试回忆录",
    acknowledgementPredicate: (value) => !isDeleteConfirmationSource(value),
    finalPredicate: (value) => (
      value.includes("新建回忆录") && !value.includes(fixture.renamedTitle)
    ),
    guardPredicate: isDeleteConfirmationSource,
    onPointerFallback: () => artifacts.mark("ios-ui:project:delete:pointer-fallback"),
  });
  await fixture.assertDeleted(project.id);
  artifacts.mark("ios-ui:project:delete:complete");
}

async function runIOSAccountProjectJourney(
  client,
  config,
  artifacts,
  fixture,
  initialSource,
) {
  let source = await logoutAndLogin(client, config, artifacts, initialSource);
  source = await openProjects(client, artifacts);
  const baseline = await fixture.api.listProjects();
  const baselineProject = baseline.projects.find(
    (project) => project.id === fixture.initialActiveProjectId,
  );
  if (!baselineProject || !source.includes(baselineProject.title)) {
    throw new Error("真机回忆录管理页未显示后端记录的基线回忆录");
  }
  await cancelProjectCreation(client, fixture, artifacts);
  const { project } = await createProject(client, fixture, artifacts);
  await renameProject(client, fixture, project, artifacts);
  await switchProjects(client, fixture, project, baselineProject.title, artifacts);
  await deleteProject(client, fixture, project, artifacts);
  return {
    accountReauthenticated: true,
    baselineRestored: true,
    projectJourney: ["cancel", "create", "rename", "switch", "delete"],
  };
}

module.exports = {
  projectCardActionPoint,
  runIOSAccountProjectJourney,
  tapProjectCardAction,
  tapScrollableNamedElement,
  visibleNamedElement,
};
