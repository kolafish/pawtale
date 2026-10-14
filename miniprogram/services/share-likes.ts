import type {
  MemoirShareLikeActivityItem,
  MemoirShareLikes,
  MemoirShareLikeSummary,
  MemoirShareLikeTarget,
} from "../domain/memoir-share-likes";
import { loginWithWeChat, needsWeChatProfileSetup } from "./auth";
import { listMemoirShareLikeActivity, readMemoirShareLikes, setMemoirShareLike } from "./memoir-share-like-api";
import { ApiError } from "./request";
import { resolveBackendAvatarUrl } from "./user-profile";

const AVATAR_LIMIT = 8;
const PROFILE_PROMPT_STORAGE_KEY = "pawtale_share_like_profile_prompted";
const ACTIVITY_SEEN_STORAGE_KEY = "pawtale_share_like_activity_seen";

export interface ShareLikeAvatar {
  key: string;
  url: string;
}

export interface ShareLikerView {
  key: string;
  url: string;
  name: string;
}

// The author's full list: every liker with a picture and a name.
export function shareLikerViews(likers: MemoirShareLikeSummary["likers"]): ShareLikerView[] {
  return likers.map((liker, index) => ({
    key: String(index),
    url: resolveBackendAvatarUrl(liker.avatar_url),
    name: liker.display_name.trim() || "未留名",
  }));
}

export interface ShareLikePeople {
  avatars: ShareLikeAvatar[];
  text: string;
}

function likerNames(summary: Pick<MemoirShareLikeSummary, "likers">): string[] {
  return summary.likers.map((liker) => liker.display_name.trim()).filter(Boolean).slice(0, 2);
}

export function shareLikePeople(summary: MemoirShareLikeSummary, emptyText = ""): ShareLikePeople {
  const names = likerNames(summary);
  let text = emptyText;
  if (summary.count && !names.length) {
    text = `${summary.count} 位家人觉得好`;
  } else if (summary.count && summary.count <= names.length) {
    text = `${names.join("、")}觉得好`;
  } else if (summary.count) {
    text = `${names.join("、")}等 ${summary.count} 位家人觉得好`;
  }
  return {
    avatars: summary.likers.slice(0, AVATAR_LIMIT).map((liker, index) => ({
      key: String(index),
      url: resolveBackendAvatarUrl(liker.avatar_url),
    })),
    text,
  };
}

export interface ShareLikeData {
  shareLikeVisible: boolean;
  shareLiked: boolean;
  shareCanLike: boolean;
  shareLikeBusy: boolean;
  shareLikeAvatars: ShareLikeAvatar[];
  shareLikeText: string;
  // The visitor liked anonymously; the author would only see "未留名".
  shareLikeProfileMissing: boolean;
}

export const INITIAL_SHARE_LIKE_DATA: ShareLikeData = {
  shareLikeVisible: false,
  shareLiked: false,
  shareCanLike: false,
  shareLikeBusy: false,
  shareLikeAvatars: [],
  shareLikeText: "",
  shareLikeProfileMissing: false,
};

function shareLikePatch(likes: MemoirShareLikes): Partial<ShareLikeData> {
  const people = shareLikePeople(likes, likes.can_like ? "觉得好，就点个赞让讲故事的人知道" : "还没有家人点赞");
  return {
    shareLikeVisible: true,
    shareLiked: likes.liked,
    shareCanLike: likes.can_like,
    shareLikeAvatars: people.avatars,
    shareLikeText: people.text,
    shareLikeProfileMissing: Boolean(likes.profile_missing),
  };
}

async function signInForLike(): Promise<boolean> {
  // wx.login needs no prompt; returns whether the new account still lacks an avatar or name.
  return needsWeChatProfileSetup((await loginWithWeChat()).user);
}

function profilePromptShown(): boolean {
  try {
    return Boolean(wx.getStorageSync<boolean>(PROFILE_PROMPT_STORAGE_KEY));
  } catch {
    return false;
  }
}

function rememberProfilePrompt(): void {
  try {
    wx.setStorageSync(PROFILE_PROMPT_STORAGE_KEY, true);
  } catch {
    // Asking again next time is harmless.
  }
}

