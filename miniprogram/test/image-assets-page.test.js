require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  flushAsyncWork,
  mockMemoirApiModules,
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
    thumbnailState: localPath ? "ready" : "idle",
    thumbnailError: "",
    titleText: asset.title,
    statusText: "未使用",
    placementText: "尚未入书",
    detailsExpanded: false,
    sourceIds: asset.suggested_source_utterance_ids || (asset.source_utterance_id ? [asset.source_utterance_id] : []),
    enabledPlacements: asset.placements.filter((placement) => placement.enabled),
  };
}

function loadImageAssetsPage(apiExports = {}) {
  mockModule("../stores/memoir-cache.ts", { writeMemoirIdentitySnapshot() {} });
  mockModule("../services/auth.ts", {
    getMemoirIdentity: async () => ({ active_project_id: "project-1" }),
  });
  mockMemoirApiModules({
    createMemoirBookImagePlacement: unmockedAsync("createMemoirBookImagePlacement"),
    deleteMemoirBookImagePlacement: unmockedAsync("deleteMemoirBookImagePlacement"),
    deleteMemoirImageAsset: unmockedAsync("deleteMemoirImageAsset"),
    downloadMemoirImageAssetFile: unmockedAsync("downloadMemoirImageAssetFile"),
    downloadMemoirShareVideoJob: unmockedAsync("downloadMemoirShareVideoJob"),
    getMemoirBookPlan: async () => null,
    getMemoirBookSnapshot: unmockedAsync("getMemoirBookSnapshot"),
    getMemoirShareVideoJob: unmockedAsync("getMemoirShareVideoJob"),
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

test("image assets page reuses its cards for one story section and its videos", async () => {
  const asset = imageAsset("asset-section", {
    source_utterance_id: "utterance-1",
    title: "外婆家的院子",
  });
  const videoJobs = [];
  const videoDownloads = [];
  const navigationTitles = [];
  const { page } = loadImageAssetsPage({
    downloadMemoirImageAssetFile: async () => "/tmp/asset-section.jpg",
    getMemoirBookSnapshot: async () => ({
      identity: { active_project: { id: "project-1", title: "我的故事" } },
      plan: {
        chapters: [{
          id: "chapter-1",
          title: "童年",
          sort_order: 0,
          source_refs: [{ utterance_id: "utterance-1" }],
          sections: [{
            id: "section-1",
            title: "外婆家的夏天",
            sort_order: 0,
            status: "active",
            materials: [{
              utterance_id: "utterance-1",
              text: "每年夏天，我都会去外婆家住上一阵子。",
            }],
          }],
        }],
      },
      image_assets: { items: [asset] },
      share_video_counts: [{
        utterance_id: "utterance-1",
        count: 2,
        job_ids: ["video-2", "video-1"],
        current_job_ids: ["video-2"],
        job_titles: { "video-2": "外婆家的夏天", "video-1": "院子里的蝉鸣" },
      }],
    }),
    getMemoirShareVideoJob: async (id) => {
      videoJobs.push(id);
      return { id, output: { download_url: `/videos/${id}` } };
    },
    downloadMemoirShareVideoJob: async (job) => {
      videoDownloads.push(job.id);
      return `/tmp/${job.id}.mp4`;
    },
  });
  global.wx = {
    setNavigationBarTitle({ title }) { navigationTitles.push(title); },
    navigateTo({ url }) { navigationTitles.push(url); },
  };
  const context = pageContext(page);

  page.onLoad.call(context, {
    projectId: "project-1",
    scope: "section",
    chapterId: "chapter-1",
    sectionId: "section-1",
  });
  await page.onShow.call(context);
  await flushAsyncWork();

  assert.equal(context.data.isStoryMaterialView, true);
  assert.deepEqual(context.data.items.map((item) => item.id), ["asset-section"]);
  assert.deepEqual(context.data.videos.map((item) => item.id), ["video-2", "video-1"]);
  assert.deepEqual(context.data.videos.map((item) => item.title), ["外婆家的夏天", "院子里的蝉鸣"]);
  assert.deepEqual(context.data.videos.map((item) => item.isCurrent), [true, false]);
  assert.deepEqual(navigationTitles, ["外婆家的夏天"]);

  page.remakeVideo.call(context, { currentTarget: { dataset: { id: "video-1" } } });
  assert.equal(navigationTitles[1], "/pages/transcript-media/index?id=utterance-1");

  await page.previewVideo.call(context, {
    currentTarget: { dataset: { id: "video-2" } },
  });
  assert.deepEqual(videoJobs, ["video-2"]);
  assert.deepEqual(videoDownloads, ["video-2"]);
  assert.equal(context.data.videoPreviewPath, "/tmp/video-2.mp4");
  assert.equal(context.data.videoPreviewTitle, "外婆家的夏天");

  page.closeVideoPreview.call(context);
  assert.equal(context.data.videoPreviewPath, "");
});

test("story video list keeps a numbered title for older snapshots", async () => {
  const { page } = loadImageAssetsPage({
    getMemoirBookSnapshot: async () => ({
      identity: { active_project: { id: "project-1", title: "我的故事" } },
      plan: { chapters: [] },
      image_assets: { items: [] },
      share_video_counts: [{
        utterance_id: "utterance-1",
        count: 1,
        job_ids: ["video-1"],
        current_job_ids: ["video-1"],
      }],
    }),
  });
  global.wx = { setNavigationBarTitle() {} };
  const context = pageContext(page);

  page.onLoad.call(context, { projectId: "project-1", scope: "unassigned" });
  await page.onShow.call(context);

  assert.deepEqual(context.data.videos.map((item) => item.title), ["分享视频 1"]);
});

test("image assets page batches real thumbnails and previews the cached thumbnail", async () => {
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
  assert.equal(patchCount, 4);
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

  assert.equal(downloads.length, assets.length);
  assert.deepEqual(previews, [{
    current: "/tmp/asset-1-thumb.jpg",
    urls: ["/tmp/asset-1-thumb.jpg"],
  }]);
});

test("image assets page exposes a failed thumbnail and retries it on tap", async () => {
  const asset = imageAsset("asset-1");
  let attempts = 0;
  const previews = [];
  const { page } = loadImageAssetsPage({
    downloadMemoirImageAssetFile: async () => {
      attempts += 1;
      if (attempts === 1) {
        throw new Error("图片下载超时");
      }
      return "/tmp/asset-1-thumb.jpg";
    },
  });
  const context = pageContext(page, {
    projectId: "project-1",
    items: [imageAssetView(asset)],
  });
  context.loadToken = 1;

  await page.downloadVisibleImages.call(context, [asset], "project-1", 1);

  assert.equal(context.data.items[0].thumbnailState, "failed");
  assert.equal(context.data.items[0].thumbnailError, "图片下载超时");
  assert.equal(context.data.items[0].localPath, "");

  global.wx = {
    previewImage(options) {
      previews.push(options);
    },
  };
  await page.previewAsset.call(context, {
    currentTarget: { dataset: { id: "asset-1" } },
  });

  assert.equal(attempts, 2);
  assert.equal(context.data.items[0].thumbnailState, "ready");
  assert.equal(context.data.items[0].localPath, "/tmp/asset-1-thumb.jpg");
  assert.deepEqual(previews, [{
    current: "/tmp/asset-1-thumb.jpg",
    urls: ["/tmp/asset-1-thumb.jpg"],
  }]);
});

test("image assets page turns a native image decode failure into a retry state", () => {
  const asset = imageAsset("asset-1");
  const { page } = loadImageAssetsPage();
  const context = pageContext(page, {
    projectId: "project-1",
    items: [imageAssetView(asset, "/tmp/asset-1-thumb.jpg")],
  });

  page.onThumbnailError.call(context, {
    currentTarget: { dataset: { id: "asset-1" } },
  });

  assert.equal(context.data.items[0].thumbnailState, "failed");
  assert.equal(context.data.items[0].localPath, "");
  assert.match(context.data.items[0].thumbnailError, /点按重试/);
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
      source_utterance_ids: ["utterance-1"],
      replace_body_placements: true,
      inline_position: "auto",
      display_size: "full_width",
    },
  }]);
  assert.equal(context.data.error, "");
});

