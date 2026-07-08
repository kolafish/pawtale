const HOME_SHARE_QUERY = "from=share_home";
const BOOK_SHARE_QUERY = "from=share_book";

const HOME_SHARE_TITLE = "余主任留声机，把家里的故事慢慢讲下来";
const HOME_SHARE_PATH = `/pages/home/index?${HOME_SHARE_QUERY}`;
const BOOK_SHARE_PATH = `/pages/book/index?${BOOK_SHARE_QUERY}`;
const SHARE_TITLE_LIMIT = 18;

interface BookShareInput {
  projectTitle?: string;
  bookTitle?: string;
  hasBookContent?: boolean;
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

export function enableMiniProgramShareMenu(): void {
  if (typeof wx === "undefined" || typeof wx.showShareMenu !== "function") {
    return;
  }
  wx.showShareMenu({
    menus: ["shareAppMessage", "shareTimeline"],
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
    title: input.hasBookContent ? `《${title}》正在整理成书` : `一起整理《${title}》的书稿`,
    path: BOOK_SHARE_PATH,
  };
}

export function bookShareTimeline(input: BookShareInput = {}): WechatMiniprogram.ShareTimelinePayload {
  const title = bookTitle(input);
  return {
    title: input.hasBookContent ? `《${title}》正在整理成书` : `一起整理《${title}》的书稿`,
    query: BOOK_SHARE_QUERY,
  };
}
