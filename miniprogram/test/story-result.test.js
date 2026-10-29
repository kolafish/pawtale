require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { componentContext, loadComponentModule } = require("./page-harness");

function setup(t, { celebrate = false, deferCanvas = false } = {}) {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1000 });
  const { component } = loadComponentModule("../components/story-result/index.ts");
  const result = { id: "result-1", eventIds: ["one", "two"], titles: [
    { id: "s1", title: "第一次离家", label: "新增小节" },
    { id: "s2", title: "小时候的家", label: "补充" },
  ], wordDelta: 644, moreCount: 0, celebrate };
  const context = componentContext(component, { result });
  let intersect, canvasCallback;
  context.createIntersectionObserver = () => ({
    relativeToViewport() { return this; },
    observe(_, callback) { intersect = callback; },
    disconnect() {},
  });
  context.createSelectorQuery = () => ({ select: () => ({ fields: (_, callback) => {
    canvasCallback = callback;
    return { exec: () => { if (!deferCanvas) callback(null); } };
  } }) });
  component.lifetimes.ready.call(context);
  return { component, context, result, visible: value => intersect({ intersectionRatio: value ? 1 : 0 }),
    visibleRatio: ratio => intersect({ intersectionRatio: ratio }),
    resolveCanvas: () => canvasCallback(null),
    advance(milliseconds) { for (let n = 0; n < milliseconds; n += 33) t.mock.timers.tick(33); },
    detach() { component.lifetimes.detached.call(context); } };
}

test("result waits for visibility, pauses offscreen and completes only after visible reading time", t => {
  const h = setup(t);
  h.advance(5000);
  assert.deepEqual(h.context.events, []);
  h.visibleRatio(.6); // Seeing the book or only part of the text cannot consume the result.
  h.advance(5000);
  assert.deepEqual(h.context.events, []);
  h.visible(true);
  h.advance(900);
  assert.ok(h.context.data.rows[0].opacity > h.context.data.rows[1].opacity);
  h.advance(400);
  h.visible(false);
  const paused = h.context.data.count;
  h.advance(5000);
  assert.equal(h.context.data.count, paused);
  assert.deepEqual(h.context.events, []);
  h.visible(true);
  h.advance(1400);
  assert.equal(h.context.data.count, 644);
  assert.equal(h.context.data.opacity, 1);
  h.context.properties.result = { ...h.result }; // An identical snapshot must not restart playback.
  h.component.observers.result.call(h.context);
  h.advance(1200);
  assert.deepEqual(h.context.events, [{ name: "complete", detail: { id: "result-1" } }]);
  h.visible(true);
  h.advance(5000);
  assert.equal(h.context.events.length, 1);
  h.detach();
});

test("background time and a destroyed canvas callback cannot acknowledge an unseen result", t => {
  const h = setup(t, { celebrate: true, deferCanvas: true });
  h.component.pageLifetimes.hide.call(h.context);
  h.resolveCanvas();
  h.visible(true);
  h.advance(5000);
  assert.deepEqual(h.context.events, []);
  h.component.pageLifetimes.show.call(h.context);
  h.advance(1800);
  assert.equal(h.context.data.count, 644);
  h.detach();
  h.resolveCanvas();
  h.advance(5000);
  assert.deepEqual(h.context.events, []);
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
  assert.deepEqual(h.context.events, []);
  h.advance(2900);
  assert.deepEqual(h.context.events, [{ name: "complete", detail: { id: "replacement" } }]);
  h.detach();
});
