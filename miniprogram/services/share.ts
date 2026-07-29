const HOME_SHARE_QUERY = "from=share_home";
const BOOK_SHARE_QUERY = "from=share_book";

const HOME_SHARE_TITLE = "老余口述，把家里的故事慢慢讲下来";
const HOME_SHARE_PATH = `/pages/home/index?${HOME_SHARE_QUERY}`;
const BOOK_SHARE_FALLBACK_PATH = `/pages/book/index?${BOOK_SHARE_QUERY}`;
const SHARE_TITLE_LIMIT = 18;

interface BookShareInput {
  projectId?: string;
  projectTitle?: string;
  bookTitle?: string;
  hasBookContent?: boolean;
}

interface ShareMenuOptions {
  includeTimeline?: boolean;
}

interface LocalFileShareInput {
  filePath: string;
  fileName?: string;
}

export class ShareFileMessageCancelError extends Error {
  constructor(message = "已取消分享") {
    super(message);
  }
}

function shortTitle(value: string | undefined, fallback: string): string {
  const title = (value || "").trim() || fallback;
  if (title.length <= SHARE_TITLE_LIMIT) {
    return title;
  }
  return `${title.slice(0, SHARE_TITLE_LIMIT)}...`;
}

function bookTitle(input: BookShareInput): string {
  return shortTitle(input.bookTitle || input.projectTitle, "我的回忆录");
}

function encodedProjectQuery(projectId: string): string {
  return `projectId=${encodeURIComponent(projectId)}&${BOOK_SHARE_QUERY}`;
}

export function bookReaderSharePath(projectId: string): string {
  return `/pages/book-reader/index?${encodedProjectQuery(projectId)}`;
}

function bookSharePath(input: BookShareInput): string {
  return input.hasBookContent && input.projectId
    ? bookReaderSharePath(input.projectId)
    : BOOK_SHARE_FALLBACK_PATH;
}

export function enableMiniProgramShareMenu(options: ShareMenuOptions = {}): void {
  if (typeof wx === "undefined" || typeof wx.showShareMenu !== "function") {
    return;
  }
  const menus: Array<"shareAppMessage" | "shareTimeline"> = options.includeTimeline === false
    ? ["shareAppMessage"]
    : ["shareAppMessage", "shareTimeline"];
  wx.showShareMenu({
    menus,
  });
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

export function bookShareAppMessage(input: BookShareInput = {}): WechatMiniprogram.ShareAppMessagePayload {
  const title = bookTitle(input);
  return {
    title: input.hasBookContent ? `送你一本《${title}》` : `一起整理《${title}》的书稿`,
    path: bookSharePath(input),
  };
}

export function bookShareTimeline(input: BookShareInput = {}): WechatMiniprogram.ShareTimelinePayload {
  const title = bookTitle(input);
  const query = input.hasBookContent && input.projectId
    ? encodedProjectQuery(input.projectId)
    : BOOK_SHARE_QUERY;
  return {
    title: input.hasBookContent ? `送你一本《${title}》` : `一起整理《${title}》的书稿`,
    query,
  };
}
