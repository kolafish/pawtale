import { createMemoirExcerptShare, readMemoirExcerptShare, suggestMemoirExcerptTitle, createMemoirExcerptImageJob, getMemoirIllustrationStyles } from "./memoir-book-api";
import { excerptShareMessage, enableMiniProgramShareMenu, hideMiniProgramShareMenu } from "./share";
import type { MemoirShareLikeTarget } from "../domain/memoir-share-likes";
import { ApiError, cleanBaseUrl, readBackendConfig } from "./request";
import type { MemoirExcerptShare } from "../domain/memoir-book";
import { TranscriptMaterialImageController, initialTranscriptMaterialImageTaskState } from "./transcript-material-image";
import { getMemoirMaterialImageGenerationJob } from "./memoir-transcript-api";
import { illustrationStyleViews, readShareVideoBackgroundSettings } from "./share-video-background-settings";

export const INITIAL_EXCERPT_DATA = {
  excerptMode: "" as "" | "select" | "compose" | "preview" | "shared",
  ...initialTranscriptMaterialImageTaskState(),
  excerptTitle: "", excerptTitleBusy: false, excerptTitleError: "",
  excerptStyle: "text" as "text" | "image", excerptImageSource: "ai" as "ai" | "photo",
  excerptBackgroundNote: "", excerptIllustrationStyle: "watercolor", excerptStyleOptions: [] as ReturnType<typeof illustrationStyleViews>,
  excerptPhotoIds: [] as string[], excerptAiAssetIds: [] as string[], excerptPhotoBusy: false,
  excerptStoryImageUrl: "", excerptImageDisclosure: "", excerptReadingLink: null as MemoirExcerptShare["reading_link"],
  excerptContent: "", excerptBookTitle: "", excerptChapterTitle: "",
  excerptImageUrl: "", excerptImageReady: false, excerptBusy: false, excerptSelectionCount: 0,
  excerptReloadRequired: false, excerptBeforeHighlight: "", excerptHighlight: "",
};

interface ExcerptHost {
  data: typeof INITIAL_EXCERPT_DATA & { projectId: string; chapterId: string; loading: boolean; error: string };
  excerptImageController: TranscriptMaterialImageController | null;
  excerptSource: string;
  excerptVersionId: string;
  excerptToken: string;
  excerptGeneration: number;
  sharedReadGeneration: number;
  excerptEditor: WechatMiniprogram.EditorContext | null;
  createSelectorQuery(): WechatMiniprogram.SelectorQuery;
  setData(patch: Partial<ExcerptHost["data"]>, callback?: () => void): void;
  loadChapterReader(projectId: string, chapterId: string): Promise<void>;
  loadShareLikes(target: MemoirShareLikeTarget): Promise<void>;
}
function methods<T extends object>(value: T & ThisType<ExcerptHost & T>): T { return value; }

