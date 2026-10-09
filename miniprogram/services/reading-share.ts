import { createMemoirBookShare, getMemoirBookShareStatus, revokeMemoirBookShare, updateMemoirBookShare } from "./memoir-book-api";
import { routeAuthError } from "./navigation";
import { pageUrl } from "./page-input";
import { shareLikerViews, type ShareLikerView } from "./share-likes";
import { formatBeijingDateTimeShort } from "./time";

const BOOK_SHARE_INACTIVE_DESCRIPTION = "生成后 7 天有效；你可以决定何时更新家人看到的书稿。";

function bookShareDescription(active: boolean, expiresAt: string | null): string {
  if (!active) return BOOK_SHARE_INACTIVE_DESCRIPTION;
  const expiresText = expiresAt ? formatBeijingDateTimeShort(expiresAt) : "";
  return `${expiresText ? `链接有效至 ${expiresText}` : "链接有效"}；家人可阅读你确认分享的书稿。`;
}

export interface ReadingShareData {
  bookSharePanelOpen: boolean;
  bookShareActive: boolean;
  bookShareReady: boolean;
  bookShareNeedsUpdate: boolean;
  bookShareStale: boolean;
  bookShareBusy: boolean;
  bookShareStatusKnown: boolean;
  bookShareDescription: string;
  // Who among the family liked this book or chapter, shown to the author only.
  bookShareLikeCount: number;
  bookShareLikers: ShareLikerView[];
}

export const INITIAL_READING_SHARE_DATA: ReadingShareData = {
  bookSharePanelOpen: false,
  bookShareActive: false,
  bookShareReady: false,
  bookShareNeedsUpdate: false,
  bookShareStale: false,
  bookShareBusy: false,
  bookShareStatusKnown: false,
  bookShareDescription: BOOK_SHARE_INACTIVE_DESCRIPTION,
  bookShareLikeCount: 0,
  bookShareLikers: [],
};

interface ReadingShareTarget {
  projectId: string;
  chapterId?: string;
  title: string;
  available: boolean;
  stale: boolean;
}

interface ReadingShareHost {
  data: ReadingShareData & { projectId: string; error: string };
  bookShareToken: string;
  bookShareStatusSequence: number;
  readingShareTarget(): ReadingShareTarget;
  setData(patch: Partial<ReadingShareData & { error: string }>): void;
}

function defineReadingShareMethods<T extends object>(methods: T & ThisType<ReadingShareHost & T>): T {
  return methods;
}

