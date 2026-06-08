require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

const STORE_KEY = "pawtale_memoir_session";

function clone(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function installWxStorageMock(initialStorage = {}, failWrites = 0) {
  const storage = clone(initialStorage) || {};
  const writes = [];
  let remainingFailures = failWrites;

  global.wx = {
    getStorageSync(key) {
      return clone(storage[key]);
    },
    setStorageSync(key, value) {
      writes.push({ key, value: clone(value) });
      if (remainingFailures > 0) {
        remainingFailures -= 1;
        throw new Error("storage full");
      }
      storage[key] = clone(value);
    },
    removeStorageSync(key) {
      delete storage[key];
    },
  };

  return { storage, writes };
}

function sessionStoreModule() {
  delete require.cache[require.resolve("../stores/session.ts")];
  return require("../stores/session.ts");
}

test("writeMemoirStore persists fallback when story detail cache is too large", () => {
  const mock = installWxStorageMock({}, 1);
  const { replaceMemoirStore } = sessionStoreModule();

  const result = replaceMemoirStore({
    cache_identity: { user_id: "user-1", project_id: "project-1" },
    story_detail_cache: {
      version: 3,
      details: {
        answer_1: {
          detail: { answer: { id: "answer_1" } },
          cached_at: "2026-06-08T00:00:00.000Z",
        },
      },
    },
  });

  assert.equal(mock.writes.length, 2);
  assert.equal(result.story_detail_cache, undefined);
  assert.equal(typeof result.cache_write_failed_at, "string");
  assert.deepEqual(mock.storage[STORE_KEY], result);
});

test("writeMemoirStore marks unpersisted result when storage write fails without fallback", () => {
  installWxStorageMock({}, 1);
  const { replaceMemoirStore } = sessionStoreModule();

  const result = replaceMemoirStore({
    cache_identity: { user_id: "user-1", project_id: "project-1" },
  });

  assert.equal(typeof result.cache_write_failed_at, "string");
});

test("writeMemoirStore clears previous write failure marker after successful write", () => {
  const mock = installWxStorageMock({
    [STORE_KEY]: {
      cache_identity: { user_id: "user-1", project_id: "project-1" },
      cache_write_failed_at: "2026-06-08T00:00:00.000Z",
    },
  });
  const { writeMemoirStore } = sessionStoreModule();

  const result = writeMemoirStore({
    project: { id: "project-1", title: "回忆录" },
  });

  assert.equal(result.cache_write_failed_at, undefined);
  assert.equal(mock.storage[STORE_KEY].cache_write_failed_at, undefined);
  assert.equal(mock.storage[STORE_KEY].project.title, "回忆录");
});
