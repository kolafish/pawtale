require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { componentContext, loadComponentModule, mockModule } = require("./page-harness");

test("photo caption save preserves a failed draft and can retry or clear the note", async () => {
  const requests = [];
  let fail = true;
  mockModule("../services/memoir-book-api.ts", {
    updateMemoirImageAsset: async (projectId, assetId, data) => {
      requests.push({ projectId, assetId, ...data });
      if (fail) throw new Error("网络不可用");
      return { id: assetId, caption: data.caption, source_type: "manual_upload" };
    },
  });
  const { component } = loadComponentModule("../components/photo-caption-editor/index.ts");
  const context = componentContext(component, { projectId: "p", assetId: "photo", caption: "原说明" });
  context.edit();
  context.change({ detail: { value: "爸爸和奶奶，1982 年" } });
  await context.save({ detail: { value: context.data.value } });
  assert.equal(context.data.editing, true);
  assert.equal(context.data.value, "爸爸和奶奶，1982 年");
  assert.equal(context.data.error, "网络不可用");
  assert.equal(context.events.filter(event => event.name === "saved").length, 0);
  assert.deepEqual(context.events.filter(event => event.name === "busy").map(event => event.detail.busy), [true, false]);
  fail = false;
  await context.save({ detail: { value: context.data.value } });
  assert.equal(context.data.saved, true);
  assert.equal(context.data.editing, false);
  assert.equal(context.events.find(event => event.name === "saved").detail.asset.caption, "爸爸和奶奶，1982 年");
  context.edit();
  await context.save({ detail: { value: "  " } });
  assert.equal(requests.at(-1).caption, "");
});
