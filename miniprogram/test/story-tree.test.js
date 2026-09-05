require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { componentContext, loadComponentModule } = require("./page-harness");
const { emptyStoryViewData, toggleStoryChapter, highlightStoryGrowth } = require("../components/story-tree/story-view-data.ts");

const tap = (dataset = {}) => ({ currentTarget: { dataset } });
function tree(story = emptyStoryViewData()) {
  const { component } = loadComponentModule("../components/story-tree/index.ts");
  return componentContext(component, { story });
}

test("tree requests chapter expansion without changing the host's story or another tree", () => {
  const story = { ...emptyStoryViewData(), chapters: [{ id: "childhood", expanded: false, sections: [] }] };
  const first = tree(story);
  const second = tree(story);
  first.toggleChapter(tap({ id: "childhood" }));
  assert.deepEqual(first.events, [{ name: "togglechapter", detail: { chapterId: "childhood" } }]);
  assert.equal(first.properties.story.chapters[0].expanded, false);
  const expanded = toggleStoryChapter(story.chapters, first.events[0].detail.chapterId);
  assert.equal(expanded[0].expanded, true);
  assert.equal(second.properties.story.chapters[0].expanded, false);
  assert.equal(second.events.length, 0);
});

test("tree preserves chapter and section targets for reading, editing and media", () => {
  const context = tree();
  context.readChapter(tap({ id: "childhood" }));
  context.openChapter(tap({ id: "childhood" }));
  context.openChapter(tap({ chapterId: "childhood", sectionId: "summer" }));
  context.openMedia(tap({ id: "childhood" }));
  context.openMedia(tap());
  assert.deepEqual(context.events, [
    { name: "readchapter", detail: { chapterId: "childhood" } },
    { name: "openchapter", detail: { chapterId: "childhood", sectionId: "" } },
    { name: "openchapter", detail: { chapterId: "childhood", sectionId: "summer" } },
    { name: "openmedia", detail: { chapterId: "childhood" } },
    { name: "openmedia", detail: { chapterId: "" } },
  ]);
});

test("tree leaves book, transcript and chapter-manager destinations to its host", () => {
  const context = tree();
  context.openBook();
  context.openTranscripts();
  context.openChapterManager();
  assert.deepEqual(context.events.map(({ name }) => name), ["openbook", "opentranscripts", "managechapters"]);
});

test("tree ignores chapter actions without a target", () => {
  const context = tree();
  context.toggleChapter(tap());
  context.readChapter(tap());
  context.openChapter(tap({ sectionId: "orphan" }));
  assert.deepEqual(context.events, []);
});

test("growth highlights open the affected chapters and preserve other expanded chapters", () => {
  const chapters = [
    { id: "new", expanded: false, sections: [] },
    { id: "updated", expanded: false, sections: [{ id: "new-section" }, { id: "old-section" }] },
    { id: "reading", expanded: true, sections: [] },
    { id: "closed", expanded: false, sections: [] },
  ];
  const highlighted = highlightStoryGrowth(chapters, ["new"], ["new-section"]);
  assert.deepEqual(highlighted.map(({ expanded }) => expanded), [true, true, true, false]);
  assert.deepEqual(highlighted.map(({ highlighted }) => highlighted), [true, false, false, false]);
  assert.deepEqual(highlighted[1].sections.map(({ highlighted }) => highlighted), [true, false]);
  assert.equal(chapters[0].expanded, false);
  assert.equal(chapters[1].sections[0].highlighted, undefined);
});
