require("./register-ts");
const test = require("node:test");
const assert = require("node:assert/strict");
const { sharedReadingKey, readSharedReadingProgress, saveSharedReadingProgress } = require("../stores/reading-progress.ts");

test("shared reading positions are isolated by visitor, object and backend with an explicit schema", () => {
  const storage = {};
  global.wx = { getStorageSync: key => storage[key], setStorageSync: (key, value) => { storage[key] = value; } };
  const guest = sharedReadingKey("book-1", "book");
  const value = { versionId: "v1", chapterId: "c1", chapterIndex: 0, scrollTop: 321, audioChapterId: "c1", audioHash: "hash", audioIndex: 2, audioSeconds: 3.2 };
  saveSharedReadingProgress(guest, value);
  assert.deepEqual(readSharedReadingProgress(guest), value);
  assert.equal(readSharedReadingProgress(sharedReadingKey("story-1", "story")), null);
  storage.pawtale_backend_cookie = "session";
  storage.pawtale_memoir_session = { cache_identity: { user_id: "visitor-1" } };
  const member = sharedReadingKey("book-1", "book");
  assert.notEqual(member, guest);
  assert.equal(readSharedReadingProgress(member), null);
  storage.pawtale_shared_reading_v1.schema = 0;
  assert.equal(readSharedReadingProgress(guest), null);
  delete global.wx;
});
