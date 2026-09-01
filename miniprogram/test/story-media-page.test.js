require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  clearModule,
  loadPageModule,
  mockModule,
  pageContext,
} = require("./page-harness");

function snapshot() {
  return {
    identity: {
      active_project: { id: "project-1", title: "我的故事" },
    },
    plan: {
      chapters: [{
        id: "chapter-1",
        title: "童年",
        sort_order: 0,
        source_refs: [
          { utterance_id: "utterance-1" },
          { utterance_id: "utterance-2" },
        ],
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
    image_assets: {
      items: [
        {
          id: "image-section",
          status: "ready",
          title: "外婆家的院子",
          caption: "夏天的院子里种满了花。",
          source_utterance_id: "utterance-1",
          placements: [],
        },
        {
          id: "image-chapter",
          status: "ready",
          title: "童年旧照",
          caption: "",
          source_chapter_id: "chapter-1",
          placements: [],
        },
      ],
    },
    share_video_counts: [{
      utterance_id: "utterance-1",
      count: 2,
      job_ids: ["video-2", "video-1"],
    }],
  };
}

function loadPage() {
  const calls = {
    imageDownloads: [],
    videoJobs: [],
    videoDownloads: [],
  };
  mockModule("../services/memoir-snapshot-api.ts", {
    getMemoirBookSnapshot: async () => snapshot(),
  });
  mockModule("../services/memoir-book-api.ts", {
    downloadMemoirImageAssetFile: async (...args) => {
      calls.imageDownloads.push(args);
      return "/tmp/image-section.jpg";
    },
  });
  mockModule("../services/memoir-transcript-api.ts", {
    getMemoirShareVideoJob: async (id) => {
      calls.videoJobs.push(id);
      return { id, output: { download_url: `/videos/${id}` } };
    },
    downloadMemoirShareVideoJob: async (job) => {
      calls.videoDownloads.push(job.id);
      return `/tmp/${job.id}.mp4`;
    },
  });
  mockModule("../services/navigation.ts", { routeAuthError: () => false });
  clearModule("../pages/story/story-media-index.ts");
  const loaded = loadPageModule("../pages/story-media/index.ts");
  return { ...loaded, calls };
}

test("story media page shows and previews media assigned to one section", async () => {
  const previews = [];
  const navigationTitles = [];
  global.wx = {
    getStorageSync() { return ""; },
    setNavigationBarTitle({ title }) { navigationTitles.push(title); },
    previewImage(options) { previews.push(options); },
  };
  const { page, calls } = loadPage();
  const context = pageContext(page);

  await page.onLoad.call(context, {
    projectId: "project-1",
    scope: "section",
    chapterId: "chapter-1",
    sectionId: "section-1",
  });

  assert.equal(context.data.title, "外婆家的夏天");
  assert.deepEqual(context.data.images.map((item) => item.id), ["image-section"]);
  assert.deepEqual(context.data.videos.map((item) => item.id), ["video-2", "video-1"]);
  assert.deepEqual(navigationTitles, ["外婆家的夏天"]);

  await page.previewImage.call(context, {
    currentTarget: { dataset: { id: "image-section" } },
  });
  assert.deepEqual(calls.imageDownloads, [["project-1", "image-section"]]);
  assert.deepEqual(previews, [{
    current: "/tmp/image-section.jpg",
    urls: ["/tmp/image-section.jpg"],
  }]);

  await page.previewVideo.call(context, {
    currentTarget: { dataset: { id: "video-2" } },
  });
  assert.deepEqual(calls.videoJobs, ["video-2"]);
  assert.deepEqual(calls.videoDownloads, ["video-2"]);
  assert.equal(context.data.videoPreviewPath, "/tmp/video-2.mp4");
  assert.equal(context.data.videoPreviewTitle, "分享视频 1");

  page.closeVideoPreview.call(context);
  assert.equal(context.data.videoPreviewPath, "");
});
