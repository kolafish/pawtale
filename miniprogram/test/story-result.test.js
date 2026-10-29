require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { componentContext, loadComponentModule } = require("./page-harness");

function setup(t, { celebrate = false, deferCanvas = false, copyHeight = 100, windowHeight = 600 } = {}) {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1000 });
  const { component } = loadComponentModule("../components/story-result/index.ts");
  const result = { id: "result-1", eventIds: ["one", "two"], titles: [
    { id: "s1", title: "第一次离家", label: "新增小节" },
    { id: "s2", title: "小时候的家", label: "补充" },
  ], wordDelta: 644, moreCount: 0, celebrate };
  const context = componentContext(component, { result });
  let intersect, canvasCallback;
  const layout = { copyHeight, windowHeight };
  global.wx = { getWindowInfo: () => ({ pixelRatio: 2, windowHeight: layout.windowHeight }) };
  context.createIntersectionObserver = () => ({
    relativeToViewport() { return this; },
    observe(_, callback) { intersect = callback; },
    disconnect() {},
  });
  context.createSelectorQuery = () => ({ select: () => ({
    boundingClientRect: callback => ({ exec: () => callback({ height: layout.copyHeight }) }),
    fields: (_, callback) => {
    canvasCallback = callback;
    return { exec: () => { if (!deferCanvas) callback(null); } };
  } }) });
  component.lifetimes.ready.call(context);
  return { component, context, result, layout,
    get completions() { return context.events.filter(event => event.name === "complete"); },
    band: (index, ratio = 1) => intersect({ intersectionRatio: ratio, dataset: { index } }),
    visible: value => intersect({ intersectionRatio: value ? 1 : 0 }),
    visibleRatio: ratio => intersect({ intersectionRatio: ratio }),
    resolveCanvas: () => canvasCallback(null),
    advance(milliseconds) { for (let n = 0; n < milliseconds; n += 33) t.mock.timers.tick(33); },
    detach() { component.lifetimes.detached.call(context); } };
}

test("result waits for visibility, pauses offscreen and completes only after visible reading time", t => {
  const h = setup(t);
  h.advance(5000);
  assert.deepEqual(h.completions, []);
  h.visibleRatio(.6); // Seeing the book or only part of the text cannot consume the result.
  h.advance(5000);
  assert.deepEqual(h.completions, []);
  h.visible(true);
  h.advance(900);
  assert.ok(h.context.data.rows[0].opacity > h.context.data.rows[1].opacity);
  h.advance(400);
  h.visible(false);
  const paused = h.context.data.count;
  h.advance(5000);
  assert.equal(h.context.data.count, paused);
  assert.deepEqual(h.completions, []);
  h.visible(true);
  h.advance(1400);
  assert.equal(h.context.data.count, 644);
  assert.equal(h.context.data.opacity, 1);
  h.context.properties.result = { ...h.result }; // An identical snapshot must not restart playback.
  h.component.observers.result.call(h.context);
  h.advance(1200);
  assert.deepEqual(h.completions, [{ name: "complete", detail: { id: "result-1" } }]);
  h.visible(true);
  h.advance(5000);
  assert.equal(h.completions.length, 1);
  h.detach();
});

test("background time and a destroyed canvas callback cannot acknowledge an unseen result", t => {
  const h = setup(t, { celebrate: true, deferCanvas: true });
  h.component.pageLifetimes.hide.call(h.context);
  h.resolveCanvas();
  h.visible(true);
  h.advance(5000);
  assert.deepEqual(h.completions, []);
  h.component.pageLifetimes.show.call(h.context);
  h.advance(1800);
  assert.equal(h.context.data.count, 644);
  h.detach();
  h.resolveCanvas();
  h.advance(5000);
  assert.deepEqual(h.completions, []);
});

test("a replacement result resets playback and cannot emit completion for the old result", t => {
  const h = setup(t);
  h.visible(true);
  h.advance(2000);
  h.context.properties.result = { ...h.result, id: "replacement", wordDelta: 3 };
  h.component.observers.result.call(h.context);
  h.visible(true);
  h.advance(1000);
  assert.ok(h.context.data.count >= 0 && h.context.data.count <= 3);
  assert.deepEqual(h.completions, []);
  h.advance(2900);
  assert.deepEqual(h.completions, [{ name: "complete", detail: { id: "replacement" } }]);
  h.detach();
});


test("oversize large-text titles remain readable and only complete after every strip was seen", t => {
  const h = setup(t, { copyHeight: 1000, windowHeight: 280 });
  h.context.properties.result = { ...h.result, id: "long-result", titles: h.result.titles.map(row => ({ ...row, title: "回忆".repeat(100) })) };
  h.component.observers.result.call(h.context);
  assert.equal(h.context.data.rows[0].title.length, 200);
  assert.ok(h.context.data.rows.every(row => row.opacity === 1 && row.offset === 0));
  assert.equal(h.context.data.metaOpacity, 1);
  assert.equal(h.context.data.count, 644);
  assert.deepEqual(h.context.events.at(-1), { name: "readingmode", detail: { id: "long-result", static: true } });
  const bands = h.context.data.bands;
  assert.ok(bands.length > 2 && bands.every(band => band.height <= 112));
  h.band(0, .6);
  h.advance(5000);
  h.band(0);
  h.advance(1000);
  h.band(0, 0);
  // Jumping to the bottom must not consume the unseen middle.
  h.band(bands.length - 1);
  h.advance(5000);
  h.band(bands.length - 1, 0);
  assert.deepEqual(h.completions, []);
  assert.equal(h.context.data.opacity, 1);
  h.band(1);
  h.component.pageLifetimes.hide.call(h.context);
  h.advance(5000);
  h.band(1, 0);
  h.component.pageLifetimes.show.call(h.context);
  for (let index = 2; index < bands.length - 1; index++) {
    h.band(index); h.advance(1000); h.band(index, 0);
  }
  assert.deepEqual(h.completions, []);
  h.band(1);
  h.advance(1000);
  assert.deepEqual(h.completions, []); // Fade remains after reading the final strip.
  h.advance(900);
  assert.deepEqual(h.completions, [{ name: "complete", detail: { id: "long-result" } }]);
  h.detach();
});

test("viewport and font changes remeasure reading mode without consuming previous layout", t => {
  const h = setup(t, { copyHeight: 800, windowHeight: 300 });
  h.band(0); h.advance(1000);
  h.layout.copyHeight = 100;
  h.layout.windowHeight = 600;
  h.component.pageLifetimes.resize.call(h.context);
  assert.equal(h.context.data.bands.length, 0);
  assert.deepEqual(h.context.events.at(-1), { name: "readingmode", detail: { id: h.result.id, static: false } });
  h.visible(true); h.advance(2000);
  h.layout.copyHeight = 900;
  h.component.observers.largeText.call(h.context);
  assert.ok(h.context.data.rows.every(row => row.opacity === 1));
  h.band(0); h.advance(5000);
  assert.deepEqual(h.completions, []);
  h.detach();
});
