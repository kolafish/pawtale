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

test("tree expands all themes inside the hero without mutating the supplied story", () => {
  const story = { ...emptyStoryViewData(), chapters: [{ id: "childhood" }] };
  const context = tree(story);
  context.toggleAllChapters();
  assert.equal(context.data.showAllChapters, true);
  assert.equal(context.properties.story, story);
  context.toggleAllChapters();
  assert.equal(context.data.showAllChapters, false);
});

test("tree emits only supported destinations and preserves chapter identity", () => {
  const context = tree(undefined, { canOpenBook: true, canSwitchProject: true });
  context.openProjects();
  context.openBook();
  context.openChapter(tap({ id: "childhood" }));
  context.openTranscripts();
  context.openVoiceEdit();
  assert.deepEqual(context.events, [
    { name: "openprojects", detail: undefined },
    { name: "openbook", detail: undefined },
    { name: "openchapter", detail: { chapterId: "childhood" } },
    { name: "opentranscripts", detail: undefined },
    { name: "voiceedit", detail: undefined },
  ]);
});

test("tree ignores unavailable project switching and chapter actions without a target", () => {
  const context = tree(undefined, { canSwitchProject: false });
  context.openProjects();
  context.openBook();
  context.openChapter(tap());
  assert.deepEqual(context.events, []);
});

test("demo tree opens only readable chapters while owner chapters remain editable", () => {
  const story = {
    ...emptyStoryViewData(),
    // Object properties arrive from WeChat with nested arrays as indexed objects.
    chapters: { 0: { id: "family", readable: true }, 1: { id: "childhood", readable: false } },
  };
  const guest = tree(story, { demoMode: true });
  guest.openChapter(tap({ id: "childhood" }));
  guest.openChapter(tap({ id: "missing" }));
  guest.openChapter(tap());
  guest.openChapter(tap({ id: "family" }));
  assert.deepEqual(guest.events, [
    { name: "openchapter", detail: { chapterId: "family" } },
  ]);

  const owner = tree(story);
  owner.openChapter(tap({ id: "childhood" }));
  assert.deepEqual(owner.events, [
    { name: "openchapter", detail: { chapterId: "childhood" } },
  ]);
});

test("tree template folds the complete story list into the hero without timeline numbering", () => {
  const template = fs.readFileSync(path.join(__dirname, "../components/story-tree/index.wxml"), "utf8");
  assert.match(template, /class="story-hero/);
  assert.match(template, /id="story-book-action"[\s\S]*?bindtap="openBook"/);
  assert.doesNotMatch(template, />读整本书|>阅读全书|>看看书稿/);
  assert.match(template, /id="story-summary-toggle"/);
  assert.match(template, /class="story-list-expand/);
  assert.doesNotMatch(template, /树上的故事/);
  assert.match(template, /全部回忆/);
  assert.match(template, /语音编辑/);
  assert.doesNotMatch(template, /story-book-utility|bookMetaText|book-card|book-sprout\.png|我的书/);
  assert.doesNotMatch(template, /class="book-symbol"/);
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

test("tree expands the theme list before measuring its growth position", () => {
  const context = tree({
    ...emptyStoryViewData(),
    chapters: [{ id: "later" }],
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
