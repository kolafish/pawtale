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
