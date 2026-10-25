require("./register-ts");

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
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

test("book and chapter share panels receive the full liker list", () => {
  for (const page of ["book", "chapter-workspace"]) {
    const template = fs.readFileSync(path.join(__dirname, `../pages/${page}/index.wxml`), "utf8");
    const binding = template.match(/<template[^>]*\bis="readingShareActions"\s+data="([^"]+)"/);
    assert.ok(binding, `${page} must render the reading share panel`);
    assert.match(binding[1], /\bbookShareLikeCount\b/, `${page} must pass the like count`);
    assert.match(binding[1], /\bbookShareLikers\b/, `${page} must pass the full liker list`);
  }
});

function loadPlayer({ likeApi = {}, loginWithWeChat = unmockedAsync("loginWithWeChat"), reading = false } = {}) {
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
  return loadPageModule(reading ? "../pages/book-reader/index.ts" : "../pages/share-video-player/index.ts");
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

test("a visitor must consent before video-like authentication, then can complete the like and profile flow", async () => {
  const navigations = [];
  const storage = {};
  installWx(navigations, storage);
  const calls = [];
  let signedIn = false;
  let currentLikes = likes({ count: 1, likers: [{ display_name: "三姑", avatar_url: "/api/auth/avatars/a/1.jpg" }] });
  const { page } = loadPlayer({
    likeApi: {
      readMemoirShareLikes: async (target) => {
        calls.push(["read", target]);
        return currentLikes;
      },
      setMemoirShareLike: async (target, liked) => {
        calls.push(["set", target, liked]);
        if (!signedIn) throw new ApiError(401, "请先登录", {});
        currentLikes = likes({
          count: liked ? 2 : 1,
          liked,
          profile_missing: liked,
          likers: [
            ...(liked ? [{ display_name: "", avatar_url: null }] : []),
            { display_name: "三姑", avatar_url: "/api/auth/avatars/a/1.jpg" },
          ],
        });
        return currentLikes;
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
  assert.equal(signedIn, false);
  assert.deepEqual(calls.slice(1), [["set", { video_code: CODE }, true]]);
  assert.equal(context.data.shareLiked, false);
  assert.equal(context.data.shareLikeBusy, false);
  assert.equal(navigations.length, 1);
  const loginUrl = new URL(navigations[0], "https://miniprogram.example");
  assert.equal(loginUrl.pathname, "/pages/login/index");

  const { page: login } = loadPageModule("../pages/login/index.ts");
  const loginContext = pageContext(login);
  login.onLoad.call(loginContext, Object.fromEntries(loginUrl.searchParams));
  assert.equal(loginContext.data.profileMode, false);
  assert.equal(loginContext.data.privacyAgreed, false);
  await loginContext.submitWeChat();
  assert.equal(signedIn, false);
  assert.equal(calls.length, 2);

  // Returning without consent never resumes the pending like.
  await context.loadShareLikes(context.shareLikeTarget);
  assert.equal(context.data.shareLiked, false);
  assert.equal(signedIn, false);

  loginContext.changePrivacyConsent({ detail: { value: ["privacy_contract"] } });
  await loginContext.submitWeChat();
  assert.equal(signedIn, true);
  assert.equal(loginContext.data.profileMode, true);
  assert.equal(loginContext.data.wechatLoading, false);
  const backs = [];
  global.wx.navigateBack = () => backs.push(true);
  loginContext.skipWeChatProfile();
  assert.deepEqual(backs, [true]);
  // The share page reloads the persisted like after returning from login/profile setup.
  await context.loadShareLikes(context.shareLikeTarget);
  const completedLike = calls.filter(([method]) => method === "set");
  assert.deepEqual(completedLike, [
    ["set", { video_code: CODE }, true],
    ["set", { video_code: CODE }, true],
  ]);

  assert.equal(context.data.shareLiked, true);
  assert.equal(context.data.shareLikeText, "三姑等 2 位家人觉得好");
  assert.deepEqual(context.data.shareLikeAvatars.map((avatar) => avatar.url), ["", "https://api.example.com/api/auth/avatars/a/1.jpg"]);
  assert.equal(navigations.length, 1);
  // Skipping the form leaves a way back to it, since the author would only see "未留名".
  assert.equal(context.data.shareLikeProfileMissing, true);
  page.openShareLikeProfile.call(context);
  assert.equal(navigations[1], "/pages/login/index?from=like");
  navigations.length = 1;

  // Unliking and liking again never asks a second time.
  await page.toggleShareLike.call(context);
  await page.toggleShareLike.call(context);
  assert.equal(context.data.shareLiked, true);
  assert.equal(navigations.length, 1);
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

test("a shared-book visitor consents before authentication and can retry a failed like without losing the target", async () => {
  const { sampleBookOutput } = require("./book-fixtures");
  const navigations = [];
  const backs = [];
  installWx(navigations);
  global.wx.navigateBack = () => backs.push(true);
  global.wx.reLaunch = () => assert.fail("share login must return to the shared book");
  let loginCalls = 0;
  let liked = false;
  let failLike = true;
  const mutations = [];
  const { page } = loadPlayer({
    reading: true,
    likeApi: {
      readMemoirBookShare: async () => sampleBookOutput(),
      readMemoirShareLikes: async () => likes({ liked }),
      setMemoirShareLike: async (target, value) => {
        if (!loginCalls) throw new ApiError(401, "请先登录", {});
        mutations.push([target, value]);
        if (failLike) throw new ApiError(503, "点赞暂时不可用", {});
        liked = value;
        return likes({ liked });
      },
    },
    loginWithWeChat: async () => {
      loginCalls += 1;
      return { user: { display_name: "三姑", avatar_url: "/avatar.jpg" } };
    },
  });
  const context = pageContext(page);
  await context.onLoad({ shareToken: "family-token" });
  await context.toggleShareLike();
  assert.equal(loginCalls, 0);
  assert.equal(liked, false);

  const loginUrl = new URL(navigations[0], "https://miniprogram.example");
  const { page: login } = loadPageModule("../pages/login/index.ts");
  const loginContext = pageContext(login);
  loginContext.onLoad(Object.fromEntries(loginUrl.searchParams));
  assert.equal(loginContext.data.profileMode, false);
  await loginContext.submitWeChat();
  assert.equal(loginCalls, 0);
  assert.deepEqual(mutations, []);

  loginContext.changePrivacyConsent({ detail: { value: ["privacy_contract"] } });
  await loginContext.submitWeChat();
  assert.match(loginContext.data.error, /点赞暂时不可用/);
  assert.equal(liked, false);
  assert.equal(loginContext.data.wechatLoading, false);
  assert.deepEqual(backs, []);

  failLike = false;
  await loginContext.submitWeChat();
  assert.deepEqual(mutations, [
    [{ reading_token: "family-token" }, true],
    [{ reading_token: "family-token" }, true],
  ]);
  assert.equal(loginContext.data.profileMode, false);
  assert.equal(loginContext.data.error, "");
  assert.deepEqual(backs, [true]);
  await context.loadShareLikes(context.shareLikeTarget);
  assert.equal(context.data.shareLiked, true);
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
  assert.equal(notice.text, "三姑、小军等家人给您的故事点了 4 个赞");
  assert.equal(notice.avatars.length, 3);
  assert.equal(notice.latestAt, "2026-10-09T08:03:00");
});


test("a revoked share clears already loaded video when returning to the page", async () => {
  installWx([]);
  let revoked = false;
  const { page } = loadPlayer({ likeApi: { readMemoirShareLikes: async () => {
    if (revoked) throw new ApiError(404, "作者已停止分享", {});
    return likes();
  } } });
  const context = pageContext(page);
  await context.onLoad({ code: CODE });
  await context.loadShareLikes({ video_code: CODE });
  assert.ok(context.data.videoUrl);
  revoked = true;
  await context.loadShareLikes(context.shareLikeTarget);
  assert.equal(context.data.videoUrl, "");
  assert.equal(context.data.video, null);
  assert.equal(context.data.shareLikeVisible, false);
  assert.equal(context.data.error, "作者已停止分享");
  delete global.wx;
});
