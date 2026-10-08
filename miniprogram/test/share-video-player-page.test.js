require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { loadPageModule, mockMemoirApiModules, pageContext } = require("./page-harness");

function loadPlayer(readMemoirSharedVideo) {
  mockMemoirApiModules({
    readMemoirSharedVideo,
    memoirShareVideoDownloadUrl: (url) => `https://api.example.com${url}`,
    memoirSharedVideoUrl: (code, variant) => `https://api.example.com/shares/${code}/${variant}`,
  });
  return loadPageModule("../pages/share-video-player/index.ts");
}

const sharedVideo = {
  title: "外婆家的冬天",
  book_title: "我们家的故事",
  narrator_name: "余老师",
  duration_ms: 28000,
  video_url: "/api/mobile/memoir/share-video/shares/abc/video",
  poster_url: "/api/mobile/memoir/share-video/shares/abc/video?variant=poster",
  end_frame_url: "/api/mobile/memoir/share-video/shares/abc/video?variant=end",
  book_share_token: "book-token",
};

test("a scanned mini program code opens the clip and forwards the same card", async () => {
  const codes = [];
  const navigations = [];
  const navigationTitles = [];
  global.wx = {
    navigateTo: ({ url }) => navigations.push(url),
    setNavigationBarTitle: ({ title }) => navigationTitles.push(title),
  };
  try {
    const { page } = loadPlayer(async (code) => {
      codes.push(code);
      return sharedVideo;
    });
    const context = pageContext(page);

    await page.onLoad.call(context, { scene: "0123456789abcdef0123" });

    assert.deepEqual(codes, ["0123456789abcdef0123"]);
    assert.equal(context.data.videoUrl, "https://api.example.com/api/mobile/memoir/share-video/shares/abc/video");
    assert.equal(context.data.error, "");
    assert.deepEqual(navigationTitles, [sharedVideo.book_title]);
    assert.deepEqual(page.onShareAppMessage.call(context), {
      title: "外婆家的冬天",
      path: "/pages/share-video-player/index?code=0123456789abcdef0123",
      // The forwarded card uses the 5:4 cover, not the small list thumbnail.
      imageUrl: "https://api.example.com/shares/0123456789abcdef0123/card",
    });
    assert.equal(context.data.posterUrl, "https://api.example.com/api/mobile/memoir/share-video/shares/abc/video?variant=poster");

    // Playback ends on the closing card instead of returning to the cover; replay starts over.
    page.holdEndFrame.call(context);
    assert.equal(context.data.ended, true);
    assert.equal(context.data.endFrameUrl, "https://api.example.com/api/mobile/memoir/share-video/shares/abc/video?variant=end");
    page.replayVideo.call(context);
    assert.equal(context.data.ended, false);

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