test("image placement lets an author choose a manual position or return to automatic", async () => {
  const inputs = [];
  const asset = imageAsset("asset-1", { source_utterance_id: "utterance-1" });
  const { page } = loadImageAssetsPage({ createMemoirBookImagePlacement: async (_pid, _aid, input) => { inputs.push(input.inline_position); return asset; } });
  global.wx = { showToast() {} };
  const context = pageContext(page, { projectId: "project-1", items: [imageAssetView(asset)] });
  const event = { currentTarget: { dataset: { id: "asset-1" } } };
  context.toggleAssetDetails(event);
  assert.equal(context.data.items[0].detailsExpanded, true);
  for (const value of ["1", "2", "3", "0"]) await context.chooseSourcePosition({ ...event, detail: { value } });
  assert.deepEqual(inputs, ["before", "middle", "after", "auto"]);
});


test("excerpt images use recovered sources and become placed after saving", async () => {
  const asset = imageAsset("excerpt", { suggested_source_utterance_ids: ["source-a", "source-b"] });
  const calls = [];
  const { page } = loadImageAssetsPage({
    listMemoirImageAssets: async () => ({ items: [asset], total: 1, included_count: 0, unused_count: 1 }),
    downloadMemoirImageAssetFile: async () => "/tmp/excerpt.jpg",
    createMemoirBookImagePlacement: async (_project, _asset, input) => {
      calls.push(input);
      return { ...asset, include_in_book: true, placements: [{ id: "placed", enabled: true,
        placement_type: "chapter_inline", source_utterance_ids: input.source_utterance_ids,
        chapter_id: "chapter-1", chapter_title: "童年", inline_position: "auto" }] };
    },
  });
  global.wx = { showToast() {} };
  const context = pageContext(page, { projectId: "project-1" });
  await context.loadImageAssets();
  await context.placeNearSource({ currentTarget: { dataset: { id: asset.id } } });
  assert.deepEqual(calls[0].source_utterance_ids, ["source-a", "source-b"]);
  assert.equal(calls[0].replace_body_placements, true);
  assert.equal(context.data.items[0].include_in_book, true);
  assert.equal(context.data.includedCount, 1);
  assert.equal(context.data.items[0].detailsExpanded, false);
  await context.placeNearSource({ currentTarget: { dataset: { id: asset.id } } });
  assert.equal(calls.length, 1);
});

