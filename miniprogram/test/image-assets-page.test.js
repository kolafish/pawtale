require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  loadPageModule,
  mockModule,
  pageContext,
  unmockedAsync,
} = require("./page-harness");

function imageAsset(id, overrides = {}) {
  return {
    id,
    project_id: "project-1",
    status: "ready",
    source_type: "chapter_illustration",
    source_signature: `signature-${id}`,
    storage_path: `images/${id}.jpg`,
    thumbnail_storage_path: `images/${id}-thumb.jpg`,
    file_url: `/image-assets/${id}/file`,
    thumbnail_url: `/image-assets/${id}/file?thumbnail=true`,
    mime_type: "image/jpeg",
    title: `图片 ${id}`,
    caption: "",
    alt_text: "",
    style_id: "documentary",
    prompt_hash: "hash",
    prompt_version: "v1",
    provider: "fake",
    model: "fake",
    source_utterance_id: null,
    source_chapter_id: null,
    source_chapter_title: "第一章",
    source_quote: "那年夏天。",
    metadata_json: {},
    include_in_book: false,
    placements: [],
    created_at: "2026-07-01T00:00:00.000Z",
    updated_at: "2026-07-01T00:00:00.000Z",
    ...overrides,
  };
}

function imageAssetView(asset, localPath = "") {
  return {
    ...asset,
    localPath,
    titleText: asset.title,
    sourceLabel: "章节插图",
    statusText: "未使用",
    placementText: "尚未入书",
    createdAtText: "07-01 08:00",
    captionDraft: asset.caption,
    captionChanged: false,
    detailsExpanded: false,
    enabledPlacements: asset.placements.filter((placement) => placement.enabled),
  };
}

function loadImageAssetsPage(apiExports = {}) {
  mockModule("../services/auth.ts", {
    getMemoirIdentity: async () => ({ active_project_id: "project-1" }),
  });
  mockModule("../services/memoir-voice-api.ts", {
    createMemoirBookImagePlacement: unmockedAsync("createMemoirBookImagePlacement"),
    deleteMemoirBookImagePlacement: unmockedAsync("deleteMemoirBookImagePlacement"),
    deleteMemoirImageAsset: unmockedAsync("deleteMemoirImageAsset"),
    downloadMemoirImageAssetFile: unmockedAsync("downloadMemoirImageAssetFile"),
    getMemoirBookPlan: async () => null,
    listMemoirImageAssets: async () => ({
      items: [],
      total: 0,
      ready_count: 0,
      included_count: 0,
      unused_count: 0,
    }),
    updateMemoirImageAsset: unmockedAsync("updateMemoirImageAsset"),
    ...apiExports,
  });
  return loadPageModule("../pages/image-assets/index.ts");
}

test("image assets page batches thumbnails and uses the full file for preview", async () => {
  const assets = Array.from({ length: 9 }, (_, index) => imageAsset(`asset-${index + 1}`));
  const downloads = [];
  let activeDownloads = 0;
  let maxActiveDownloads = 0;
  const { page } = loadImageAssetsPage({
    downloadMemoirImageAssetFile: async (projectId, assetId, thumbnail) => {
      downloads.push({ projectId, assetId, thumbnail });
      activeDownloads += 1;
      maxActiveDownloads = Math.max(maxActiveDownloads, activeDownloads);
      await new Promise((resolve) => setTimeout(resolve, 2));
      activeDownloads -= 1;
      return `/tmp/${assetId}${thumbnail ? "-thumb" : ""}.jpg`;
    },
  });
  const context = pageContext(page, {
    projectId: "project-1",
    items: assets.map((asset) => imageAssetView(asset)),
  });
  context.loadToken = 1;
  let patchCount = 0;
  const setData = context.setData;
  context.setData = function setDataAndCount(patch) {
    patchCount += 1;
    setData.call(this, patch);
  };

  await page.downloadVisibleImages.call(context, assets, "project-1", 1);

  assert.equal(maxActiveDownloads, 4);
  assert.equal(patchCount, 3);
  assert.equal(context.data.items.every((item) => item.localPath.endsWith("-thumb.jpg")), true);

  const previews = [];
  global.wx = {
    previewImage(options) {
      previews.push(options);
    },
  };
  await page.previewAsset.call(context, {
    currentTarget: { dataset: { id: "asset-1" } },
  });

  assert.deepEqual(downloads[downloads.length - 1], {
    projectId: "project-1",
    assetId: "asset-1",
    thumbnail: false,
  });
  assert.deepEqual(previews, [{
    current: "/tmp/asset-1.jpg",
    urls: ["/tmp/asset-1.jpg"],
  }]);
});