interface ShareLikeHost {
  data: ShareLikeData;
  shareLikeTarget: MemoirShareLikeTarget | null;
  setData(patch: Partial<ShareLikeData>): void;
}

function defineShareLikeMethods<T extends object>(methods: T & ThisType<ShareLikeHost & T>): T {
  return methods;
}

export const shareLikeMethods = defineShareLikeMethods({
  async loadShareLikes(target: MemoirShareLikeTarget) {
    this.shareLikeTarget = target;
    try {
      const likes = await readMemoirShareLikes(target);
      if (this.shareLikeTarget === target) this.setData(shareLikePatch(likes));
    } catch (error) {
      // Reading or watching works without likes.
      console.warn("share likes unavailable", error);
    }
  },

  openShareLikeProfile() {
    wx.navigateTo({ url: "/pages/login/index?from=like" });
  },

  refreshShareLikes() {
    if (this.shareLikeTarget) void this.loadShareLikes(this.shareLikeTarget);
  },

  async toggleShareLike() {
    const target = this.shareLikeTarget;
    if (!target || this.data.shareLikeBusy || !this.data.shareCanLike) return;
    const liked = !this.data.shareLiked;
    let needsProfile = false;
    this.setData({ shareLikeBusy: true });
    try {
      let likes: MemoirShareLikes;
      try {
        likes = await setMemoirShareLike(target, liked);
      } catch (error) {
        // Visitors are signed in by the tap itself; nothing is asked before they choose to like.
        if (!(error instanceof ApiError) || error.statusCode !== 401) throw error;
        needsProfile = await signInForLike();
        likes = await setMemoirShareLike(target, liked);
      }
      if (this.shareLikeTarget === target) this.setData(shareLikePatch(likes));
    } catch (error) {
      wx.showToast({ title: error instanceof Error && error.message ? error.message : "点赞没有成功，请重试", icon: "none" });
      return;
    } finally {
      this.setData({ shareLikeBusy: false });
    }
    if (liked && needsProfile && !profilePromptShown()) {
      rememberProfilePrompt();
      wx.navigateTo({ url: "/pages/login/index?from=like" });
    }
  },
});

export interface ShareLikeNotice extends ShareLikePeople {
  latestAt: string;
}

function activitySeenKey(userId: string, projectId: string): string {
  return `${userId}:${projectId}`;
}

function readActivitySeen(): Record<string, string> {
  try {
    return wx.getStorageSync<Record<string, string>>(ACTIVITY_SEEN_STORAGE_KEY) || {};
  } catch {
    return {};
  }
}

function activityTarget(item: MemoirShareLikeActivityItem): string {
  const title = item.target_title.trim();
  return title ? `《${title}》` : "您的故事";
}

export function shareLikeNotice(items: MemoirShareLikeActivityItem[]): ShareLikeNotice | null {
  if (!items.length) return null;
  // One relative may like several stories; name and picture them once.
  const likers = [...new Map(items.map((item) => [`${item.liker.display_name}|${item.liker.avatar_url || ""}`, item.liker])).values()];
  const names = likerNames({ likers });
  const targets = new Set(items.map(activityTarget));
  const target = targets.size === 1 ? [...targets][0] : "您的故事";
  const who = names.length ? `${names.join("、")}${likers.length > names.length ? "等家人" : ""}` : "家人";
  return {
    avatars: shareLikePeople({ count: likers.length, likers }).avatars,
    text: items.length === 1 ? `${who}给${target}点了赞` : `${who}给${target}点了 ${items.length} 个赞`,
    latestAt: items[0].created_at,
  };
}

// The author hears about likes that arrived since they last tapped "知道了" on this device.
export async function loadShareLikeNotice(userId: string, projectId: string): Promise<ShareLikeNotice | null> {
  const after = readActivitySeen()[activitySeenKey(userId, projectId)] || "";
  return shareLikeNotice((await listMemoirShareLikeActivity(projectId, after)).items);
}

export function markShareLikeNoticeSeen(userId: string, projectId: string, latestAt: string): void {
  try {
    wx.setStorageSync(ACTIVITY_SEEN_STORAGE_KEY, { ...readActivitySeen(), [activitySeenKey(userId, projectId)]: latestAt });
  } catch {
    // The notice simply shows again.
  }
}
