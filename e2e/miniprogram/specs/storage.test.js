const assert = require("node:assert/strict");
const { test } = require("node:test");
const {
  captureStorageEntries,
  restoreStorageEntries,
} = require("../support/session");

function inMemoryMiniProgram(initialEntries) {
  const storage = new Map(initialEntries);
  return {
    storage,
    async callWxMethod(method, ...args) {
      if (method === "getStorageInfoSync") {
        return { keys: [...storage.keys()] };
      }
      if (method === "getStorageSync") {
        return storage.get(args[0]);
      }
      if (method === "setStorageSync") {
        storage.set(args[0], args[1]);
        return undefined;
      }
      if (method === "removeStorageSync") {
        storage.delete(args[0]);
        return undefined;
      }
      throw new Error(`Unexpected wx method: ${method}`);
    },
  };
}

test("storage snapshot restores existing values and removes test-only keys", async () => {
  const miniProgram = inMemoryMiniProgram([
    ["existing-object", { enabled: false, speedRatio: 1 }],
    ["existing-string", "before"],
  ]);
  const keys = ["existing-object", "existing-string", "test-only"];
  const snapshot = await captureStorageEntries(miniProgram, keys);

  miniProgram.storage.set("existing-object", { enabled: true, speedRatio: 0.7 });
  miniProgram.storage.delete("existing-string");
  miniProgram.storage.set("test-only", "fixture");
  await restoreStorageEntries(miniProgram, snapshot);

  assert.deepEqual(
    await captureStorageEntries(miniProgram, keys),
    snapshot,
  );
  assert.deepEqual([...miniProgram.storage.entries()], [
    ["existing-object", { enabled: false, speedRatio: 1 }],
    ["existing-string", "before"],
  ]);
});