test("unresolved images require a source choice and replace the body placement", async () => {
  const asset = imageAsset("unresolved", { suggested_source_utterance_ids: [] });
  const calls = [];
  const { page } = loadImageAssetsPage({
    listMemoirImageAssets: async () => ({ items: [asset], total: 1, included_count: 0, unused_count: 1 }),
    downloadMemoirImageAssetFile: async () => "/tmp/unresolved.jpg",
    getMemoirBookPlan: async () => ({ chapters: [{ id: "chapter", title: "童年", include_in_book: true, sort_order: 0,
      sections: [{ id: "section", title: "院子", status: "active", include_in_book: true, sort_order: 0,
        materials: [{ utterance_id: "chosen-source", text: "院子里的故事", include_in_book: true, sort_order: 0 },
          { utterance_id: "excluded", text: "未收录素材", include_in_book: false, sort_order: 1 }] }] }] }),
    createMemoirBookImagePlacement: async (_p, _a, data) => { calls.push(data); return asset; },
  });
  global.wx = { showToast() {} };
  const context = pageContext(page, { projectId: "project-1" });
  await context.loadImageAssets();
  assert.deepEqual(context.data.materialOptions.map(item => item.value), ["chosen-source"]);
  const event = { currentTarget: { dataset: { id: asset.id } } };
  await context.placeNearSource(event);
  assert.equal(calls.length, 0);
  assert.equal(context.data.items[0].detailsExpanded, true);
  await context.chooseSourceMaterial({ ...event, detail: { value: "0" } });
  assert.deepEqual(calls[0], { placement_type: "chapter_inline", source_utterance_ids: ["chosen-source"],
    inline_position: "auto", display_size: "full_width", replace_body_placements: true });
  await context.placeInAppendix(event);
  assert.equal(calls[1].placement_type, "appendix");
  assert.equal(calls[1].source_utterance_ids, undefined);
  assert.equal(calls[1].replace_body_placements, true);
});
