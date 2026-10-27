import { createMemoirExcerptShare, previewMemoirBookShare, readMemoirExcerptShare } from "./memoir-book-api";
import { excerptShareMessage, enableMiniProgramShareMenu, hideMiniProgramShareMenu } from "./share";
import type { MemoirShareLikeTarget } from "../domain/memoir-share-likes";
import { ApiError } from "./request";

export const INITIAL_EXCERPT_DATA = {
  excerptMode: "" as "" | "select" | "preview" | "shared",
  excerptContent: "", excerptBookTitle: "", excerptChapterTitle: "",
  excerptImageUrl: "", excerptImageReady: false, excerptBusy: false, excerptSelectionCount: 0,
  excerptReloadRequired: false,
};

interface ExcerptHost {
  data: typeof INITIAL_EXCERPT_DATA & { projectId: string; chapterId: string; loading: boolean; error: string };
  excerptSource: string;
  excerptVersionId: string;
  excerptToken: string;
  excerptGeneration: number;
  excerptEditor: WechatMiniprogram.EditorContext | null;
  createSelectorQuery(): WechatMiniprogram.SelectorQuery;
  setData(patch: Partial<ExcerptHost["data"]>): void;
  loadShareLikes(target: MemoirShareLikeTarget): Promise<void>;
}
function methods<T extends object>(value: T & ThisType<ExcerptHost & T>): T { return value; }

export const readingExcerptMethods = methods({
  invalidateExcerptRequests() {
    this.excerptGeneration++;
    this.setData({ excerptBusy: false, loading: false });
  },
  async loadExcerptSource() {
    const generation = ++this.excerptGeneration;
    hideMiniProgramShareMenu();
    this.setData({ loading: true, error: "" });
    try {
      const output = await previewMemoirBookShare(this.data.projectId, this.data.chapterId);
      if (generation !== this.excerptGeneration) return;
      if (!output.latest_version?.content.trim() || output.is_stale) throw Error("正文正在更新，请完成更新后再选择划线内容。");
      this.excerptSource = output.latest_version.content;
      this.excerptVersionId = output.latest_version.id;
      this.setData({ excerptChapterTitle: output.title, loading: false });
    } catch (error) {
      if (generation === this.excerptGeneration) this.setData({ loading: false, error: error instanceof Error ? error.message : "正文打开失败" });
    }
  },
  onExcerptEditorReady() {
    const generation = this.excerptGeneration;
    this.createSelectorQuery().select("#excerpt-editor").context((result) => {
      if (!result?.context || generation !== this.excerptGeneration || this.data.excerptMode !== "select") return;
      const { context } = result;
      this.excerptEditor = context;
      context.setContents({ delta: { ops: [{ insert: this.excerptSource + "\n" }] },
        fail: () => this.setData({ error: "正文加载失败，请返回重试。" }) });
    }).exec();
  },
  async readExcerptSelection(): Promise<string> {
    const editor = this.excerptEditor;
    if (!editor) throw Error("正文尚未就绪，请稍后再试。");
    return new Promise((resolve, reject) => editor.getSelectionText({
      success: ({ text }) => resolve(text.trim()), fail: () => reject(Error("未能读取选中文字，请重新选择。")),
    }));
  },
  async previewExcerptSelection() {
    if (this.data.excerptBusy || this.data.excerptReloadRequired || this.data.excerptMode !== "select") return;
    const generation = ++this.excerptGeneration;
    this.setData({ excerptBusy: true, error: "" });
    try {
      const content = await this.readExcerptSelection();
      if (generation !== this.excerptGeneration) return;
      const count = Array.from(content).length;
      if (!count) throw Error("请先长按正文，拖动选区选择要分享的文字。");
      if (count > 120) throw Error(`已选 ${count} 字，请缩短到 120 字以内，让卡片清晰易读。`);
      if (!this.excerptSource.includes(content)) throw Error("请选择正文中连续的一段原文。");
      const share = await createMemoirExcerptShare(this.data.projectId, this.data.chapterId, { version_id: this.excerptVersionId, content });
      if (generation !== this.excerptGeneration) return;
      this.excerptToken = share.token;
      this.setData({ excerptMode: "preview", excerptContent: share.content, excerptBookTitle: share.book_title,
        excerptChapterTitle: share.chapter_title, excerptImageUrl: excerptShareMessage(share.token, share.content).imageUrl || "",
        excerptSelectionCount: count, excerptImageReady: false });
    } catch (error) {
      if (generation === this.excerptGeneration) this.setData({ error: error instanceof Error ? error.message : "分享卡片准备失败",
        excerptReloadRequired: error instanceof ApiError && error.statusCode === 409 });
    } finally {
      if (generation === this.excerptGeneration) this.setData({ excerptBusy: false });
    }
  },
  onExcerptImageLoad(event: { currentTarget: { dataset: { url?: string } } }) {
    if (this.data.excerptMode === "preview" && event.currentTarget.dataset.url === this.data.excerptImageUrl) this.setData({ excerptImageReady: true, error: "" });
  },
  onExcerptImageError(event: { currentTarget: { dataset: { url?: string } } }) {
    if (this.data.excerptMode === "preview" && event.currentTarget.dataset.url === this.data.excerptImageUrl) this.setData({ excerptImageReady: false, error: "卡片加载失败，请重新选择后再试。" });
  },
  reselectExcerpt() {
    this.excerptGeneration++;
    this.excerptToken = "";
    this.excerptEditor = null;
    hideMiniProgramShareMenu();
    this.setData({ ...INITIAL_EXCERPT_DATA, excerptMode: "select", error: "" });
    void this.loadExcerptSource();
  },
  async loadExcerptSnapshot() {
    const generation = ++this.excerptGeneration;
    this.setData({ loading: true, error: "" });
    hideMiniProgramShareMenu();
    try {
      const excerpt = await readMemoirExcerptShare(this.excerptToken);
      if (generation !== this.excerptGeneration) return;
      this.setData({ excerptContent: excerpt.content, excerptBookTitle: excerpt.book_title,
        excerptChapterTitle: excerpt.chapter_title, loading: false, excerptImageReady: true });
      enableMiniProgramShareMenu();
      await this.loadShareLikes({ reading_token: this.excerptToken });
    } catch (error) {
      if (generation === this.excerptGeneration) this.setData({ loading: false, excerptContent: "", excerptImageReady: false,
        error: error instanceof Error ? error.message : "划线分享暂时打不开" });
    }
  },
  retryExcerpt() {
    if (this.data.excerptMode === "shared") void this.loadExcerptSnapshot();
    else this.reselectExcerpt();
  },
});
