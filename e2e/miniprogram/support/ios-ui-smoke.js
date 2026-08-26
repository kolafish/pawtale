const path = require("node:path");
const { sleep, waitForValue } = require("./wait");

const CANONICAL_SCREEN = { width: 430, height: 932 };
const RECENT_SLOT_X = [70, 161, 269, 376];

function predicateLiteral(value) {
  return `'${String(value).replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
}

function containsAll(source, values) {
  return values.every((value) => source.includes(value));
}

async function waitForSource(client, predicate, description, timeoutMs = 20000) {
  return waitForValue(
    () => client.source(),
    predicate,
    {
      timeoutMs,
      intervalMs: 500,
      description,
      describeValue: () => "页面未满足预期",
    },
  );
}

async function largestElement(client, using, value) {
  const elements = await client.findElements(using, value);
  if (elements.length === 0) {
    throw new Error(`未找到真机元素：${value}`);
  }
  const candidates = await Promise.all(elements.map(async (element) => ({
    element,
    rect: await client.elementRect(element),
  })));
  candidates.sort((left, right) => (
    (right.rect.width * right.rect.height) - (left.rect.width * left.rect.height)
  ));
  return candidates[0];
}

async function tapLargestNamedElement(client, name) {
  const selected = await largestElement(client, "accessibility id", name);
  await client.tap(
    selected.rect.x + selected.rect.width / 2,
    selected.rect.y + selected.rect.height / 2,
  );
  return selected.rect;
}

async function tapCanonicalPoint(client, x, y) {
  const rect = await client.windowRect();
  await client.tap(
    rect.x + (x / CANONICAL_SCREEN.width) * rect.width,
    rect.y + (y / CANONICAL_SCREEN.height) * rect.height,
  );
}

function isPawtaleSource(source) {
  const authenticatedShell = ["首页", "记录", "书稿", "我的"].every(
    (name) => source.includes(`name="${name}"`) || source.includes(`label="${name}"`),
  );
  return authenticatedShell || (
    source.includes("老余口述") && (
      source.includes("欢迎登录")
      || source.includes("登录后开始")
    )
  );
}

async function reachDiscoverPage(client) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const source = await client.source();
    if (isPawtaleSource(source)) {
      return "pawtale";
    }
    if (containsAll(source, ["发现", "小程序", "朋友圈"])) {
      return "discover";
    }
    if (source.length < 1200 && source.includes("bundleId=\"com.tencent.xin\"")) {
      return "miniprogram-list";
    }
    if (source.includes("保留当前内容?") || containsAll(source, ["取消", "发表"])) {
      throw new Error("微信正在编辑内容；请先手动保存或退出编辑页，再运行真机测试");
    }
    if (source.includes("发现")) {
      await tapLargestNamedElement(client, "发现");
      await sleep(1000);
      continue;
    }
    if (source.includes("返回")) {
      await tapLargestNamedElement(client, "返回");
      await sleep(1000);
      continue;
    }
    throw new Error(
      "无法从当前微信页面安全进入“发现”；请先回到微信主界面，或直接打开一次老余口述",
    );
  }
  throw new Error("连续返回后仍未到达微信“发现”页");
}

async function openPawtale(client, config, artifacts) {
  await client.activateApp(config.wechatBundleId);
  await sleep(1500);
  // WeChat's chat list can take over 30 seconds to produce a full accessibility
  // hierarchy. The bottom tab locations are stable, so enter Discover before
  // requesting source. If Pawtale is already open, this only switches its Book tab.
  await tapCanonicalPoint(client, 269, 888);
  artifacts.mark("ios-ui:wechat:discover-coordinate-tapped");
  await sleep(1000);
  const destination = await reachDiscoverPage(client);
  if (destination === "pawtale") {
    artifacts.mark("ios-ui:pawtale:already-open");
    return client.source();
  }
  if (destination === "discover") {
    artifacts.mark("ios-ui:wechat:discover");
    await tapLargestNamedElement(client, "小程序");
    await sleep(2000);
  }
  const slot = config.recentSlot - 1;
  await tapCanonicalPoint(client, RECENT_SLOT_X[slot], 253);
  try {
    const source = await waitForSource(
      client,
      isPawtaleSource,
      "老余口述启动",
      25000,
    );
    artifacts.mark("ios-ui:pawtale:opened-from-recents");
    return source;
  } catch (error) {
    throw new Error(
      `微信“最近使用”第 ${config.recentSlot} 个不是老余口述；`
      + "请先手动打开一次老余口述，或设置 PAWTALE_E2E_IOS_RECENT_SLOT",
      { cause: error },
    );
  }
}

async function findTextField(client, placeholder) {
  const predicate = [
    "type == 'XCUIElementTypeTextField'",
    `placeholderValue == ${predicateLiteral(placeholder)}`,
  ].join(" AND ");
  const elements = await client.findElements("-ios predicate string", predicate);
  if (elements.length === 0) {
    throw new Error(`未找到输入框：${placeholder}`);
  }
  return elements.at(-1);
}

async function replaceTextField(client, placeholder, text, options = {}) {
  let field = await findTextField(client, placeholder);
  const rect = await client.elementRect(field);
  await client.tap(rect.x + rect.width / 2, rect.y + rect.height / 2);
  await sleep(500);
  field = await findTextField(client, placeholder);
  await client.clearElement(field);
  for (const character of text) {
    await client.typeKeys(character);
    await sleep(options.characterDelayMs || 250);
  }
  await sleep(300);
  field = await findTextField(client, placeholder);
  const actual = String(await client.elementAttribute(field, "value"));
  if (options.masked) {
    if (actual.length !== text.length) {
      throw new Error(`密码输入不完整：期望 ${text.length} 个字符，实际 ${actual.length} 个`);
    }
  } else if (actual !== text) {
    throw new Error(`邮箱输入不完整：期望 ${text.length} 个字符，实际 ${actual.length} 个`);
  }
}

async function loginIfNeeded(client, config, artifacts, initialSource) {
  let source = initialSource;
  if (!source.includes("欢迎登录") && source.includes("登录后开始")) {
    await tapLargestNamedElement(client, "登录后开始");
    source = await waitForSource(
      client,
      (value) => value.includes("欢迎登录"),
      "账号登录页",
    );
  }
  if (!source.includes("欢迎登录")) {
    artifacts.mark("ios-ui:login:session-reused");
    return source;
  }
  if (!source.includes("name@example.com")) {
    await tapLargestNamedElement(client, "使用 Pawtale 账号登录");
    source = await waitForSource(
      client,
      (value) => value.includes("name@example.com") && value.includes("至少 8 位"),
      "Pawtale 账号表单",
    );
  }
  artifacts.mark("ios-ui:login:form-ready");
  await replaceTextField(client, "name@example.com", config.account.email);
  await replaceTextField(client, "至少 8 位", config.account.password, { masked: true });
  await tapLargestNamedElement(client, "欢迎登录");
  await waitForSource(
    client,
    (value) => !value.includes("inputView"),
    "iOS 键盘收起",
  );
  const loginButton = await largestElement(
    client,
    "-ios predicate string",
    "type == 'XCUIElementTypeButton' AND name == '账号登录'",
  );
  await client.tap(
    loginButton.rect.x + loginButton.rect.width / 2,
    loginButton.rect.y + loginButton.rect.height / 2,
  );
  source = await waitForSource(
    client,
    (value) => (
      (value.includes("首页") && !value.includes("欢迎登录"))
      || value.includes("邮箱或密码不正确")
      || value.includes("请输入正确的邮箱地址")
    ),
    "测试账号登录结果",
    30000,
  );
  if (source.includes("欢迎登录")) {
    throw new Error("测试账号登录被拒绝；凭据已通过本机配置读取，未写入产物");
  }
  artifacts.mark("ios-ui:login:complete");
  return source;
}

async function verifyTab(client, name, expectedMarkers, artifacts, screenshotName) {
  await tapLargestNamedElement(client, name);
  const source = await waitForSource(
    client,
    (value) => containsAll(value, expectedMarkers),
    `${name} Tab 内容`,
  );
  artifacts.mark(`ios-ui:tab:${name}:complete`);
  if (screenshotName) {
    await artifacts.screenshot(client, screenshotName, source);
  }
  return source;
}

async function runIOSUISmoke(client, config, artifacts) {
  let source = await openPawtale(client, config, artifacts);
  source = await loginIfNeeded(client, config, artifacts, source);
  if (!source.includes("首页")) {
    throw new Error("测试账号登录后未显示主 Tab");
  }
  await verifyTab(client, "记录", ["讲述记录"], artifacts, "records.png");
  await verifyTab(
    client,
    "书稿",
    ["书稿", "章节"],
    artifacts,
    "book.png",
  );
  const meSource = await verifyTab(
    client,
    "我的",
    ["管理回忆录、语音工具和个人偏好"],
    artifacts,
  );
  if (!meSource.includes(config.account.email)) {
    throw new Error("“我的”页未显示本机配置的测试账号");
  }
  const homeSource = await verifyTab(
    client,
    "首页",
    ["分享给家人"],
    artifacts,
    "home.png",
  );
  return {
    accountVerified: true,
    homeGreetingVisible: homeSource.includes("你好"),
    tabs: ["首页", "记录", "书稿", "我的"],
  };
}

function iosSessionCapabilities(config, tunnel) {
  return {
    platformName: "iOS",
    "appium:automationName": "XCUITest",
    "appium:udid": tunnel.udid,
    "appium:bundleId": config.wechatBundleId,
    "appium:xcodeOrgId": config.iosTeamId,
    "appium:xcodeSigningId": "Apple Development",
    "appium:updatedWDABundleId": config.wdaBundleId,
    "appium:noReset": true,
    "appium:useNewWDA": false,
    "appium:newCommandTimeout": 600,
    "appium:maxTypingFrequency": 10,
  };
}

function artifactPath(runPath, fileName) {
  return path.join(runPath, fileName);
}

module.exports = {
  CANONICAL_SCREEN,
  RECENT_SLOT_X,
  artifactPath,
  containsAll,
  findTextField,
  iosSessionCapabilities,
  isPawtaleSource,
  largestElement,
  loginIfNeeded,
  openPawtale,
  predicateLiteral,
  replaceTextField,
  runIOSUISmoke,
  tapCanonicalPoint,
  verifyTab,
  waitForSource,
};
