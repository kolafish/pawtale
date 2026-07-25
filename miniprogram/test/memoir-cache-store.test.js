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
  delete require.cache[require.resolve("../stores/memoir-cache-store.ts")];
  return require("../stores/memoir-cache-store.ts");
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
    markMemoirCacheStale,
    markMemoirCacheResourcesStaleForUser,
    readMemoirCache,
    writeMemoirCache,
  } = cacheModule();
  const cachedAt = Date.parse("2026-07-25T00:00:00.000Z");

  assert.equal(
    writeMemoirCache(descriptor(), { title: "第二版" }, {
      serverRevision: 2,
      cachedAtMs: cachedAt,
    }),
    "written",
  );
  assert.deepEqual(readMemoirCache(descriptor(), cachedAt + 1_000), {
    status: "fresh",
    payload: { title: "第二版" },
    serverRevision: 2,
    localInvalidationRevision: 0,
    cachedAt: "2026-07-25T00:00:00.000Z",
  });
  assert.equal(
    writeMemoirCache(descriptor(), { title: "第一版" }, {
      serverRevision: 1,
      cachedAtMs: cachedAt + 2_000,
    }),
    "ignored_older",
  );

  markMemoirCacheStale(descriptor(), true);
  const stale = readMemoirCache(descriptor(), cachedAt + 3_000);
  assert.equal(stale.status, "stale");
  assert.equal(stale.payload.title, "第二版");
  assert.equal(stale.localInvalidationRevision, 1);

  writeMemoirCache(descriptor("user-2"), { title: "其他用户" }, {
    serverRevision: 1,
    cachedAtMs: cachedAt,
  });
  markMemoirCacheResourcesStaleForUser("user-1", new Set(["book"]));
  assert.equal(readMemoirCache(descriptor("user-1"), cachedAt).status, "stale");
  assert.equal(
    readMemoirCache(descriptor("user-1"), cachedAt).localInvalidationRevision,
    2,
  );
  assert.equal(readMemoirCache(descriptor("user-2"), cachedAt).status, "fresh");

  writeMemoirCache(descriptor("user-3"), { title: "较新的本地状态" }, {
    cachedAtMs: cachedAt + 5_000,
  });
  assert.equal(
    writeMemoirCache(descriptor("user-3"), { title: "旧格式迁移数据" }, {
      cachedAtMs: cachedAt,
    }),
    "ignored_older",
  );
  assert.equal(
    readMemoirCache(descriptor("user-3"), cachedAt + 6_000).payload.title,
    "较新的本地状态",
  );
  delete global.wx;
});

test("memoir snapshots reject incompatible schemas and clear only one user scope", () => {
  const storage = installStorageMock();
  const {
    clearMemoirCacheForUser,
    readMemoirCache,
    writeMemoirCache,
  } = cacheModule();
  const cachedAt = Date.parse("2026-07-25T00:00:00.000Z");

  writeMemoirCache(descriptor("user-1"), { title: "用户一" }, {
    serverRevision: 1,
    cachedAtMs: cachedAt,
  });
  writeMemoirCache(descriptor("user-2"), { title: "用户二" }, {
    serverRevision: 1,
    cachedAtMs: cachedAt,
  });

  assert.deepEqual(readMemoirCache(descriptor("user-1", 2), cachedAt), { status: "miss" });
  assert.equal(Object.keys(storage).length, 1);

  clearMemoirCacheForUser("user-2");
  assert.equal(Object.keys(storage).length, 0);
  delete global.wx;
});
