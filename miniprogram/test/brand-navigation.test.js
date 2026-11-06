require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const { loadComponentModule, componentContext, loadPageModule, pageContext } = require("./page-harness");
const { setPageNavigationTitle } = require("../services/page-navigation.ts");

function navigation({ home = false, large = false, stack = [{}], capsule } = {}) {
  global.wx = {
    getWindowInfo: () => ({ statusBarHeight: 47, windowWidth: 320 }),
    getMenuButtonBoundingClientRect: () => capsule || ({ top: 51, left: 226, height: 32 }),
    getStorageSync: () => ({ largeTextEnabled: large }),
    setStorageSync() {},
  };
  global.getCurrentPages = () => stack;
  const { component } = loadComponentModule("../components/brand-navigation/index.ts");
  const context = componentContext(component, { title: "语音采集与处理授权", home });
  context.createSelectorQuery = () => ({
    select: () => ({ boundingClientRect: callback => ({ exec: () => callback({ height: home ? 91 : 143 }) }) }),
  });
  component.lifetimes.attached.call(context);
  return { context, component };
}

test("navigation reserves the capsule and measures wrapped titles in large text mode", () => {
  const { context, component } = navigation({ large: true });
  assert.equal(context.data.capsuleSpace, 106);
  assert.equal(context.data.statusBarHeight, 47);
  assert.equal(context.data.navigationHeight, 143);
  assert.equal(context.data.largeText, true);
  assert.deepEqual(context.events.at(-1), { name: "layout", detail: { height: 143 } });
  global.wx.getWindowInfo = () => ({ statusBarHeight: 24, windowWidth: 430 });
  component.pageLifetimes.resize.call(context);
  assert.equal(context.data.statusBarHeight, 24);
  assert.equal(context.data.capsuleSpace, 216);
});

test("empty desktop capsule rectangles keep a usable navigation area", () => {
  const { context } = navigation({ home: true, capsule: { top: 0, left: 0, height: 0 } });
  assert.equal(context.data.brandRowHeight, 44);
  assert.equal(context.data.capsuleSpace, 104);
  assert.equal(context.data.navigationHeight, 91);
});

test("back returns one level; direct shared links and failed back recover to home", () => {
  const { context } = navigation({ stack: [{}, {}] });
  const calls = [];
  global.wx.navigateBack = options => { calls.push(options.delta); options.fail(); };
  global.wx.reLaunch = options => calls.push(options.url);
  context.goBack();
  assert.deepEqual(calls, [1, "/pages/home/index"]);
  calls.length = 0;
  global.getCurrentPages = () => [{}];
  context.goBack();
  assert.deepEqual(calls, ["/pages/home/index"]);
});

test("dynamic navigation titles reach page data even before the brand component attaches", () => {
  const changes = [];
  navigation({ stack: [{ setData: () => assert.fail("must not update a hidden page") }, { setData: data => changes.push(data) }] });
  setPageNavigationTitle("第一次离家");
  assert.deepEqual(changes, [{ navigationTitle: "第一次离家" }]);
});

test("every registered page supplies the shared navigation when native navigation is disabled", () => {
  const root = path.join(__dirname, "..");
  const app = JSON.parse(fs.readFileSync(path.join(root, "app.json"), "utf8"));
  assert.equal(app.window.navigationStyle, "custom");
  assert.equal(app.usingComponents["brand-navigation"], "/components/brand-navigation/index");
  for (const page of app.pages) {
    const template = fs.readFileSync(path.join(root, `${page}.wxml`), "utf8");
    assert.match(template, /<brand-navigation\b/, page);
    assert.match(template, /navigationTitle/, page);
    assert.match(template, /bindlayout="onNavigationLayout"/, page);
    assert.match(template, /<page-meta\b[^>]*navigationHeight/, page);
  }
});

test("measured navigation height reaches page-level sticky content without wrapping it", () => {
  const { page } = loadPageModule("../pages/voice-chat/index.ts");
  const context = pageContext(page);
  context.onNavigationLayout({ detail: { height: 172 } });
  assert.equal(context.data.navigationHeight, 172);
});
