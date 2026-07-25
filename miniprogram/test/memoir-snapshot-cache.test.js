require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");

function installStorageMock() {
  const storage = {};
  global.wx = {
    getStorageSync(key) {
      return storage[key];
    },
    setStorageSync(key, value) {
      storage[key] = value;
    },
    removeStorageSync(key) {
      delete storage[key];
    },
    getStorageInfoSync() {
      return { keys: Object.keys(storage) };
    },
  };
  return storage;
}

function cacheModule() {
  delete require.cache[require.resolve("../stores/memoir-snapshot-cache.ts")];
  return require("../stores/memoir-snapshot-cache.ts");
}

function descriptor(userId = "user-1", schemaVersion = 1) {
  return {
    scope: {
      userId,
      storageOwnerId: "owner-1",
      projectId: "project-1",
      resource: "book",
    },
    cacheSchemaVersion: schemaVersion,
    maxAgeMs: 60_000,
    isPayload(value) {
      return Boolean(value && typeof value === "object" && typeof value.title === "string");
    },
  };
}

test("memoir snapshots preserve the newest server revision and expose stale data", () => {
  installStorageMock();
  const {
    markMemoirSnapshotStale,
    markMemoirSnapshotsStaleForUser,
    readMemoirSnapshot,
    writeMemoirSnapshot,
  } = cacheModule();
  const cachedAt = Date.parse("2026-07-25T00:00:00.000Z");

  assert.equal(
    writeMemoirSnapshot(descriptor(), { title: "第二版" }, 2, cachedAt),
    "written",
  );
  assert.deepEqual(readMemoirSnapshot(descriptor(), cachedAt + 1_000), {
    status: "fresh",
    payload: { title: "第二版" },
    serverRevision: 2,
    localInvalidationRevision: 0,
    cachedAt: "2026-07-25T00:00:00.000Z",
  });
  assert.equal(
    writeMemoirSnapshot(descriptor(), { title: "第一版" }, 1, cachedAt + 2_000),
    "ignored_older",
  );

  markMemoirSnapshotStale(descriptor());
  const stale = readMemoirSnapshot(descriptor(), cachedAt + 3_000);
  assert.equal(stale.status, "stale");
  assert.equal(stale.payload.title, "第二版");
  assert.equal(stale.localInvalidationRevision, 1);

  writeMemoirSnapshot(descriptor("user-2"), { title: "其他用户" }, 1, cachedAt);
  markMemoirSnapshotsStaleForUser("user-1");
  assert.equal(readMemoirSnapshot(descriptor("user-1"), cachedAt).status, "stale");
  assert.equal(
    readMemoirSnapshot(descriptor("user-1"), cachedAt).localInvalidationRevision,
    2,
  );
  assert.equal(readMemoirSnapshot(descriptor("user-2"), cachedAt).status, "fresh");
  delete global.wx;
});

test("memoir snapshots reject incompatible schemas and clear only one user scope", () => {
  const storage = installStorageMock();
  const {
    clearMemoirSnapshotsForUser,
    readMemoirSnapshot,
    writeMemoirSnapshot,
  } = cacheModule();
  const cachedAt = Date.parse("2026-07-25T00:00:00.000Z");

  writeMemoirSnapshot(descriptor("user-1"), { title: "用户一" }, 1, cachedAt);
  writeMemoirSnapshot(descriptor("user-2"), { title: "用户二" }, 1, cachedAt);

  assert.deepEqual(readMemoirSnapshot(descriptor("user-1", 2), cachedAt), { status: "miss" });
  assert.equal(Object.keys(storage).length, 1);

  clearMemoirSnapshotsForUser("user-2");
  assert.equal(Object.keys(storage).length, 0);
  delete global.wx;
});
