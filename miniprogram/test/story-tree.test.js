require("./register-ts");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { componentContext, loadComponentModule } = require("./page-harness");
const { emptyStoryViewData } = require("../components/story-tree/story-view-data.ts");

const tap = (dataset = {}) => ({ currentTarget: { dataset } });

function tree(story = emptyStoryViewData(), properties = {}) {
  const { component } = loadComponentModule("../components/story-tree/index.ts");
  return componentContext(component, { story, ...properties });
}

test("tree reveals additional themes locally without mutating the supplied story", () => {
  const story = { ...emptyStoryViewData(), chapters: [{ id: "childhood" }] };
  const context = tree(story);
  context.toggleAllChapters();
  assert.equal(context.data.showAllChapters, true);
  assert.equal(context.properties.story, story);
  context.toggleAllChapters();
  assert.equal(context.data.showAllChapters, false);
});

test("tree emits only supported destinations and preserves chapter identity", () => {
  const context = tree(undefined, { canSwitchProject: true });
  context.openProjects();
  context.openChapter(tap({ id: "childhood" }));
  context.openBook();
  context.openTranscripts();
  context.openVoiceEdit();
  assert.deepEqual(context.events, [
    { name: "openprojects", detail: undefined },
    { name: "openchapter", detail: { chapterId: "childhood" } },
    { name: "openbook", detail: undefined },
    { name: "opentranscripts", detail: undefined },
    { name: "voiceedit", detail: undefined },
  ]);
});

test("tree ignores unavailable project switching and chapter actions without a target", () => {
  const context = tree(undefined, { canSwitchProject: false });
  context.openProjects();
  context.openChapter(tap());
  assert.deepEqual(context.events, []);
});

test("tree template keeps the hero, compact story list and book card without timeline numbering", () => {
  const template = fs.readFileSync(path.join(__dirname, "../components/story-tree/index.wxml"), "utf8");
  assert.match(template, /class="story-hero/);
  assert.match(template, /树上的故事/);
  assert.match(template, /我的书/);
  assert.match(template, /name="heroAction"/);
  assert.match(template, /name="afterHero"/);
  assert.doesNotMatch(template, /chapter-node-number|小节|时间线/);
});

test("tree reports the parent theme position for growth feedback", () => {
  const context = tree();
  let rect = { top: 175 };
  const targets = [];
  context.createSelectorQuery = () => ({ select: selector => {
    targets.push(selector);
    return { boundingClientRect: callback => ({ exec: () => callback(rect) }) };
  } });
  context.focusGrowth("childhood");
  assert.deepEqual(context.events, [{ name: "focusgrowth", detail: { top: 175 } }]);
  rect = null;
  context.focusGrowth("deleted");
  assert.equal(context.events.length, 1);
  assert.deepEqual(targets, ["#story-chapter-childhood", "#story-chapter-deleted"]);
});

test("tree reveals a hidden theme before measuring its growth position", () => {
  const context = tree({
    ...emptyStoryViewData(),
    chapters: [{ id: "later", featured: false }],
  });
  const targets = [];
  context.createSelectorQuery = () => ({ select: selector => {
    targets.push(selector);
    return { boundingClientRect: callback => ({ exec: () => callback({ top: 240 }) }) };
  } });
  context.focusGrowth("later");
  assert.equal(context.data.showAllChapters, true);
  assert.deepEqual(targets, ["#story-chapter-later"]);
  assert.deepEqual(context.events, [{ name: "focusgrowth", detail: { top: 240 } }]);
});