export const readingShareMethods = defineReadingShareMethods({
  isReadingShareTarget(projectId: string, chapterId?: string) {
    const target = this.readingShareTarget();
    return projectId === target.projectId && chapterId === target.chapterId;
  },

  onShareAppMessage() {
    const canShare = this.data.bookShareReady && !this.readingShareTarget().stale && Boolean(this.bookShareToken);
    return {
      title: canShare ? `阅读《${this.readingShareTarget().title || "家庭回忆录"}》` : "老余口述",
      path: canShare
        ? pageUrl("/pages/book-reader/index", { shareToken: this.bookShareToken })
        : "/pages/home/index",
    };
  },

  async openBookSharePanel() {
    if (!this.data.projectId || !this.readingShareTarget().available) return;
    if (this.data.bookSharePanelOpen) {
      this.setData({ bookSharePanelOpen: false });
      return;
    }
    this.setData({ bookSharePanelOpen: true });
    await this.refreshBookShareStatus();
  },

  async refreshBookShareStatus() {
    const { projectId, chapterId } = this.readingShareTarget();
    if (!projectId) return;
    const sequence = ++this.bookShareStatusSequence;
    this.setData({ bookShareBusy: true, bookShareStatusKnown: false, bookShareReady: false });
    try {
      const status = await getMemoirBookShareStatus(projectId, chapterId);
      if (!this.isReadingShareTarget(projectId, chapterId) || sequence !== this.bookShareStatusSequence) return;
      this.bookShareToken = status.active ? status.token || "" : "";
      const stale = status.stale || Boolean(this.readingShareTarget().stale);
      const needsUpdate = status.active && !stale && !status.current;
      this.setData({
        bookShareLikeCount: status.likes?.count ?? 0,
        bookShareLikers: shareLikerViews(status.likes?.likers ?? []),
        bookShareActive: status.active,
        bookShareReady: status.active && status.current && !stale && Boolean(this.bookShareToken),
        bookShareNeedsUpdate: needsUpdate,
        bookShareStale: stale,
        bookShareStatusKnown: true,
        bookShareDescription: stale
          ? status.active
            ? "书稿正在更新，阅读链接已暂停。更新完成后，你可以决定是否将新版发给家人。"
            : "书稿正在更新，完成后即可生成阅读链接。"
          : needsUpdate
            ? "新版书稿已完成。确认更新后，之前收到链接的家人也能读到新版。"
            : bookShareDescription(status.active, status.expires_at),
      });
    } catch (error) {
      if (this.isReadingShareTarget(projectId, chapterId) && sequence === this.bookShareStatusSequence && !routeAuthError(error, "login")) {
        this.setData({ error: error instanceof Error ? error.message : "分享状态读取失败" });
      }
    } finally {
      if (this.isReadingShareTarget(projectId, chapterId) && sequence === this.bookShareStatusSequence) this.setData({ bookShareBusy: false });
    }
  },

  async createBookShare() {
    const { projectId, chapterId } = this.readingShareTarget();
    if (!projectId || !this.data.bookShareStatusKnown || this.data.bookShareBusy || this.data.bookShareStale || this.readingShareTarget().stale) return;
    this.setData({ bookShareBusy: true, error: "" });
    try {
      const share = await createMemoirBookShare(projectId, chapterId);
      if (!this.isReadingShareTarget(projectId, chapterId)) return;
      this.bookShareToken = share.token;
      this.setData({
        bookShareActive: true,
        bookShareReady: true,
        bookShareNeedsUpdate: false,
        bookShareStale: false,
        bookShareStatusKnown: true,
        bookShareDescription: bookShareDescription(true, share.expires_at),
      });
    } catch (error) {
      if (this.isReadingShareTarget(projectId, chapterId) && !routeAuthError(error, "login")) {
        this.setData({ error: error instanceof Error ? error.message : "生成分享链接失败" });
      }
    } finally {
      if (this.isReadingShareTarget(projectId, chapterId)) this.setData({ bookShareBusy: false });
    }
  },

  async updateBookShare() {
    const { projectId, chapterId } = this.readingShareTarget();
    if (!projectId || !this.data.bookShareStatusKnown || !this.data.bookShareActive || !this.data.bookShareNeedsUpdate || this.data.bookShareBusy || this.readingShareTarget().stale) return;
    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "更新家人阅读的书稿",
        content: "确认后，之前收到阅读链接的家人也能看到新版书稿。链接和有效期不会改变。",
        confirmText: "更新书稿",
        success: (result) => resolve(Boolean(result.confirm)),
        fail: () => resolve(false),
      });
    });
    if (!confirmed || !this.isReadingShareTarget(projectId, chapterId)) return;
    this.setData({ bookShareBusy: true, error: "" });
    try {
      const share = await updateMemoirBookShare(projectId, chapterId);
      if (!this.isReadingShareTarget(projectId, chapterId)) return;
      this.bookShareToken = share.token;
      this.setData({
        bookShareReady: true,
        bookShareNeedsUpdate: false,
        bookShareDescription: bookShareDescription(true, share.expires_at),
      });
    } catch (error) {
      if (this.isReadingShareTarget(projectId, chapterId) && !routeAuthError(error, "login")) {
        this.setData({ error: error instanceof Error ? error.message : "更新分享书稿失败" });
      }
    } finally {
      if (this.isReadingShareTarget(projectId, chapterId)) this.setData({ bookShareBusy: false });
    }
  },

  async regenerateBookShare() {
    if (!this.data.bookShareStatusKnown || !this.data.bookShareActive || this.data.bookShareBusy || this.data.bookShareStale || this.readingShareTarget().stale) return;
    const { projectId, chapterId } = this.readingShareTarget();
    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "重新生成阅读链接",
        content: "重新生成后，之前发出的链接会立即失效。请将新链接重新发送给家人。",
        confirmText: "重新生成",
        success: (result) => resolve(Boolean(result.confirm)),
        fail: () => resolve(false),
      });
    });
    if (confirmed && this.isReadingShareTarget(projectId, chapterId)) await this.createBookShare();
  },

  async revokeBookShare() {
    if (!this.data.projectId || !this.data.bookShareStatusKnown || !this.data.bookShareActive || this.data.bookShareBusy) return;
    const { projectId, chapterId } = this.readingShareTarget();
    const confirmed = await new Promise<boolean>((resolve) => {
      wx.showModal({
        title: "撤销阅读链接",
        content: "撤销后，家人将无法通过此前发送的链接继续阅读。",
        confirmText: "撤销链接",
        success: (result) => resolve(Boolean(result.confirm)),
        fail: () => resolve(false),
      });
    });
    if (!confirmed || !this.isReadingShareTarget(projectId, chapterId)) return;
    this.setData({ bookShareBusy: true, error: "" });
    try {
      await revokeMemoirBookShare(projectId, chapterId);
      if (!this.isReadingShareTarget(projectId, chapterId)) return;
      this.bookShareToken = "";
      this.setData({
        bookShareActive: false,
        bookShareReady: false,
        bookShareNeedsUpdate: false,
        bookShareStatusKnown: true,
        bookShareDescription: this.data.bookShareStale
          ? "书稿正在更新，完成后即可生成阅读链接。"
          : BOOK_SHARE_INACTIVE_DESCRIPTION,
      });
    } catch (error) {
      if (this.isReadingShareTarget(projectId, chapterId) && !routeAuthError(error, "login")) {
        this.setData({ error: error instanceof Error ? error.message : "撤销分享链接失败" });
      }
    } finally {
      if (this.isReadingShareTarget(projectId, chapterId)) this.setData({ bookShareBusy: false });
    }
  },

});
