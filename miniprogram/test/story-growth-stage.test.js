require("./register-ts");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const {
  resolveStoryTreeGrowthStage,
  STORY_CHAPTER_SPRIG_IMAGE,
  STORY_GROWTH_NOTICE_IMAGES,
  STORY_TREE_STAGE_IMAGES,
} = require("../components/story-tree/story-growth-stage.ts");

const chapter = (patch = {}) => ({ include_in_book: true, char_count: 0, readable: false, ...patch });
const snapshot = (patch = {}) => ({
  chapters: [chapter()], book_readable: false, book_char_count: 0, book_chapter_count: 0, ...patch,
});

test("story tree grows through structure and writing milestones", () => {
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ chapters: [] })), 1);
  assert.equal(resolveStoryTreeGrowthStage(snapshot()), 2);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ chapters: [chapter(), chapter({ readable: true })] })), 2);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({
    chapters: [chapter({ readable: true }), chapter({ readable: true })],
    book_readable: true,
    book_char_count: 20_000,
    book_chapter_count: 2,
  })), 2);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ chapters: [chapter(), chapter(), chapter()] })), 3);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({
    chapters: [chapter({ char_count: 1_499 }), chapter({ char_count: 1_500 })],
  })), 2);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({
    chapters: [chapter({ char_count: 1_500 }), chapter({ char_count: 1_500 })],
  })), 3);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ chapters: Array.from({ length: 5 }, () => chapter()) })), 4);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ chapters: [chapter({ char_count: 10_000 })] })), 4);
});

test("story tree becomes mature only after both final milestones", () => {
  assert.equal(resolveStoryTreeGrowthStage(snapshot({
    chapters: Array.from({ length: 5 }, () => chapter({ char_count: 1_999 })),
  })), 4);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({
    chapters: [chapter({ char_count: 10_000 }), chapter(), chapter()],
  })), 4);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({
    chapters: Array.from({ length: 5 }, () => chapter({ char_count: 2_000 })),
  })), 5);
});

test("story tree art configuration points to packaged assets", () => {
  assert.deepEqual(Object.values(STORY_TREE_STAGE_IMAGES), [
    "/assets/story-tree/tree-stage-01-seed.png",
    "/assets/story-tree/tree-stage-02-seedling.png",
    "/assets/story-tree/tree-stage-03-sapling.png",
    "/assets/story-tree/tree-stage-04-branching.png",
    "/assets/story-tree/tree-stage-05-mature.png",
  ]);
  assert.equal(STORY_GROWTH_NOTICE_IMAGES.progress, STORY_TREE_STAGE_IMAGES[4]);
  const sources = new Set([
    ...Object.values(STORY_TREE_STAGE_IMAGES),
    STORY_CHAPTER_SPRIG_IMAGE,
    ...Object.values(STORY_GROWTH_NOTICE_IMAGES),
  ]);
  for (const source of sources) {
    const file = path.join(__dirname, "..", source.slice(1));
    assert.equal(fs.existsSync(file), true, `${source} must exist`);
  }
});
