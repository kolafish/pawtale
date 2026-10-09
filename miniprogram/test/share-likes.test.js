require("./register-ts");

const assert = require("node:assert/strict");
const test = require("node:test");
const { ApiError } = require("../services/request.ts");
const {
  clearModule,
  loadPageModule,
  mockMemoirApiModules,
  mockModule,
  pageContext,
  unmockedAsync,
} = require("./page-harness");

const CODE = "0123456789abcdef0123";

function loadPlayer({ likeApi = {}, loginWithWeChat = unmockedAsync("loginWithWeChat") } = {}) {
  mockMemoirApiModules({
    readMemoirSharedVideo: async () => ({
      title: "外婆家的冬天",
      book_title: "我们家的故事",
      narrator_name: "",
      duration_ms: 28000,
      video_url: "/video",
      poster_url: "/poster",
      end_frame_url: "",
      book_share_token: null,
    }),
    memoirShareVideoDownloadUrl: (url) => url,
    memoirSharedVideoUrl: (code, variant) => `/shares/${code}/${variant}`,
    readMemoirShareLikes: unmockedAsync("readMemoirShareLikes"),
    setMemoirShareLike: unmockedAsync("setMemoirShareLike"),
    ...likeApi,
  });
  mockModule("../services/auth.ts", {
    loginWithWeChat,
    needsWeChatProfileSetup: (user) => !user.avatar_url,
  });
  mockModule("../services/user-profile.ts", {
    resolveBackendAvatarUrl: (url) => (url ? `https://api.example.com${url}` : ""),
  });
  clearModule("../services/share-likes.ts");
  return loadPageModule("../pages/share-video-player/index.ts");
}

function likes(patch = {}) {
  return { count: 0, likers: [], liked: false, can_like: true, ...patch };
}

function installWx(navigations, storage = {}) {
  global.wx = {
    setNavigationBarTitle() {},
    showShareMenu() {},
    showToast() {},
    navigateTo: ({ url }) => navigations.push(url),
    getStorageSync: (key) => storage[key],
    setStorageSync: (key, value) => { storage[key] = value; },
  };
}

test("a visitor likes a forwarded video by tapping, then is asked once for avatar and name", async () => {
  const navigations = [];
  const storage = {};
  installWx(navigations, storage);
  const calls = [];
  let signedIn = false;
  const { page } = loadPlayer({
    likeApi: {
      readMemoirShareLikes: async (target) => {
        calls.push(["read", target]);
        return likes({ count: 1, likers: [{ display_name: "三姑", avatar_url: "/api/auth/avatars/a/1.jpg" }] });
      },
      setMemoirShareLike: async (target, liked) => {
        calls.push(["set", target, liked]);
        if (!signedIn) throw new ApiError(401, "请先登录", {});
        return likes({
          count: liked ? 2 : 1,
          liked,
          profile_missing: liked,
          likers: [
            ...(liked ? [{ display_name: "", avatar_url: null }] : []),
            { display_name: "三姑", avatar_url: "/api/auth/avatars/a/1.jpg" },
          ],
        });
      },
    },
    loginWithWeChat: async () => {
      signedIn = true;
      return { user: { display_name: "微信用户", avatar_url: null } };
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { code: CODE });
  await Promise.resolve();
  assert.equal(context.data.shareLikeVisible, true);
  assert.equal(context.data.shareCanLike, true);
  assert.equal(context.data.shareLikeText, "三姑觉得好");
  assert.deepEqual(context.data.shareLikeAvatars, [{ key: "0", url: "https://api.example.com/api/auth/avatars/a/1.jpg" }]);

  await page.toggleShareLike.call(context);
  assert.deepEqual(calls.slice(1), [
    ["set", { video_code: CODE }, true],
    ["set", { video_code: CODE }, true],
  ]);
  assert.equal(context.data.shareLiked, true);
  assert.equal(context.data.shareLikeBusy, false);
  assert.equal(context.data.shareLikeText, "三姑等 2 位家人觉得好");
  assert.deepEqual(context.data.shareLikeAvatars.map((avatar) => avatar.url), ["", "https://api.example.com/api/auth/avatars/a/1.jpg"]);
  assert.deepEqual(navigations, ["/pages/login/index?from=like"]);
  // Skipping the form leaves a way back to it, since the author would only see "未留名".
  assert.equal(context.data.shareLikeProfileMissing, true);
  page.openShareLikeProfile.call(context);
  assert.deepEqual(navigations, ["/pages/login/index?from=like", "/pages/login/index?from=like"]);
  navigations.length = 1;

  // Unliking and liking again never asks a second time.
  signedIn = false;
  await page.toggleShareLike.call(context);
  await page.toggleShareLike.call(context);
  assert.equal(context.data.shareLiked, true);
  assert.deepEqual(navigations, ["/pages/login/index?from=like"]);
});

test("the author sees who liked their video without a like button", async () => {
  installWx([]);
  const { page } = loadPlayer({
    likeApi: {
      readMemoirShareLikes: async () => likes({ can_like: false }),
      setMemoirShareLike: async () => {
        throw new Error("the author cannot like their own video");
      },
    },
  });
  const context = pageContext(page);

  await page.onLoad.call(context, { code: CODE });
  await Promise.resolve();
  assert.equal(context.data.shareCanLike, false);
  assert.equal(context.data.shareLikeText, "还没有家人点赞");
  await page.toggleShareLike.call(context);
  assert.equal(context.data.shareLiked, false);
});

test("the home notice names who liked which story", () => {
  mockModule("../services/user-profile.ts", { resolveBackendAvatarUrl: (url) => url || "" });
  clearModule("../services/share-likes.ts");
  const { shareLikeNotice } = require("../services/share-likes.ts");
  const like = (id, name, kind, title) => ({
    id,
    target_kind: kind,
    target_title: title,
    liker: { display_name: name, avatar_url: name ? `/${name}.jpg` : null },
    created_at: `2026-10-09T08:0${id}:00`,
  });

  assert.equal(shareLikeNotice([]), null);
  assert.deepEqual(shareLikeNotice([like(3, "三姑", "chapter", "家里的人")]), {
    avatars: [{ key: "0", url: "/三姑.jpg" }],
    text: "三姑给《家里的人》点了赞",
    latestAt: "2026-10-09T08:03:00",
  });
  const notice = shareLikeNotice([
    like(3, "三姑", "video", "外婆家的冬天"),
    like(2, "三姑", "chapter", "家里的人"),
    like(1, "小军", "book", "余仲尤往事"),
    like(0, "", "book", "余仲尤往事"),
  ]);
  assert.equal(notice.text, "三姑、小军等家人给你的故事点了 4 个赞");
  assert.equal(notice.avatars.length, 3);
  assert.equal(notice.latestAt, "2026-10-09T08:03:00");
});
