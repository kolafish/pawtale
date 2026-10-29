require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { componentContext, loadComponentModule } = require("./page-harness");

function setup(t, { celebrate = true, deferCanvas = false } = {}) {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1000 });
  const audioCalls = { played: 0, stopped: 0, options: [], contexts: [] };
  global.wx = {
    getWindowInfo: () => ({ pixelRatio: 2 }),
    setInnerAudioOption: options => audioCalls.options.push(options),
    createInnerAudioContext() {
      const audio = { onPlay() {}, onTimeUpdate() {}, onEnded() {}, onStop() {}, onError() {},
        play() { audioCalls.played++; }, stop() { audioCalls.stopped++; }, destroy() {} };
      audioCalls.contexts.push(audio); return audio;
    },
  };
  const { component } = loadComponentModule("../components/story-result/index.ts");
  const result = { id: "result-1", eventIds: ["one", "two"], titles: [
    { id: "s1", title: "第一次离家", label: "新增小节" },
    { id: "s2", title: "小时候的家", label: "补充" },
  ], wordDelta: 644, moreCount: 3, celebrate };
  const context = componentContext(component, { result });
  let intersect, canvasCallback;
  context.createIntersectionObserver = () => ({
    relativeToViewport() { return this; }, observe(_, callback) { intersect = callback; }, disconnect() {},
  });
  context.createSelectorQuery = () => ({ select: () => ({ fields: (_, callback) => {
    canvasCallback = callback; return { exec: () => { if (!deferCanvas) callback(null); } };
  } }) });
  component.lifetimes.ready.call(context);
  return { component, context, result, audioCalls,
    visible: (ratio = 1) => intersect({ intersectionRatio: ratio }),
    resolveCanvas: () => canvasCallback(null),
    advance(milliseconds) { for (let n = 0; n < milliseconds; n += 33) t.mock.timers.tick(33); },
    detach() { component.lifetimes.detached.call(context); } };
}

test("overlay waits until fully visible, holds one clear title and fades in and out gradually around a clear reading interval", t => {
  const h = setup(t);
  h.visible(.6); h.advance(7000);
  assert.deepEqual(h.context.events, []);
  assert.equal(h.audioCalls.played, 0);
  h.visible(); h.advance(800);
  assert.ok(h.context.data.opacity > .3 && h.context.data.opacity < .7);
  h.advance(1000);
  assert.equal(h.context.data.title, "第一次离家");
  assert.equal(h.context.data.opacity, 1);
  assert.equal(h.context.data.textOpacity, 1);
  assert.equal(h.audioCalls.played, 1);
  assert.deepEqual(h.audioCalls.options, [{ obeyMuteSwitch: true, mixWithOther: true }]);
  assert.equal(h.audioCalls.contexts[0].obeyMuteSwitch, true);
  assert.equal(h.audioCalls.contexts[0].volume, .22);
  h.advance(3500);
  assert.equal(h.context.data.count, 644);
  assert.equal(h.context.data.opacity, 1);
  assert.deepEqual(h.context.events, []);
  h.context.properties.result = { ...h.result };
  h.component.observers.result.call(h.context); // Polling the same result cannot restart it.
  h.advance(1700);
  assert.ok(h.context.data.opacity > .3 && h.context.data.opacity < .7);
  assert.deepEqual(h.context.events, []);
  h.advance(1200);
  assert.equal(h.context.data.opacity, 0);
  assert.deepEqual(h.context.events, [{ name: "complete", detail: { id: "result-1" } }]);
  h.visible(); h.advance(7000);
  assert.equal(h.context.events.length, 1);
  assert.equal(h.audioCalls.played, 1);
  h.detach();
});

