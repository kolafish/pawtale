const assert = require("node:assert/strict");
const path = require("node:path");
const { after, before, test } = require("node:test");
const {
  captureStorageEntries,
  launchE2ESession,
  restoreStorageEntries,
  shutdownE2ESession,
} = require("../support/session");
const { sleep, waitForCurrentPage } = require("../support/wait");

let session;
let homePage;
let failed = false;
let originalRuntimeStorage = [];

const RUNTIME_STORAGE_KEYS = [
  "pawtale_backend_config",
  "pawtale_backend_cookie",
  "pawtale_home_launch_state",
  "pawtale_memoir_session",
];

function baseState() {
  return {
    loading: false, creatingVoice: false, voiceEntry: null, needsLogin: false,
    error: "", userId: "visual-user", displayName: "Kola", projectId: "visual-project",
    hasProject: true, greetingText: "你好，Kola", voiceStartDisabled: false, primaryVoiceButtonTitle: "开始讲述",
    voiceAuthorizationAgreed: true, voiceAuthorizationRequested: false, showVoiceAuthorizationPanel: false,
    largeTextEnabled: false, homePageClass: "home-page", storyLoaded: true, storyError: "", notice: null,
    shareCandidate: null, shareVideoTotal: 0, shareVideoActiveCount: 0, shareVideoFailedCount: 0,
    story: {
      memoir: { title: "Kola 的回忆录", bookMetaText: "5 章 · 6184 字", bookProgressText: "5 章 · 17 小节 · 6184 字" },
      overview: { title: "Kola 的回忆录", summaryText: "5 章 · 17 小节", hasChapters: true, themeTags: [], growthAnimating: false },
      chapters: [],
    },
  };
}

function stateFixtures() {
  const base = baseState();
  return [
    { name: "populated", data: base },
    { name: "logged-out", data: { ...base, needsLogin: true, userId: "", displayName: "", projectId: "", hasProject: false, greetingText: "你好" } },
    { name: "authorization-requested", data: { ...base, voiceAuthorizationAgreed: false, voiceAuthorizationRequested: true, showVoiceAuthorizationPanel: true } },
    { name: "first-load-failure", data: { ...base, voiceStartDisabled: true, storyLoaded: false, hasProject: false, projectId: "", error: "网络暂不可用，请重试" }, retry: true },
    { name: "cached-refresh-failure", data: { ...base, storyError: "进度暂未刷新，已保存的讲述和故事仍在，稍后会再试。" }, retry: true },
    { name: "video-recommendation", data: { ...base, shareCandidate: { id: "visual-candidate", status: "ready" }, shareVideoTotal: 3 } },
    { name: "large-text-long-copy", data: {
      ...base, largeTextEnabled: true, homePageClass: "home-page large-text",
      greetingText: "你好，一位名字稍微长一些的讲述者",
      story: { ...base.story, memoir: { ...base.story.memoir, title: "小时候在故乡和家人一起生活的漫长回忆" } },
    } },
  ];
}

async function renderFixture(fixture) {
  await session.miniProgram.pageScrollTo(0);
  await homePage.setData(fixture.data);
  await sleep(500);
  const hero = await homePage.$("#home-voice");
  const primary = await homePage.$("#primary-voice-action");
  const memoir = await homePage.$("#home-memoir");
  assert.ok(hero, `${fixture.name} 应渲染讲述卡片`);
  assert.ok(primary, `${fixture.name} 应渲染开始讲述按钮`);
  assert.equal(Boolean(memoir), fixture.data.storyLoaded, `${fixture.name} 回忆录入口应跟随数据加载状态`);
  if (fixture.retry) assert.ok(await homePage.$(".home-story-action"), `${fixture.name} 应提供重试入口`);
  const systemInfo = await session.miniProgram.systemInfo();
  const pageSize = await homePage.size();
  const heroSize = await hero.size();
  const primarySize = await primary.size();
  const pixels = value => Number.parseFloat(String(value));
  assert.ok(pixels(pageSize.width) <= pixels(systemInfo.windowWidth) + 1, `${fixture.name} 不应横向溢出`);
  assert.ok(pixels(heroSize.width) > pixels(systemInfo.windowWidth) * 0.8, `${fixture.name} 讲述卡片应占据主视觉宽度`);
  assert.ok(pixels(primarySize.height) >= 40, `${fixture.name} 主按钮点击区域不小于 40px`);
  await session.miniProgram.screenshot({ path: path.join(session.artifacts.runPath, `home-${fixture.name}-top.png`) });
}

before(async () => {
  session = await launchE2ESession({
    deferRuntimePreparation: true,
    initialPage: "/pages/login/index",
    requireAccount: false,
  });
  originalRuntimeStorage = await captureStorageEntries(
    session.miniProgram,
    RUNTIME_STORAGE_KEYS,
  );
  await session.miniProgram.callWxMethod("setStorageSync", "pawtale_backend_config", {
    httpBaseUrl: "http://127.0.0.1:9",
    wsBaseUrl: "ws://127.0.0.1:9",
  });
  await session.miniProgram.callWxMethod("removeStorageSync", "pawtale_backend_cookie");
  await session.miniProgram.callWxMethod("removeStorageSync", "pawtale_memoir_session");
  homePage = await session.miniProgram.reLaunch("/pages/home/index");
  if (!homePage) homePage = await waitForCurrentPage(session.miniProgram, "pages/home/index");
  await homePage.callMethod("onHide");
});

after(async () => {
  if (!session) return;
  let teardownError = null;
  try {
    await restoreStorageEntries(session.miniProgram, originalRuntimeStorage);
  } catch (error) {
    teardownError = error;
  }
  try {
    await shutdownE2ESession(session);
  } catch (error) {
    teardownError = teardownError
      ? new AggregateError([teardownError, error], "视觉测试存储恢复与开发者工具清理均失败")
      : error;
  }
  await session.artifacts.finish({
    failed: failed || Boolean(teardownError),
    target: "devtools-home-visual-matrix",
    teardownError,
  });
  if (teardownError) throw teardownError;
});

test("home renders the complete account and story-state visual matrix", async () => {
  try {
    for (const fixture of stateFixtures()) await renderFixture(fixture);
  } catch (error) {
    failed = true;
    await session.artifacts.captureFailure(session.miniProgram, error);
    throw error;
  }
});
