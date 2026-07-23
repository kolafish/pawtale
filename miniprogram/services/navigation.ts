import { ApiError } from "./request";

export const HOME_PAGE_URL = "/pages/home/index";
export const BOOK_PAGE_URL = "/pages/book/index";
export const LOGIN_PAGE_URL = "/pages/login/index";
export const ME_PAGE_URL = "/pages/me/index";

export type AuthNavigationTarget = "login" | "me";

export function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && [401, 403].includes(error.statusCode);
}

export function switchToHome(): void {
  wx.switchTab({ url: HOME_PAGE_URL });
}

export function switchToBook(): void {
  wx.switchTab({ url: BOOK_PAGE_URL });
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