test("background pauses reading and sound; returning never replays the chime", t => {
  const h = setup(t);
  h.visible(); h.advance(1500);
  h.component.pageLifetimes.hide.call(h.context);
  const count = h.context.data.count;
  assert.equal(h.audioCalls.stopped, 1);
  h.advance(10000);
  assert.equal(h.context.data.count, count);
  assert.deepEqual(h.context.events, []);
  h.component.pageLifetimes.show.call(h.context);
  h.advance(6700);
  assert.equal(h.audioCalls.played, 1);
  assert.equal(h.context.events.length, 1);
  h.detach();
});

test("delayed canvas callbacks and destroyed instances cannot consume results", t => {
  const h = setup(t, { deferCanvas: true });
  h.component.pageLifetimes.hide.call(h.context);
  h.resolveCanvas(); h.visible(); h.advance(7000);
  assert.deepEqual(h.context.events, []);
  h.component.pageLifetimes.show.call(h.context);
  h.advance(1800);
  h.detach(); h.resolveCanvas(); h.advance(7000);
  assert.deepEqual(h.context.events, []);
  assert.equal(h.audioCalls.stopped, 1);
});

test("long titles stay concise with their full text accessible; tiny edits are quiet", t => {
  const h = setup(t);
  const fullTitle = "年少时第一次独自离开家乡".repeat(10);
  h.context.properties.result = { ...h.result, id: "replacement", wordDelta: 3, celebrate: false,
    titles: [{ id: "long", label: "补充", title: fullTitle }] };
  h.component.observers.result.call(h.context);
  assert.ok(Array.from(h.context.data.title).length <= 14);
  assert.ok(h.context.data.summary.includes(fullTitle));
  h.visible(); h.advance(1000);
  assert.ok(h.context.data.count >= 0 && h.context.data.count <= 3);
  assert.deepEqual(h.context.events, []);
  h.advance(2400);
  assert.deepEqual(h.context.events, [{ name: "complete", detail: { id: "replacement" } }]);
  assert.equal(h.audioCalls.played, 0);
  h.detach();
});

test("render bridge delays cannot count as reading time or acknowledge before rendering", t => {
  const h = setup(t, { celebrate: false });
  let rendered;
  const setData = h.context.setData;
  h.context.setData = function(patch, callback) { setData.call(this, patch); rendered = callback; };
  h.visible(); h.advance(8000);
  assert.deepEqual(h.context.events, []);
  h.context.setData = setData;
  rendered(); h.advance(2000);
  assert.deepEqual(h.context.events, []);
  h.advance(1400);
  assert.equal(h.context.events.length, 1);
  h.detach();
});

test("resizing the same result redraws it without replaying its sound", t => {
  const h = setup(t);
  h.visible(); h.advance(1500);
  h.component.pageLifetimes.resize.call(h.context);
  h.visible(); h.advance(8200);
  assert.equal(h.audioCalls.played, 1);
  assert.deepEqual(h.context.events, [{ name: "complete", detail: { id: "result-1" } }]);
  h.detach();
});

test("book pixels and particles honor the fade envelope, including the first and final frames", () => {
  const { drawStoryBook } = require("../components/story-result/book-art.ts");
  const alpha = [], stack = [];
  const context = new Proxy({ globalAlpha: 1,
    save() { stack.push(this.globalAlpha); }, restore() { this.globalAlpha = stack.pop(); },
    createLinearGradient() { return { addColorStop() {} }; },
    createRadialGradient() { return { addColorStop() {} }; },
    fill() { alpha.push(this.globalAlpha); }, stroke() { alpha.push(this.globalAlpha); },
    fillRect() { alpha.push(this.globalAlpha); },
  }, { get(target, key) { return key in target ? target[key] : () => {}; } });
  for (const opacity of [0, .5, 1, 0]) {
    alpha.length = 0;
    drawStoryBook(context, 320, 140, 2, 1.2, opacity);
    assert.ok(alpha.length > 0);
    assert.ok(alpha.every(value => value >= 0 && value <= opacity));
    assert.ok(alpha.includes(opacity));
    assert.equal(context.globalAlpha, 1);
  }
});
