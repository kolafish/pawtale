import type { MemoirReadingShareCardSelection } from "../domain/memoir-book";
import { getMemoirBookShareStatus, readMemoirBookShare, revokeMemoirBookShare, updateMemoirBookShare, saveMemoirShareCardSelection } from "./memoir-book-api";
import { routeAuthError } from "./navigation";
import { pageUrl } from "./page-input";
import { shareLikerViews, type ShareLikerView } from "./share-likes";
import { enableMiniProgramShareMenu, hideMiniProgramShareMenu, readingShareMessage, readingShareImageUrl, readingShareExcerpt } from "./share";

const DESCRIPTION = "分享链接长期有效，书稿更新自动同步";
export interface ReadingShareData {
  bookShareActive: boolean;
  bookShareReady: boolean;
  bookShareStale: boolean;
  bookShareBusy: boolean;
  bookShareStatusKnown: boolean;
  bookShareDescription: string;
  bookShareFeedbackVisible: boolean;
  bookShareImageUrl: string;
  bookShareLikeCount: number;
  bookShareLikers: ShareLikerView[];
}
export const INITIAL_READING_SHARE_DATA: ReadingShareData = {
  bookShareActive: false, bookShareReady: false, bookShareStale: false, bookShareBusy: false,
  bookShareStatusKnown: false, bookShareDescription: DESCRIPTION, bookShareFeedbackVisible: false, bookShareImageUrl: "",
  bookShareLikeCount: 0, bookShareLikers: [],
};
interface ReadingShareTarget {
  projectId: string;
  chapterId?: string;
  sectionId?: string;
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
function defineReadingShareMethods<T extends object>(methods: T & ThisType<ReadingShareHost & T>): T { return methods; }
export const readingShareMethods = defineReadingShareMethods({
  invalidateReadingShareRequests() {
    this.bookShareStatusSequence += 1;
    this.setData({ bookShareBusy: false });
  },
  isReadingShareTarget(projectId: string, chapterId?: string, sectionId?: string) {
    const target = this.readingShareTarget();
    return projectId === target.projectId && chapterId === target.chapterId && sectionId === target.sectionId;
  },
  onShareAppMessage() {
    return readingShareMessage(this.readingShareTarget().title, this.data.bookShareReady ? this.bookShareToken : "", this.data.bookShareImageUrl);
  },
  async loadReadingShareCard(token: string, confirmExcerpt = false, saved?: MemoirReadingShareCardSelection | null) {
    const { projectId, chapterId, sectionId } = this.readingShareTarget();
    const sequence = this.bookShareStatusSequence;
    const output = await readMemoirBookShare(token);
    const version = output.latest_version;
    const plain = { imageUrl: readingShareImageUrl(token, undefined, version?.id), needsConfirmation: false };
    if (!version || !(chapterId || sectionId) || !this.isReadingShareTarget(projectId, chapterId, sectionId) || sequence !== this.bookShareStatusSequence) return plain;
    if (!confirmExcerpt) {
      if (!saved || saved.version_id !== version.id) return { ...plain, needsConfirmation: true };
      return { ...plain, imageUrl: readingShareImageUrl(token, saved.excerpt_length ? {
        versionId: saved.version_id, start: saved.excerpt_start, length: saved.excerpt_length,
      } : undefined, version.id) };
    }
    const excerpt = readingShareExcerpt(version.content, version.id);
    const confirmed = excerpt && await new Promise<boolean>((resolve, reject) => wx.showModal({
      title: "在分享封面展示这段原文？", content: `“${excerpt.text}”\n\n封面在微信聊天中直接可见，请确认没有不想公开的内容。`,
      confirmText: "使用摘录", cancelText: "不放摘录",
      success: result => resolve(Boolean(result.confirm)), fail: () => reject(new Error("无法确认封面摘录，请重试")),
    }));
    if (!this.isReadingShareTarget(projectId, chapterId, sectionId) || sequence !== this.bookShareStatusSequence) return plain;
    await saveMemoirShareCardSelection(projectId, {
      token, version_id: version.id, excerpt_start: confirmed ? excerpt.start : 0, excerpt_length: confirmed ? excerpt.length : 0,
    }, chapterId, sectionId);
    return { ...plain, imageUrl: readingShareImageUrl(token, confirmed ? excerpt : undefined, version.id) };
  },
  async refreshBookShareStatus() {
    const { projectId, chapterId, sectionId } = this.readingShareTarget();
    if (!projectId || this.data.bookShareBusy) return;
    const sequence = ++this.bookShareStatusSequence;
    hideMiniProgramShareMenu();
    this.setData({ bookShareReady: false });
    try {
      const status = await getMemoirBookShareStatus(projectId, chapterId, sectionId);
      if (!this.isReadingShareTarget(projectId, chapterId, sectionId) || sequence !== this.bookShareStatusSequence) return;
      this.bookShareToken = status.active ? status.token || "" : "";
      this.setData({
        bookShareLikeCount: status.likes?.count ?? 0, bookShareLikers: shareLikerViews(status.likes?.likers ?? []),
        bookShareActive: status.active, bookShareReady: false,
        bookShareStale: status.stale, bookShareStatusKnown: true,
        bookShareFeedbackVisible: status.stale,
        bookShareDescription: status.stale ? "已移除部分内容，分享暂停；书稿更新后自动恢复。" : DESCRIPTION,
      });
      if (status.current && !status.stale && this.bookShareToken) {
        const card = await this.loadReadingShareCard(this.bookShareToken, false, status.card_selection);
        if (this.isReadingShareTarget(projectId, chapterId, sectionId) && sequence === this.bookShareStatusSequence) {
          this.setData({ bookShareImageUrl: card.imageUrl, bookShareReady: !card.needsConfirmation,
            ...(card.needsConfirmation ? { bookShareFeedbackVisible: true, bookShareDescription: "请点击分享确认封面摘录。" } : {}) });
          if (!card.needsConfirmation) enableMiniProgramShareMenu();
        }
      }
    } catch (error) {
      if (this.isReadingShareTarget(projectId, chapterId, sectionId) && sequence === this.bookShareStatusSequence) {
        this.setData({ bookShareReady: false, bookShareFeedbackVisible: true, bookShareDescription: "暂时无法确认分享内容，请重试。" });
      }
    }
  },
  async prepareReadingShare() {
    const { projectId, chapterId, sectionId, available } = this.readingShareTarget();
    if (!projectId || !available || this.data.bookShareBusy) return;
    if (this.data.bookShareReady) {
      if (chapterId || sectionId) this.setData({ bookShareFeedbackVisible: true });
      return;
    }
    const sequence = ++this.bookShareStatusSequence;
    this.setData({ bookShareBusy: true, bookShareFeedbackVisible: true, error: "", bookShareDescription: "正在准备分享内容…" });
    hideMiniProgramShareMenu();
    try {
      const share = await updateMemoirBookShare(projectId, chapterId, sectionId);
      if (!this.isReadingShareTarget(projectId, chapterId, sectionId) || sequence !== this.bookShareStatusSequence) return;
      this.bookShareToken = share.token;
      this.setData({ bookShareActive: true, bookShareStatusKnown: true });
      const card = await this.loadReadingShareCard(share.token, true);
      if (!this.isReadingShareTarget(projectId, chapterId, sectionId) || sequence !== this.bookShareStatusSequence) return;
      this.bookShareToken = share.token;
      this.setData({ bookShareActive: true, bookShareReady: true, bookShareStale: false, bookShareStatusKnown: true,
        bookShareImageUrl: card.imageUrl, bookShareDescription: `已准备好，再点「分享给好友」发送。${chapterId || sectionId ? DESCRIPTION : ""}` });
      enableMiniProgramShareMenu();
    } catch (error) {
      if (this.isReadingShareTarget(projectId, chapterId, sectionId) && sequence === this.bookShareStatusSequence && !routeAuthError(error, "login")) {
        this.setData({ bookShareReady: false, bookShareDescription: "准备失败，可点击同一按钮重试。", error: error instanceof Error ? error.message : "分享准备失败" });
      }
    } finally {
      if (this.isReadingShareTarget(projectId, chapterId, sectionId) && sequence === this.bookShareStatusSequence) this.setData({ bookShareBusy: false });
    }
  },
  openReadingSharePreview() {
    const { projectId, chapterId, sectionId } = this.readingShareTarget();
    wx.navigateTo({ url: pageUrl("/pages/book-reader/index", { projectId, chapterId, sectionId, sharePreview: "1" }) });
  },
  openExcerptShare() {
    const { projectId, chapterId, available } = this.readingShareTarget();
    if (!projectId || !chapterId || !available) return;
    wx.navigateTo({ url: pageUrl("/pages/book-reader/index", { projectId, chapterId, excerptSelect: "1" }) });
  },
  openReadingShareMore() {
    if (this.data.bookShareBusy) return;
    const { chapterId, sectionId } = this.readingShareTarget();
    const canSetExcerpt = this.data.bookShareReady && Boolean(chapterId || sectionId);
    const items = ["预览分享内容", ...(canSetExcerpt ? ["设置封面摘录"] : []), ...(this.data.bookShareActive ? ["停止分享"] : [])];
    wx.showActionSheet({ itemList: items,
      success: ({ tapIndex }) => {
        if (items[tapIndex] === "预览分享内容") this.openReadingSharePreview();
        else if (items[tapIndex] === "设置封面摘录") void this.configureReadingShareCard();
        else if (items[tapIndex] === "停止分享") void this.revokeBookShare();
      },
    });
  },
  async configureReadingShareCard() {
    if (!this.data.bookShareReady || this.data.bookShareBusy) return;
    const { projectId, chapterId, sectionId } = this.readingShareTarget();
    const sequence = ++this.bookShareStatusSequence;
    this.setData({ bookShareBusy: true, bookShareReady: false });
    hideMiniProgramShareMenu();
    try {
      const card = await this.loadReadingShareCard(this.bookShareToken, true);
      if (!this.isReadingShareTarget(projectId, chapterId, sectionId) || sequence !== this.bookShareStatusSequence) return;
      this.setData({ bookShareImageUrl: card.imageUrl, bookShareReady: true });
      enableMiniProgramShareMenu();
    } catch (error) {
      if (this.isReadingShareTarget(projectId, chapterId, sectionId) && sequence === this.bookShareStatusSequence && !routeAuthError(error, "login")) {
        this.setData({ error: error instanceof Error ? error.message : "封面准备失败" });
      }
    } finally {
      if (this.isReadingShareTarget(projectId, chapterId, sectionId) && sequence === this.bookShareStatusSequence) this.setData({ bookShareBusy: false });
    }
  },
  async revokeBookShare() {
    const { projectId, chapterId, sectionId } = this.readingShareTarget();
    if (!projectId || this.data.bookShareBusy || !this.data.bookShareActive) return;
    const confirmed = await new Promise<boolean>(resolve => wx.showModal({
      title: "停止分享", content: "之前发出的链接将无法继续阅读，您的书稿仍会保留。",
      confirmText: "停止分享", success: result => resolve(Boolean(result.confirm)), fail: () => resolve(false),
    }));
    if (!confirmed || !this.isReadingShareTarget(projectId, chapterId, sectionId)) return;
    const sequence = ++this.bookShareStatusSequence;
    this.setData({ bookShareBusy: true, bookShareFeedbackVisible: true });
    hideMiniProgramShareMenu();
    try {
      await revokeMemoirBookShare(projectId, chapterId, sectionId);
      if (!this.isReadingShareTarget(projectId, chapterId, sectionId) || sequence !== this.bookShareStatusSequence) return;
      this.bookShareToken = "";
      this.setData({ bookShareActive: false, bookShareReady: false, bookShareImageUrl: "", bookShareDescription: "已停止分享，再次分享会使用新链接。" });
    } catch (error) {
      if (this.isReadingShareTarget(projectId, chapterId, sectionId) && !routeAuthError(error, "login")) this.setData({ error: error instanceof Error ? error.message : "停止分享失败" });
    } finally {
      if (this.isReadingShareTarget(projectId, chapterId, sectionId) && sequence === this.bookShareStatusSequence) this.setData({ bookShareBusy: false });
    }
  },
});
