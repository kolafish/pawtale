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

function hasExactNamedElement(source, name) {
  return source.includes(`name="${name}"`) || source.includes(`label="${name}"`);
}

function isWechatDiscoverSource(source) {
  return ["发现", "小程序", "朋友圈"].every(
    (name) => hasExactNamedElement(source, name),
  );
}

function maskedValuePresent(value, placeholder) {
  const normalized = String(value || "").trim();
  return Boolean(normalized && normalized !== placeholder);
}

function isDeleteConfirmationSource(source) {
  return source.includes('name="删除回忆录"')
    && source.includes('name="取消"')
    && source.includes('name="删除"');
}

function loginSourceState(source) {
  if (source.includes("首页") && !source.includes("欢迎登录")) {
    return "complete";
  }
  if (
    source.includes("邮箱或密码不正确")
    || source.includes("请输入正确的邮箱地址")
    || source.includes("请输入邮箱和密码")
  ) {
    return "rejected";
  }
  if (source.includes("登录中")) {
    return "submitting";
  }
  return "unchanged";
}

async function waitForSource(
  client,
  predicate,
  description,
  timeoutMs = 20000,
  sourceTimeoutMs = 30000,
) {
  return waitForValue(
    () => client.source(sourceTimeoutMs),
    predicate,
    {
      timeoutMs,
      intervalMs: 500,
      description,
      describeValue: () => "页面未满足预期",
    },
  );
}

