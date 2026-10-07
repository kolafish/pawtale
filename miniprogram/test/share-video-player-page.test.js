require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { loadPageModule, mockMemoirApiModules, pageContext } = require("./page-harness");

function loadPlayer(readMemoirSharedVideo) {
  mockMemoirApiModules({
    readMemoirSharedVideo,
    downloadMemoirSharedVideo: async (video) => `wxfile://tmp${video.video_url}`,
    memoirShareVideoDownloadUrl: (url) => `https://api.example.com${url}`,
  });
  return loadPageModule("../pages/share-video-player/index.ts");
}

const sharedVideo = {
  title: "外婆家的冬天",
  book_title: "我们家的故事",
  narrator_name: "余老师",
  duration_ms: 28000,
  video_url: "/api/mobile/memoir/share-video/shares/abc/video",
  poster_url: "/api/mobile/memoir/share-video/shares/abc/video?variant=thumbnail",
  book_share_token: "book-token",
};

test("a scanned mini program code opens the clip and forwards the same card", async () => {
  const codes = [];
  const navigations = [];
  global.wx = { navigateTo: ({ url }) => navigations.push(url) };
  try {
    const { page } = loadPlayer(async (code) => {
      codes.push(code);
      return sharedVideo;
    });
    const context = pageContext(page);

    await page.onLoad.call(context, { scene: "0123456789abcdef0123" });

    assert.deepEqual(codes, ["0123456789abcdef0123"]);
    assert.equal(context.data.videoUrl, "wxfile://tmp/api/mobile/memoir/share-video/shares/abc/video");
    assert.equal(context.data.error, "");
    assert.deepEqual(page.onShareAppMessage.call(context), {
      title: "外婆家的冬天",
      path: "/pages/share-video-player/index?code=0123456789abcdef0123",
      imageUrl: "https://api.example.com/api/mobile/memoir/share-video/shares/abc/video?variant=thumbnail",
    });
    page.openBook.call(context);
    assert.deepEqual(navigations, ["/pages/book-reader/index?shareToken=book-token"]);
  } finally {
    delete global.wx;
  }
});

test("a deleted clip explains itself instead of playing", async () => {
  const { page } = loadPlayer(async () => {
    throw new Error("这段视频已被作者删除");
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { code: "0123456789abcdef0123" });

  assert.equal(context.data.video, null);
  assert.equal(context.data.error, "这段视频已被作者删除");
  assert.equal(page.onShareAppMessage.call(context).path.startsWith("/pages/home/index"), true);
});
