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

test("image assets page keeps an unsaved caption through placement and clears it after saving", async () => {
  const asset = imageAsset("asset-1", { caption: "原说明" });
  const { page } = loadImageAssetsPage({
    createMemoirBookImagePlacement: async () => ({ ...asset, include_in_book: true }),
    updateMemoirImageAsset: async (_projectId, _assetId, input) => ({ ...asset, caption: input.caption }),
  });
  global.wx = { showToast() {} };
  const context = pageContext(page, {
    projectId: "project-1",
    items: [imageAssetView(asset)],
  });
  const event = { currentTarget: { dataset: { id: "asset-1" } } };

  context.onCaptionInput({ ...event, detail: { value: "新说明" } });
  await context.placeNearSource(event);
  assert.equal(context.data.items[0].captionDraft, "新说明");
  assert.equal(context.data.items[0].captionChanged, true);

  await context.saveCaption(event);
  assert.equal(context.data.items[0].captionDraft, "新说明");
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