async function hideIOSKeyboard(client, description) {
  if (await client.isKeyboardShown()) {
    const active = await client.activeElement();
    await client.mobileKeys(active, ["\r"]);
  }
  await waitForValue(
    () => client.isKeyboardShown(),
    (shown) => shown === false,
    {
      timeoutMs: 10000,
      intervalMs: 250,
      description,
      describeValue: (shown) => (shown ? "键盘仍显示" : "键盘已收起"),
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
  await client.clickElement(selected.element);
  return selected.rect;
}

async function tapLargestNamedElementAndWait(client, name, options) {
  const acknowledgementPredicate = options.acknowledgementPredicate
    || options.finalPredicate;
  const acknowledgementDescription = `${options.description}动作生效`;
  const tryAcknowledgement = async () => {
    try {
      return await waitForSource(
        client,
        acknowledgementPredicate,
        acknowledgementDescription,
        options.acknowledgementTimeoutMs ?? 5000,
        options.acknowledgementSourceTimeoutMs,
      );
    } catch (error) {
      if (!String(error?.message || "").startsWith(`等待${acknowledgementDescription}超时`)) {
        throw error;
      }
      return null;
    }
  };

  let selected = await largestElement(client, "accessibility id", name);
  await client.clickElement(selected.element);
  let source = await tryAcknowledgement();
  if (source === null) {
    source = await client.source(options.guardSourceTimeoutMs);
    if (options.guardPredicate && !options.guardPredicate(source)) {
      throw new Error(`${options.description}点击后页面状态未知；为避免误触，未执行第二次点击`);
    }
    if (options.requireKeyboardHidden && await client.isKeyboardShown()) {
      throw new Error(`${options.description}点击未生效，且 iOS 键盘意外重新出现`);
    }
    selected = await largestElement(client, "accessibility id", name);
    await client.tapElementCenter(selected.element);
    options.onPointerFallback?.();
    source = await waitForSource(
      client,
      acknowledgementPredicate,
      acknowledgementDescription,
      options.acknowledgementTimeoutMs ?? 5000,
      options.acknowledgementSourceTimeoutMs,
    );
  }
  if (options.finalPredicate(source)) {
    return source;
  }
  return waitForSource(
    client,
    options.finalPredicate,
    options.description,
    options.finalTimeoutMs ?? 30000,
    options.finalSourceTimeoutMs,
  );
}

async function tapCanonicalPoint(client, x, y) {
  const rect = await client.windowRect();
  await client.tap(
    rect.x + (x / CANONICAL_SCREEN.width) * rect.width,
    rect.y + (y / CANONICAL_SCREEN.height) * rect.height,
  );
}

async function returnToPreviousPage(client, previousSource, options = {}) {
  try {
    return await tapLargestNamedElementAndWait(client, "返回", {
      description: "返回上一页",
      acknowledgementTimeoutMs: options.acknowledgementTimeoutMs,
      acknowledgementSourceTimeoutMs: options.sourceTimeoutMs,
      finalPredicate: (value) => value !== previousSource,
      guardSourceTimeoutMs: options.sourceTimeoutMs,
      guardPredicate: (value) => value.includes("返回"),
      finalSourceTimeoutMs: options.sourceTimeoutMs,
    });
  } catch (error) {
    if (!String(error?.message || "").startsWith("等待返回上一页动作生效超时")) {
      throw error;
    }
  }
  const source = await client.source(options.sourceTimeoutMs);
  if (!source.includes("返回")) {
    throw new Error("返回点击未生效，页面已变化；为避免误触，未执行边缘手势");
  }
  const window = await client.windowRect();
  const y = window.y + window.height * 0.5;
  await client.swipe(
    window.x + 5,
    y,
    window.x + window.width * 0.75,
    y,
    500,
  );
  return waitForSource(
    client,
    (value) => value !== source,
    "iOS 左边缘返回手势",
    options.gestureTimeoutMs ?? 10000,
    options.sourceTimeoutMs,
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
    const source = await client.source(90000);
    if (isPawtaleSource(source)) {
      return "pawtale";
    }
    if (isWechatDiscoverSource(source)) {
      return "discover";
    }
    if (source.length < 1200 && source.includes("bundleId=\"com.tencent.xin\"")) {
      return "miniprogram-list";
    }
    if (source.includes("保留当前内容?") || containsAll(source, ["取消", "发表"])) {
      throw new Error("微信正在编辑内容；请先手动保存或退出编辑页，再运行真机测试");
    }
    if (
      source.includes("新建回忆录")
      && source.includes("删除回忆录")
      && source.includes("取消")
    ) {
      const previousSource = source;
      await tapLargestNamedElementAndWait(client, "取消", {
        description: "取消遗留的回忆录删除弹窗",
        finalPredicate: (value) => value !== previousSource,
        guardPredicate: (value) => (
          value.includes("新建回忆录")
          && value.includes("删除回忆录")
          && value.includes("取消")
        ),
      });
      continue;
    }
    if (source.includes("发现")) {
      await tapLargestNamedElement(client, "发现");
      await sleep(1000);
      continue;
    }
    if (source.includes("返回")) {
      await returnToPreviousPage(client, source, { sourceTimeoutMs: 90000 });
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

async function readActiveElement(client) {
  try {
    return await client.activeElement();
  } catch (error) {
    if (error?.appiumError === "no such element") {
      return null;
    }
    throw error;
  }
}

async function focusTextField(client, field, placeholder, options = {}) {
  const waitForFocus = async (target) => {
    await waitForValue(
      async () => {
        const active = await readActiveElement(client);
        if (!active) {
          return false;
        }
        if (client.elementId(active) === client.elementId(target)) {
          return true;
        }
        return String(await client.elementAttribute(active, "placeholderValue")) === placeholder;
      },
      (matched) => matched,
      {
        timeoutMs: options.focusTimeoutMs ?? 5000,
        intervalMs: options.focusIntervalMs ?? 250,
        description: `输入框焦点：${placeholder}`,
        describeValue: () => "尚未获得焦点",
      },
    );
  };

  await client.clickElement(field);
  try {
    await waitForFocus(field);
    return field;
  } catch (error) {
    if (!String(error?.message || "").startsWith("等待输入框焦点：")) {
      throw error;
    }
    if (options.pointerFallback === false) {
      throw new Error(`输入框焦点校验失败：${placeholder}`, { cause: error });
    }
  }

  const freshField = await findTextField(client, placeholder);
  await client.tapElementCenter(freshField);
  try {
    await waitForFocus(freshField);
    return freshField;
  } catch (error) {
    if (String(error?.message || "").startsWith("等待输入框焦点：")) {
      throw new Error(`输入框焦点校验失败：${placeholder}`, { cause: error });
    }
    throw error;
  }
}

async function waitForMaskedTextField(client, placeholder, text, options = {}) {
  const timeoutMs = options.biometricTimeoutMs ?? 45000;
  const intervalMs = options.biometricIntervalMs ?? 500;
  const deadline = Date.now() + timeoutMs;
  const announce = options.onBiometricWait || (() => {
    console.log(
      `密码未立即写入；若 iPhone 显示 Face ID，请在 ${Math.ceil(timeoutMs / 1000)} 秒内完成`,
    );
  });
  announce();
  while (Date.now() < deadline) {
    let field;
    try {
      field = await findTextField(client, placeholder);
    } catch (error) {
      if (!String(error?.message || "").startsWith("未找到输入框：")) {
        throw error;
      }
      await sleep(intervalMs);
      continue;
    }
    const actual = String(await client.elementAttribute(field, "value"));
    if (maskedValuePresent(actual, placeholder)) {
      return;
    }
    const active = await readActiveElement(client);
    if (active && client.elementId(active) === client.elementId(field)) {
      await client.typeElement(field, text);
      await sleep(300);
    }
    await sleep(intervalMs);
  }
  throw new Error(
    "等待 iPhone Face ID 或密码自动填充结束超时；"
    + "可及时完成面容识别，或在“设置 → 通用 → 自动填充与密码”"
    + "关闭“自动填充密码和通行密钥”后重试",
  );
}

async function replaceTextField(client, placeholder, text, options = {}) {
  let field = await findTextField(client, placeholder);
  field = await focusTextField(client, field, placeholder);
  await client.clearElement(field);
  field = await findTextField(client, placeholder);
  field = await focusTextField(client, field, placeholder);
  await client.typeElement(field, text);
  await sleep(300);
  field = await findTextField(client, placeholder);
  const actual = String(await client.elementAttribute(field, "value"));
  if (options.masked) {
    if (!maskedValuePresent(actual, placeholder)) {
      await waitForMaskedTextField(client, placeholder, text, options);
    }
  } else if (actual !== text) {
    throw new Error(
      `输入框内容不完整（${placeholder}）：期望 ${text.length} 个字符，实际 ${actual.length} 个`,
    );
  }
}

async function assertTextFieldValue(client, placeholder, expected) {
  const field = await findTextField(client, placeholder);
  const actual = String(await client.elementAttribute(field, "value"));
  if (actual !== expected) {
    await client.clearElement(field);
    throw new Error(`输入密码后字段内容发生变化：${placeholder}`);
  }
}

async function waitForLoginSubmission(client, timeoutMs, intervalMs = 250) {
  try {
    const source = await waitForValue(
      () => client.source(),
      (value) => loginSourceState(value) !== "unchanged",
      {
        timeoutMs,
        intervalMs,
        description: "账号登录提交动作生效",
        describeValue: () => "登录表单未变化",
      },
    );
    return { acknowledged: true, source };
  } catch (error) {
    if (!String(error?.message || "").startsWith("等待账号登录提交动作生效超时")) {
      throw error;
    }
    return {
      acknowledged: false,
      source: await client.source(),
    };
  }
}

async function submitAccountLogin(client, artifacts, options = {}) {
  let loginButton = await largestElement(
    client,
    "-ios predicate string",
    "type == 'XCUIElementTypeButton' AND name == '账号登录'",
  );
  await client.clickElement(loginButton.element);
  artifacts.mark("ios-ui:login:submit:element-clicked");

  let result = await waitForLoginSubmission(
    client,
    options.acknowledgementTimeoutMs ?? 5000,
    options.intervalMs,
  );
  if (!result.acknowledged) {
    if (!containsAll(result.source, ["欢迎登录", "账号登录"])) {
      throw new Error("账号登录点击后页面状态未知；为避免误触，未执行第二次点击");
    }
    if (await client.isKeyboardShown()) {
      throw new Error("账号登录点击未生效，且 iOS 键盘意外重新出现；未执行坐标点击");
    }
    loginButton = await largestElement(
      client,
      "-ios predicate string",
      "type == 'XCUIElementTypeButton' AND name == '账号登录'",
    );
    await client.tapElementCenter(loginButton.element);
    artifacts.mark("ios-ui:login:submit:pointer-fallback");
    result = await waitForLoginSubmission(
      client,
      options.fallbackTimeoutMs ?? 5000,
      options.intervalMs,
    );
    if (!result.acknowledged) {
      throw new Error("账号登录按钮的元素点击和触控点击均未触发小程序提交事件");
    }
  }

  if (loginSourceState(result.source) === "submitting") {
    result.source = await waitForSource(
      client,
      (value) => ["complete", "rejected"].includes(loginSourceState(value)),
      "测试账号登录结果",
      options.loginTimeoutMs ?? 30000,
    );
  }
  return result.source;
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
  artifacts.mark("ios-ui:login:email-entered");
  await hideIOSKeyboard(client, "邮箱输入后的 iOS 键盘收起");
  await replaceTextField(client, "至少 8 位", config.account.password, { masked: true });
  artifacts.mark("ios-ui:login:password-entered");
  await assertTextFieldValue(client, "name@example.com", config.account.email);
  await hideIOSKeyboard(client, "密码输入后的 iOS 键盘收起");
  source = await submitAccountLogin(client, artifacts);
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
  focusTextField,
  hideIOSKeyboard,
  iosSessionCapabilities,
  isDeleteConfirmationSource,
  isPawtaleSource,
  isWechatDiscoverSource,
  largestElement,
  loginIfNeeded,
  loginSourceState,
  maskedValuePresent,
  openPawtale,
  predicateLiteral,
  readActiveElement,
  replaceTextField,
  returnToPreviousPage,
  runIOSUISmoke,
  submitAccountLogin,
  tapCanonicalPoint,
  tapLargestNamedElement,
  tapLargestNamedElementAndWait,
  verifyTab,
  waitForSource,
  waitForMaskedTextField,
};
