import { getMemoirBookShareStatus, readMemoirBookShare, revokeMemoirBookShare, updateMemoirBookShare } from "./memoir-book-api";
import { routeAuthError } from "./navigation";
import { pageUrl } from "./page-input";
import { shareLikerViews, type ShareLikerView } from "./share-likes";
import { enableMiniProgramShareMenu, hideMiniProgramShareMenu, readingShareMessage } from "./share";
import { cleanBaseUrl, readBackendConfig } from "./request";

const DESCRIPTION = "链接长期有效，更新自动同步；可在更多操作中停止分享。";
export interface ReadingShareData {
  bookShareActive: boolean;
  bookShareReady: boolean;
  bookShareStale: boolean;
  bookShareBusy: boolean;
  bookShareStatusKnown: boolean;
  bookShareDescription: string;
  bookShareImageUrl: string;
  bookShareLikeCount: number;
  bookShareLikers: ShareLikerView[];
}
export const INITIAL_READING_SHARE_DATA: ReadingShareData = {
  bookShareActive: false, bookShareReady: false, bookShareStale: false, bookShareBusy: false,
  bookShareStatusKnown: false, bookShareDescription: DESCRIPTION, bookShareImageUrl: "",
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
  isReadingShareTarget(projectId: string, chapterId?: string, sectionId?: string) {
    const target = this.readingShareTarget();
    return projectId === target.projectId && chapterId === target.chapterId && sectionId === target.sectionId;
  },
  onShareAppMessage() {
    return readingShareMessage(this.readingShareTarget().title, this.data.bookShareReady ? this.bookShareToken : "", this.data.bookShareImageUrl);
  },
  async loadReadingShareCard(token: string): Promise<string> {
    const output = await readMemoirBookShare(token);
    const manuscript = output.latest_version?.manuscript;
    const image = manuscript?.render_settings.cover_image || manuscript?.chapters.flatMap(chapter => chapter.illustrations)[0];
    return image?.image_url ? `${cleanBaseUrl(readBackendConfig().httpBaseUrl)}${image.image_url}` : "";
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
        bookShareDescription: status.stale ? "已移除部分内容，分享暂停；书稿更新后自动恢复。" : DESCRIPTION,
      });
      if (status.current && !status.stale && this.bookShareToken) {
        const image = await this.loadReadingShareCard(this.bookShareToken);
        if (this.isReadingShareTarget(projectId, chapterId, sectionId) && sequence === this.bookShareStatusSequence) {
          this.setData({ bookShareImageUrl: image, bookShareReady: true });
          enableMiniProgramShareMenu();
        }
      }
    } catch (error) {
      if (this.isReadingShareTarget(projectId, chapterId, sectionId) && sequence === this.bookShareStatusSequence) {
        this.setData({ bookShareReady: false, bookShareDescription: "暂时无法确认分享内容，请重试。" });
      }
    }
  },
  async prepareReadingShare() {
    const { projectId, chapterId, sectionId, available } = this.readingShareTarget();
    if (!projectId || !available || this.data.bookShareBusy || this.data.bookShareReady) return;
    const sequence = ++this.bookShareStatusSequence;
    this.setData({ bookShareBusy: true, error: "", bookShareDescription: "正在准备分享内容…" });
    hideMiniProgramShareMenu();
    try {
      const share = await updateMemoirBookShare(projectId, chapterId, sectionId);
      if (!this.isReadingShareTarget(projectId, chapterId, sectionId) || sequence !== this.bookShareStatusSequence) return;
      this.bookShareToken = share.token;
      this.setData({ bookShareActive: true, bookShareStatusKnown: true });
      const image = await this.loadReadingShareCard(share.token);
      if (!this.isReadingShareTarget(projectId, chapterId, sectionId) || sequence !== this.bookShareStatusSequence) return;
      this.bookShareToken = share.token;
      this.setData({ bookShareActive: true, bookShareReady: true, bookShareStale: false, bookShareStatusKnown: true,
        bookShareImageUrl: image, bookShareDescription: "已准备好，再点「分享给好友」发送。取消发送仍可在更多中停止分享。" });
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
  openReadingShareMore() {
    if (this.data.bookShareBusy) return;
    wx.showActionSheet({ itemList: ["预览分享内容", ...(this.data.bookShareActive ? ["停止分享"] : [])],
      success: ({ tapIndex }) => { if (tapIndex === 0) this.openReadingSharePreview(); else void this.revokeBookShare(); },
    });
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
    this.setData({ bookShareBusy: true });
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
