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
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ book_char_count: 2_999 })), 2);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ book_char_count: 3_000 })), 3);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ chapters: [chapter(), chapter({ readable: true })] })), 2);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ chapters: [chapter({ readable: true }), chapter({ readable: true })] })), 3);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ chapters: [chapter(), chapter(), chapter()] })), 3);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({
    chapters: [chapter({ char_count: 1_500 }), chapter({ char_count: 1_500 })],
  })), 3);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ chapters: Array.from({ length: 5 }, () => chapter()) })), 4);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ book_char_count: 9_999, chapters: [chapter(), chapter(), chapter()] })), 3);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ book_char_count: 10_000 })), 4);
});

test("story tree uses current readable coverage for the final stages", () => {
  const threeReadable = [chapter({ readable: true }), chapter({ readable: true }), chapter({ readable: true })];
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ chapters: threeReadable, book_readable: true, book_chapter_count: 3 })), 5);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({ chapters: threeReadable, book_readable: true, book_chapter_count: 2 })), 4);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({
    chapters: [chapter({ readable: true }), chapter({ readable: true }), chapter()],
  })), 4);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({
    chapters: [chapter({ readable: true }), chapter({ readable: true })], book_readable: true, book_chapter_count: 2,
  })), 4);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({
    chapters: [chapter({ readable: true, char_count: 10_000 })], book_readable: true, book_chapter_count: 1,
  })), 5);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({
    chapters: [...threeReadable, chapter({ include_in_book: false })], book_readable: true, book_chapter_count: 3,
  })), 5);
  assert.equal(resolveStoryTreeGrowthStage(snapshot({
    chapters: [chapter({ include_in_book: false, readable: true, char_count: 20_000 })], book_readable: true, book_chapter_count: 1,
  })), 4);
});

test("story tree art configuration points to packaged assets", () => {
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
