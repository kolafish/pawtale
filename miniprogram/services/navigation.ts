import { ApiError } from "./request";

export const HOME_PAGE_URL = "/pages/home/index";
export const BOOK_PAGE_URL = "/pages/book/index";
export const TRANSCRIPTS_PAGE_URL = "/pages/transcripts/index";
export const LOGIN_PAGE_URL = "/pages/login/index";
export const ME_PAGE_URL = "/pages/me/index";
export const CHAPTER_WORKSPACE_PAGE_URL = "/pages/chapter-workspace/index";

export type AuthNavigationTarget = "login" | "me";
export type PageNotFoundQuery = Record<string, string | undefined>;

const LEGACY_PAGE_REDIRECTS: Record<string, string> = {
  "pages/book-chapter/index": CHAPTER_WORKSPACE_PAGE_URL,
};

function appendQuery(url: string, query: PageNotFoundQuery): string {
  const search = Object.entries(query)
    .filter((entry): entry is [string, string] => typeof entry[1] === "string")
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join("&");
  return search ? `${url}?${search}` : url;
}

export function missingPageRecoveryUrl(
  path: string,
  query: PageNotFoundQuery = {},
): string | null {
  const normalizedPath = path.replace(/^\/+/, "").split("?")[0];
  const legacyTarget = LEGACY_PAGE_REDIRECTS[normalizedPath];
  if (legacyTarget) {
    return appendQuery(legacyTarget, query);
  }
  return normalizedPath === HOME_PAGE_URL.slice(1) ? null : HOME_PAGE_URL;
}

export function recoverMissingPage(path: string, query: PageNotFoundQuery = {}): void {
  const url = missingPageRecoveryUrl(path, query);
  if (url) {
    wx.reLaunch({ url });
  }
}

export function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && [401, 403].includes(error.statusCode);
}

export function switchToHome(): void {
  wx.switchTab({ url: HOME_PAGE_URL });
}

export function navigateToBook(): void {
  wx.navigateTo({ url: BOOK_PAGE_URL });
}

export function redirectToBook(): void {
  wx.redirectTo({ url: BOOK_PAGE_URL });
}

export function navigateToTranscripts(): void {
  wx.navigateTo({ url: TRANSCRIPTS_PAGE_URL });
}

export function redirectToTranscripts(): void {
  wx.redirectTo({ url: TRANSCRIPTS_PAGE_URL });
}

export function relaunchHome(): void {
  wx.reLaunch({ url: HOME_PAGE_URL });
}

export function switchToMe(): void {
  wx.switchTab({ url: ME_PAGE_URL });
}

export function navigateToLogin(): void {
  wx.navigateTo({ url: LOGIN_PAGE_URL });
}

export function relaunchLogin(): void {
  wx.reLaunch({ url: LOGIN_PAGE_URL });
}

export function routeAuthError(error: unknown, target: AuthNavigationTarget = "login"): boolean {
  if (!isAuthError(error)) {
    return false;
  }
  if (target === "login") {
    relaunchLogin();
  } else {
    switchToMe();
  }
  return true;
}