test("image assets page keeps successful placement removals when a later removal fails", async () => {
  const placements = ["placement-1", "placement-2"].map((id) => ({
    id,
    project_id: "project-1",
    image_asset_id: "asset-1",
    placement_type: "chapter_opening",
    chapter_id: `chapter-${id}`,
    chapter_title: id,
    caption_override: "",
    display_size: "full_width",
    sort_order: 0,
    enabled: true,
    created_at: "2026-07-01T00:00:00.000Z",
    updated_at: "2026-07-01T00:00:00.000Z",
  }));
  const asset = imageAsset("asset-1", {
    include_in_book: true,
    placements,
  });
  const deleteCalls = [];
  const { page } = loadImageAssetsPage({
    deleteMemoirBookImagePlacement: async (_projectId, _assetId, placementId) => {
      deleteCalls.push(placementId);
      if (placementId === "placement-2") {
        throw new Error("第二个位置删除失败");
      }
      return { ...asset, placements: [placements[1]] };
    },
  });
  const context = pageContext(page, {
    projectId: "project-1",
    total: 1,
    includedCount: 1,
    unusedCount: 0,
    items: [imageAssetView(asset, "/tmp/asset-1-thumb.jpg")],
  });

  await page.removeFromBook.call(context, {
    currentTarget: { dataset: { id: "asset-1" } },
  });

  assert.deepEqual(deleteCalls, ["placement-1", "placement-2"]);
  assert.deepEqual(
    context.data.items[0].enabledPlacements.map((placement) => placement.id),
    ["placement-2"],
  );
  assert.equal(context.data.items[0].localPath, "/tmp/asset-1-thumb.jpg");
  assert.equal(context.data.error, "第二个位置删除失败");
});

test("image assets page reveals secondary editing on demand and tracks caption changes", () => {
  const asset = imageAsset("asset-1", { caption: "原说明" });
  const { page } = loadImageAssetsPage();
  const context = pageContext(page, {
    items: [imageAssetView(asset)],
  });

  page.toggleAssetDetails.call(context, {
    currentTarget: { dataset: { id: "asset-1" } },
  });
  assert.equal(context.data.items[0].detailsExpanded, true);

  page.onCaptionInput.call(context, {
    currentTarget: { dataset: { id: "asset-1" } },
    detail: { value: "新说明" },
  });
  assert.equal(context.data.items[0].captionChanged, true);

  page.onCaptionInput.call(context, {
    currentTarget: { dataset: { id: "asset-1" } },
    detail: { value: " 原说明 " },
  });
  assert.equal(context.data.items[0].captionChanged, false);
});

test("image assets page places a source-linked illustration near its material", async () => {
  const asset = imageAsset("asset-1", { source_utterance_id: "utterance-1" });
  const placementCalls = [];
  const { page } = loadImageAssetsPage({
    createMemoirBookImagePlacement: async (projectId, assetId, data) => {
      placementCalls.push({ projectId, assetId, data });
      return {
        ...asset,
        include_in_book: true,
        placements: [],
      };
    },
  });
  const context = pageContext(page, {
    projectId: "project-1",
    items: [imageAssetView(asset)],
  });
  global.wx = {
    showToast() {},
  };

  await page.placeNearSource.call(context, {
    currentTarget: { dataset: { id: "asset-1" } },
  });

  assert.deepEqual(placementCalls, [{
    projectId: "project-1",
    assetId: "asset-1",
    data: {
      placement_type: "chapter_inline",
      display_size: "full_width",
    },
  }]);
  assert.equal(context.data.error, "");
});
