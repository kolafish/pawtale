import { cleanBaseUrl, readBackendConfig } from "./request";

const HOME_SHARE_QUERY = "from=share_home";
const HOME_SHARE_TITLE = "老余口述，把家里的故事慢慢讲下来";
const HOME_SHARE_PATH = `/pages/home/index?${HOME_SHARE_QUERY}`;

interface LocalFileShareInput {
  filePath: string;
  fileName?: string;
}

export class ShareFileMessageCancelError extends Error {
  constructor(message = "已取消分享") {
    super(message);
  }
}

export function enableMiniProgramShareMenu(): void {
  if (typeof wx === "undefined" || typeof wx.showShareMenu !== "function") {
    return;
  }
  wx.showShareMenu({ menus: ["shareAppMessage", "shareTimeline"] });
}

export function hideMiniProgramShareMenu(): void {
  if (typeof wx !== "undefined" && typeof wx.hideShareMenu === "function") {
    wx.hideShareMenu();
  }
}

function shareFileError(error: WechatMiniprogram.GeneralCallbackResult): Error {
  const message = error.errMsg || "文件分享失败";
  if (message.toLowerCase().includes("cancel")) {
    return new ShareFileMessageCancelError();
  }
  return new Error(message);
}

export function isShareFileMessageCancelError(error: unknown): boolean {
  return error instanceof ShareFileMessageCancelError;
}

export function shareLocalFileMessage(input: LocalFileShareInput): Promise<void> {
  const shareFileMessage = typeof wx === "undefined" ? undefined : wx.shareFileMessage;
  if (typeof shareFileMessage !== "function") {
    return Promise.reject(new Error("当前微信版本不支持直接分享文件，请更新微信后重试"));
  }
  return new Promise((resolve, reject) => {
    shareFileMessage({
      filePath: input.filePath,
      ...(input.fileName ? { fileName: input.fileName } : {}),
      success: () => resolve(),
      fail: (error) => reject(shareFileError(error)),
    });
  });
}

export function saveVideoToAlbum(filePath: string): Promise<void> {
  const saveVideo = typeof wx === "undefined" ? undefined : wx.saveVideoToPhotosAlbum;
  if (typeof saveVideo !== "function") {
    return Promise.reject(new Error("当前微信版本不支持保存视频，请更新微信后重试"));
  }
  return new Promise((resolve, reject) => {
    saveVideo({
      filePath,
      success: () => resolve(),
      fail: (error) => {
        const message = (error.errMsg || "").toLowerCase();
        if (message.includes("cancel")) {
          reject(new ShareFileMessageCancelError());
        } else if (message.includes("auth") || message.includes("privacy")) {
          reject(new Error("没有相册权限，请在小程序设置里允许保存到相册后重试"));
        } else {
          reject(new Error(error.errMsg || "保存到相册失败"));
        }
      },
    });
  });
}

export function saveImageToAlbum(filePath: string): Promise<void> {
  return new Promise((resolve, reject) => wx.saveImageToPhotosAlbum({
    filePath,
    success: () => resolve(),
    fail: (error) => {
      const message = (error.errMsg || "").toLowerCase();
      if (message.includes("cancel")) reject(new ShareFileMessageCancelError());
      else if (message.includes("auth") || message.includes("privacy")) {
        reject(new Error("没有相册权限，请在小程序设置里允许保存到相册后重试"));
      } else reject(new Error(error.errMsg || "保存图片失败，请重试"));
    },
  }));
}

export function homeShareAppMessage(): WechatMiniprogram.ShareAppMessagePayload {
  return {
    title: HOME_SHARE_TITLE,
    path: HOME_SHARE_PATH,
  };
}

export function homeShareTimeline(): WechatMiniprogram.ShareTimelinePayload {
  return {
    title: HOME_SHARE_TITLE,
    query: HOME_SHARE_QUERY,
  };
}

export const SHARED_VIDEO_PAGE = "/pages/share-video-player/index";

export function sharedVideoShareMessage(
  title: string,
  shareCode: string,
  imageUrl = "",
): WechatMiniprogram.ShareAppMessagePayload {
  return {
    title: title.trim() || "一段家里人的口述",
    path: `${SHARED_VIDEO_PAGE}?code=${encodeURIComponent(shareCode)}`,
    ...(imageUrl ? { imageUrl } : {}),
  };
}


export interface ReadingShareExcerpt {
  text: string;
  start: number;
  length: number;
  versionId: string;
}

export function readingShareExcerpt(content: string, versionId: string): ReadingShareExcerpt | undefined {
  // Keep offsets in Unicode code points, matching the server (including emoji).
  // Skip headings and never invent a hook or draw from private source material.
  for (const match of content.matchAll(/[^\r\n]+/g)) {
    const paragraph = match[0].trim();
    if (!paragraph || /^#{1,6}\s/.test(paragraph)) continue;
    const sentence = paragraph.match(/^.*?[。！？.!?](?:[”’」』])?/)?.[0] || paragraph;
    const text = Array.from(sentence).slice(0, 48).join("");
    const offset = (match.index || 0) + match[0].indexOf(paragraph);
    return { text, start: Array.from(content.slice(0, offset)).length, length: Array.from(text).length, versionId };
  }
  return undefined;
}

export function readingShareImageUrl(token: string, excerpt?: Pick<ReadingShareExcerpt, "start" | "length" | "versionId">, versionId = ""): string {
  const base = cleanBaseUrl(readBackendConfig().httpBaseUrl);
  const version = excerpt?.versionId || versionId;
  const revision = version ? `&version_id=${encodeURIComponent(version)}` : "";
  const selection = excerpt ? `&excerpt_start=${excerpt.start}&excerpt_length=${excerpt.length}` : "";
  return `${base}/api/mobile/memoir/book-shares/${encodeURIComponent(token)}/card.jpg?design=3${revision}${selection}`;
}

export function excerptShareMessage(token: string, title: string): WechatMiniprogram.ShareAppMessagePayload {
  if (!token) return readingShareMessage("", "");
  return {
    title: title.trim() || "一段家里的故事",
    path: `/pages/book-reader/index?excerptToken=${encodeURIComponent(token)}`,
    imageUrl: `${cleanBaseUrl(readBackendConfig().httpBaseUrl)}/api/mobile/memoir/excerpt-shares/${encodeURIComponent(token)}/card.jpg`,
  };
}

export function readingShareMessage(title: string, token: string, imageUrl = ""): WechatMiniprogram.ShareAppMessagePayload {
  return token ? {
    title: title.trim() || "一段家里的故事",
    path: `/pages/book-reader/index?shareToken=${encodeURIComponent(token)}`,
    imageUrl: imageUrl || readingShareImageUrl(token),
  } : homeShareAppMessage();
}
