require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  componentContext,
  flushAsyncWork,
  loadComponentModule,
  mockModule,
} = require("./page-harness");

function loadPhotos({ uploads = [], library = [] } = {}) {
  mockModule("../services/authenticated-media.ts", {
    chooseImageFiles: async (count) => ["/tmp/a.jpg", "/tmp/b.jpg"].slice(0, count),
  });
  mockModule("../services/memoir-book-api.ts", {
    downloadMemoirImageAssetFile: async (_projectId, id) => `/tmp/thumb-${id}.jpg`,
    listMemoirImageAssets: async () => ({ items: library }),
    uploadMemoirImageAsset: async (_projectId, filePath) => {
      uploads.push(filePath);
      return { id: `asset-${filePath.slice(5, 6)}` };
    },
  });
  return loadComponentModule("../components/share-video-photos/index.ts");
}

function changes(context) {
  return context.events.filter((event) => event.name === "change").map((event) => event.detail.photoIds);
}

test("album photos upload in order and append after the chosen ones", async () => {
  const uploads = [];
  const { component } = loadPhotos({ uploads });
  const context = componentContext(component, { projectId: "project-1", photoIds: ["old"], maxCount: 3 });
  await component.methods.uploadFromAlbum.call(context, 2);
  assert.deepEqual(uploads, ["/tmp/a.jpg", "/tmp/b.jpg"]);
  assert.deepEqual(changes(context), [["old", "asset-a", "asset-b"]]);
  assert.equal(context.data.paths["asset-a"], "/tmp/a.jpg");
  assert.equal(context.data.busy, false);
});

test("library picker keeps the current order and appends new picks in tap order", async () => {
  const ready = (id) => ({ id, status: "ready" });
  const { component } = loadPhotos({ library: [ready("x"), ready("y"), ready("z"), { id: "p", status: "pending" }] });
  const context = componentContext(component, { projectId: "project-1", photoIds: ["z", "gone"], maxCount: 3 });
  await component.methods.openPicker.call(context);
  await flushAsyncWork();
  assert.deepEqual(context.data.pickerItems.map((item) => [item.id, item.order]), [["x", 0], ["y", 0], ["z", 1]]);
  const tap = (index) => component.methods.togglePickerItem.call(context, { currentTarget: { dataset: { index } } });
  tap(1);
  tap(0);
  tap(2);
  component.methods.confirmPicker.call(context);
  assert.deepEqual(changes(context), [["y", "x"]]);
});

test("dragging a photo moves it to the slot under the finger", () => {
  const { component, mod } = loadPhotos();
  const grid = { left: 0, top: 0, width: 400 };
  assert.equal(mod.slotAt(grid, 250, 50, 5), 2);
  assert.equal(mod.slotAt(grid, 50, 150, 5), 4);
  assert.equal(mod.slotAt(grid, 350, 150, 5), 4);
  const photos = ["a", "b", "c"].map((id) => ({ id, path: "" }));
  const context = componentContext(component, { photoIds: ["a", "b", "c"] }, { photos, grid, dragIndex: 0 });
  component.methods.moveDrag.call(context, { touches: [{ clientX: 250, clientY: 50 }] });
  component.methods.endDrag.call(context);
  assert.deepEqual(changes(context), [["b", "c", "a"]]);
});