export const readingExcerptMethods = methods({
  invalidateExcerptRequests() {
    this.excerptGeneration++;
    this.sharedReadGeneration++;
    this.excerptImageController?.cancel();
    this.setData({ excerptBusy: false, excerptTitleBusy: false, loading: false });
  },
  onExcerptEditorReady() {
    const generation = this.excerptGeneration;
    this.createSelectorQuery().select("#excerpt-editor").context((result) => {
      if (!result?.context || generation !== this.excerptGeneration || this.data.excerptMode !== "select") return;
      const { context } = result;
      this.excerptEditor = context;
      this.setData({ error: "" });
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
    // An early tap must not invalidate the editor's pending context callback.
    if (!this.excerptEditor) {
      this.setData({ error: "正文尚未就绪，请稍后再试。" });
      return;
    }
    const generation = ++this.excerptGeneration;
    this.setData({ excerptBusy: true, error: "" });
    try {
      const content = await this.readExcerptSelection();
      if (generation !== this.excerptGeneration) return;
      const count = Array.from(content).length;
      if (!count) throw Error("请先长按正文，拖动选区选择要分享的文字。");
      if (count > 512) throw Error(`已选 ${count} 字，请缩短到 512 字以内。`);
      if (!this.excerptSource.includes(content)) throw Error("请选择正文中连续的一段原文。");
      this.setData({ excerptMode: "compose", excerptContent: content, excerptSelectionCount: count, excerptTitle: "" },
        () => wx.pageScrollTo?.({ scrollTop: 0, duration: 0 }));
      await this.suggestExcerptTitle();
    } catch (error) {
      if (generation === this.excerptGeneration) this.setData({ error: error instanceof Error ? error.message : "分享卡片准备失败",
        excerptReloadRequired: error instanceof ApiError && error.statusCode === 409 });
    } finally {
      if (generation === this.excerptGeneration) this.setData({ excerptBusy: false });
    }
  },
  async suggestExcerptTitle() {
    if (this.data.excerptTitleBusy || this.data.excerptMode !== "compose") return;
    const generation = this.excerptGeneration;
    this.setData({ excerptTitleBusy: true, excerptTitleError: "" });
    try {
      const result = await suggestMemoirExcerptTitle(this.data.projectId, this.data.chapterId,
        { version_id: this.excerptVersionId, content: this.data.excerptContent });
      if (generation === this.excerptGeneration) this.setData({ excerptTitle: result.title });
    } catch (error) {
      if (generation === this.excerptGeneration) this.setData({ excerptTitleError: error instanceof ApiError && error.statusCode === 409
          ? "正文已更新，请重新选文。" : "标题推荐失败，可以自行填写标题或重试。",
        excerptReloadRequired: error instanceof ApiError && error.statusCode === 409 });
    } finally {
      if (generation === this.excerptGeneration) this.setData({ excerptTitleBusy: false });
    }
  },
  changeExcerptTitle(event: { detail: { value: string } }) {
    this.setData({ excerptTitle: Array.from(event.detail.value).slice(0, 40).join("") });
  },
  async chooseExcerptStyle(event: { currentTarget: { dataset: { style: "text" | "image" } } }) {
    this.setData({ excerptStyle: event.currentTarget.dataset.style, error: "" });
    if (this.data.excerptStyle === "image" && !this.data.excerptStyleOptions.length) {
      const generation = this.excerptGeneration;
      try {
        const options = illustrationStyleViews(await getMemoirIllustrationStyles());
        if (generation !== this.excerptGeneration) return;
        const preferred = readShareVideoBackgroundSettings().backgroundStyle;
        this.setData({ excerptStyleOptions: options,
          excerptIllustrationStyle: options.some((item) => item.value === preferred) ? preferred : "watercolor" });
      } catch (error) {
        if (generation === this.excerptGeneration) this.setData({ error: "插图风格加载失败，请重新选择带图片。" });
      }
    }
  },
  chooseExcerptImageSource(event: { currentTarget: { dataset: { source: "ai" | "photo" } } }) {
    this.setData({ excerptImageSource: event.currentTarget.dataset.source, error: "" });
  },
  changeExcerptIllustrationStyle(event: { detail: { value: string } }) {
    if (this.data.materialImageCreating || this.data.materialImagePolling) return;
    if (event.detail.value === this.data.excerptIllustrationStyle) return;
    this.excerptImageController?.reset();
    this.setData({ excerptIllustrationStyle: event.detail.value, excerptAiAssetIds: [] });
  },
  changeExcerptBackgroundNote(event: { detail: { value: string } }) {
    if (this.data.materialImageCreating || this.data.materialImagePolling || this.data.excerptBusy) return;
    const note = event.detail.value;
    if (note.trim() !== this.data.excerptBackgroundNote.trim()) {
      this.excerptImageController?.reset();
      this.setData({ excerptAiAssetIds: [] });
    }
    this.setData({ excerptBackgroundNote: note });
  },
  changeExcerptPhotos(event: { detail: { photoIds: string[] } }) {
    this.setData({ excerptPhotoIds: event.detail.photoIds.slice(0, 1) });
  },
  onExcerptPhotoError(event: { detail: { message: string } }) {
    this.setData({ error: event.detail.message });
  },
  onExcerptPhotoBusy(event: { detail: { busy: boolean } }) {
    this.setData({ excerptPhotoBusy: event.detail.busy });
  },
  ensureExcerptImageController() {
    if (this.excerptImageController) return this.excerptImageController;
    const controller = new TranscriptMaterialImageController({
      createJob: (_unused, _start, _end, styleId) => createMemoirExcerptImageJob(this.data.projectId, this.data.chapterId,
        { version_id: this.excerptVersionId, content: this.data.excerptContent, style_id: styleId,
          background_note: this.data.excerptBackgroundNote.trim() }),
      getJob: getMemoirMaterialImageGenerationJob,
    });
    controller.setStateListener((patch) => {
      this.setData(patch);
      if (patch.materialImageJob?.asset_status === "ready") this.setData({ excerptAiAssetIds: [patch.materialImageJob.asset_id] });
    });
    this.excerptImageController = controller;
    return controller;
  },
  async generateExcerptImage() {
    if (this.data.materialImageCreating || this.data.materialImagePolling) return;
    this.setData({ excerptAiAssetIds: [], error: "" });
    await this.ensureExcerptImageController().create({ utteranceId: "", startChar: 0,
      endChar: this.data.excerptSelectionCount, styleId: this.data.excerptIllustrationStyle });
  },
  resumeExcerptImage() {
    const job = this.data.materialImageJob;
    if (job && (job.status === "pending" || job.status === "running") && !this.data.materialImagePolling) {
      void this.ensureExcerptImageController().refresh(job.job_id);
    }
  },
  async confirmExcerptCard() {
    if (this.data.excerptBusy || this.data.excerptTitleBusy || this.data.excerptPhotoBusy || this.data.excerptReloadRequired) return;
    const title = this.data.excerptTitle.trim();
    if (!title) { this.setData({ error: "请确认或填写分享标题。" }); return; }
    const assetId = this.data.excerptStyle === "image"
      ? (this.data.excerptImageSource === "ai" ? this.data.excerptAiAssetIds[0] : this.data.excerptPhotoIds[0]) : "";
    if (this.data.excerptStyle === "image" && !assetId) { this.setData({ error: "请先生成插图或选择一张照片。" }); return; }
    const generation = ++this.excerptGeneration;
    this.setData({ excerptBusy: true, error: "" });
    try {
      const share = await createMemoirExcerptShare(this.data.projectId, this.data.chapterId,
        { version_id: this.excerptVersionId, content: this.data.excerptContent, title, ...(assetId ? { image_asset_id: assetId } : {}) });
      if (generation !== this.excerptGeneration) return;
      this.excerptToken = share.token;
      this.setData({ excerptMode: "preview", excerptTitle: share.title, excerptContent: share.content,
        excerptBookTitle: share.book_title, excerptChapterTitle: share.chapter_title,
        excerptImageUrl: excerptShareMessage(share.token, share.title).imageUrl || "", excerptImageReady: false },
        () => wx.pageScrollTo?.({ scrollTop: 0, duration: 0 }));
    } catch (error) {
      if (generation === this.excerptGeneration) this.setData({ error: error instanceof Error ? error.message : "分享卡片准备失败",
        excerptReloadRequired: error instanceof ApiError && error.statusCode === 409 });
    } finally {
      if (generation === this.excerptGeneration) this.setData({ excerptBusy: false });
    }
  },
  editExcerptCard() {
    this.excerptToken = "";
    this.setData({ excerptMode: "compose", excerptImageReady: false, error: "" },
      () => wx.pageScrollTo?.({ scrollTop: 0, duration: 0 }));
    this.resumeExcerptImage();
  },
  openExcerptReading() {
    const link = this.data.excerptReadingLink;
    if (link) wx.navigateTo({ url: `/pages/book-reader/index?shareToken=${encodeURIComponent(link.token)}` });
  },
  onExcerptImageLoad(event: { currentTarget: { dataset: { url?: string } } }) {
    if (this.data.excerptMode === "preview" && event.currentTarget.dataset.url === this.data.excerptImageUrl) this.setData({ excerptImageReady: true, error: "" });
  },
  onExcerptImageError(event: { currentTarget: { dataset: { url?: string } } }) {
    if (this.data.excerptMode === "preview" && event.currentTarget.dataset.url === this.data.excerptImageUrl) this.setData({ excerptImageReady: false, error: "卡片加载失败，请重新选择后再试。" });
  },
  reselectExcerpt() {
    this.excerptImageController?.dispose();
    this.excerptImageController = null;
    this.excerptGeneration++;
    this.excerptToken = "";
    this.excerptEditor = null;
    hideMiniProgramShareMenu();
    this.setData({ ...INITIAL_EXCERPT_DATA, excerptMode: "select", error: "" });
    void this.loadChapterReader(this.data.projectId, this.data.chapterId);
  },
  async loadExcerptSnapshot() {
    const generation = ++this.excerptGeneration;
    this.setData({ loading: true, error: "" });
    hideMiniProgramShareMenu();
    try {
      const excerpt = await readMemoirExcerptShare(this.excerptToken);
      if (generation !== this.excerptGeneration) return;
      const chars = Array.from(excerpt.content);
      const lastClause = excerpt.content.match(/[^。！？!?，,；;\n]+[。！？!?]?$/)?.[0] || excerpt.content;
      const highlightLength = Math.min(Array.from(lastClause).length, 24);
      this.setData({ excerptBeforeHighlight: chars.slice(0, -highlightLength).join(""), excerptHighlight: chars.slice(-highlightLength).join(""),
        excerptTitle: excerpt.title,
        excerptStoryImageUrl: excerpt.image_url ? `${cleanBaseUrl(readBackendConfig().httpBaseUrl)}${excerpt.image_url}` : "",
        excerptImageDisclosure: excerpt.image_disclosure || "", excerptReadingLink: excerpt.reading_link || null,
        excerptContent: excerpt.content, excerptBookTitle: excerpt.book_title,
        excerptChapterTitle: excerpt.chapter_title, loading: false, excerptImageReady: true });
      enableMiniProgramShareMenu();
      await this.loadShareLikes({ reading_token: this.excerptToken });
    } catch (error) {
      if (generation === this.excerptGeneration) this.setData({ loading: false, excerptContent: "", excerptImageReady: false,
        error: error instanceof Error ? error.message : "选文分享暂时打不开" });
    }
  },
  retryExcerpt() {
    if (this.data.excerptMode === "shared") void this.loadExcerptSnapshot();
    else if (this.data.excerptMode === "compose" && !this.data.excerptReloadRequired) void this.confirmExcerptCard();
    else this.reselectExcerpt();
  },
});
