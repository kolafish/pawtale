import type {
  MemoirBookOutput,
  MemoirBookOutputExportFormat,
  MemoirBookOutputExportLayout,
  MemoirBookPlan,
} from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import {
  downloadMemoirBookOutputExport,
  assembleMemoirBookOutput,
  enqueueMemoirBookOutputEditorialCopyGeneration,
  getMemoirBookOutput,
  getMemoirBookPlan,
  suggestMemoirBookOutputTitles,
  updateMemoirBookOutput,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";
import { routeAuthError } from "../../services/navigation";
import {
  datasetChoice,
  datasetString,
  pickerIndex,
  pageUrl,
  queryParam,
  type PageDatasetEvent,
  type PagePickerEvent,
  type PageQuery,
  type PageSwitchEvent,
  type PageTextInputEvent,
} from "../../services/page-input";
import {
  bookOutputData,
  buildChapterViews,
  planStage,
  stageText,
  trimText,
  type BookData,
} from "../book/book-view-model";
import {
  bookDesignHasChanges,
  bookDesignSaveStatusText,
  bookEditorialCopyBlockedMessage,
  bookMetadataChanged,
  currentBookMetadataPayload,
  isBookLevelEditorialCopyTarget,
  latestBookCoverStyle,
  latestBookTitle,
} from "../book/book-design-model";
interface BookExportDataset {
  action?: string;
  format?: string;
  key?: string;
  layout?: string;
  target?: string;
  title?: string;
}

type DatasetEvent = PageDatasetEvent<BookExportDataset>;
type TextInputEvent = PageTextInputEvent<BookExportDataset>;
type PickerEvent = PagePickerEvent<BookExportDataset>;
type SwitchEvent = PageSwitchEvent<BookExportDataset>;

interface OpenDocumentOptions {
  filePath: string;
  fileType?: "pdf";
  showMenu?: boolean;
  success?: () => void;
  fail?: (error: WechatMiniprogram.GeneralCallbackResult) => void;
}

type WxWithDocument = WechatMiniprogram.Wx & {
  openDocument?: (options: OpenDocumentOptions) => void;
};

type BookExportData = Pick<
  BookData,
  | "loading"
  | "error"
  | "projectId"
  | "plan"
  | "planTitle"
  | "stage"
  | "stageText"
  | "chapters"
  | "totalChapterCount"
  | "allChaptersConfirmed"
  | "bookOutput"
  | "bookContent"
  | "bookCharCount"
  | "bookPreviewTitle"
  | "bookPreviewTocItems"
  | "bookPreviewChapters"
  | "bookPreviewChaptersExpanded"
  | "bookManuscriptAvailable"
  | "bookManuscriptTitle"
  | "bookManuscriptSubtitle"
  | "bookManuscriptTemplateLabel"
  | "bookManuscriptSettingsText"
  | "bookManuscriptStructureItems"
  | "bookManuscriptChapterItems"
  | "bookManuscriptBackMatterItems"
  | "bookVersionText"
  | "bookVersionOptions"
  | "bookSelectedVersionId"
  | "bookSelectedVersionIndex"
  | "bookViewingHistoricalVersion"
  | "bookVersionNotice"
  | "bookStaleNotice"
  | "bookActionText"
  | "bookExportBusyTarget"
  | "bookExportShowSourceAppendix"
  | "bookTitleInput"
  | "bookSubtitleInput"
  | "bookNarratorNameInput"
  | "bookPreparedByInput"
  | "bookPrefaceInput"
  | "bookAfterwordInput"
  | "bookBackCoverQuoteInput"
  | "bookTitleChanged"
  | "bookMetadataChanged"
  | "bookTitleSaving"
  | "bookDesignSaveStatusText"
  | "bookTitleSuggesting"
  | "bookTitleSuggestions"
  | "bookCoverStyle"
  | "bookCoverStylePreviewClass"
  | "bookCoverStyleIndex"
  | "bookCoverStyleLabel"
  | "bookCoverStyleDetailText"
  | "bookCoverStyleOptions"
  | "bookCoverStyleChanged"
  | "bookEditorialCopyBusyTarget"
  | "generatingBook"
> & {
  requestedAction: string;
  exportNotice: string;
  exportActionHintText: string;
  showBookSettings: boolean;
};

const BOOK_DESIGN_AUTOSAVE_DELAY_MS = 900;
const BOOK_EXPORT_FORMATS: readonly MemoirBookOutputExportFormat[] = ["pdf", "txt"];
const BOOK_EXPORT_LAYOUTS: readonly MemoirBookOutputExportLayout[] = ["standard", "large_print"];

function exportNotice(action: string): string {
  if (action === "reading_pdf") {
    return "阅读版适合发给家人在线阅读。";
  }
  if (action === "print_pdf") {
    return "打印版字号更大，适合装订保存。";
  }
  return "可以先预览全书，再导出阅读版或打印版。";
}

function exportActionHint(action: string): string {
  if (action === "print_pdf") {
    return "适合拿去打印装订，页边距更宽。";
  }
  if (action === "text") {
    return "导出纯文字，方便复制和继续编辑。";
  }
  return "适合微信分享和手机阅读。";
}

function defaultTitle(plan: MemoirBookPlan | null, output: MemoirBookOutput | null): string {
  const outputData = bookOutputData(output);
  return (
    trimText(outputData.bookManuscriptTitle)
    || trimText(outputData.bookPreviewTitle)
    || trimText(plan?.title || "").replace("章节规划", "").replace("主题回忆录", "回忆录")
    || "家庭回忆录"
  );
}

definePage<BookExportData>()({
  bookDesignAutosaveTimer: null as number | null,

  data: {
    loading: false,
    error: "",
    projectId: "",
    plan: null,
    planTitle: "预览与导出",
    stage: "empty",
    stageText: "尚未整理",
    chapters: [],
    totalChapterCount: 0,
    allChaptersConfirmed: false,
    bookPreviewChaptersExpanded: true,
    bookExportBusyTarget: "",
    bookExportShowSourceAppendix: true,
    bookTitleSaving: false,
    bookTitleSuggesting: false,
    bookEditorialCopyBusyTarget: "",
    generatingBook: false,
    requestedAction: "preview",
    exportNotice: exportNotice("preview"),
    exportActionHintText: exportActionHint("reading_pdf"),
    showBookSettings: false,
    ...bookOutputData(null),
  },

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    const action = queryParam(query, "action") || "preview";
    this.setData({
      projectId,
      requestedAction: action,
      exportNotice: exportNotice(action),
      exportActionHintText: exportActionHint(action),
    });
    if (!projectId) {
      this.setData({ error: "缺少回忆录项目信息" });
      return;
    }
    await this.loadBookExportPage(projectId);
  },

  onUnload() {
    this.flushBookDesignAutosave();
  },

  async loadBookExportPage(projectId: string) {
    this.setData({ loading: true, error: "" });
    try {
      const [plan, output] = await Promise.all([
        getMemoirBookPlan(projectId).catch((error) => {
          if (error instanceof ApiError && error.statusCode === 404) {
            return null;
          }
          throw error;
        }),
        this.loadBookOutput(projectId),
      ]);
      this.applyBookExportData(plan, output);
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "预览与导出页加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadBookOutput(projectId: string) {
    try {
      return await getMemoirBookOutput(projectId);
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        return null;
      }
      throw error;
    }
  },

  applyBookExportData(plan: MemoirBookPlan | null, output: MemoirBookOutput | null) {
    const chapters = plan ? buildChapterViews(plan) : [];
    const includedChapterCount = chapters.filter((chapter) => chapter.include_in_book !== false).length;
    const stage = planStage(plan);
    const outputData = bookOutputData(output);
    if (!output && !outputData.bookTitleInput) {
      outputData.bookTitleInput = defaultTitle(plan, output);
    }
    this.setData({
      plan,
      planTitle: defaultTitle(plan, output),
      stage,
      stageText: stageText(stage),
      chapters,
      totalChapterCount: includedChapterCount,
      allChaptersConfirmed: Boolean(output?.latest_version),
      ...outputData,
      bookPreviewChaptersExpanded: true,
      bookExportBusyTarget: "",
    });
  },

  changeBookTitle(event: TextInputEvent) {
    const value = event.detail.value;
    const storedTitle = latestBookTitle(this.data.bookOutput)
      || this.data.bookManuscriptTitle
      || this.data.bookPreviewTitle;
    this.setData({
      bookTitleInput: value,
      bookTitleChanged: trimText(value) !== trimText(storedTitle),
    });
    this.scheduleBookDesignAutosave();
  },

  changeBookMetadataField(event: TextInputEvent) {
    const key = datasetString(event, "key");
    const fieldByKey: Record<string, keyof BookExportData> = {
      subtitle: "bookSubtitleInput",
      narrator_name: "bookNarratorNameInput",
      prepared_by: "bookPreparedByInput",
      preface: "bookPrefaceInput",
      afterword: "bookAfterwordInput",
      back_cover_quote: "bookBackCoverQuoteInput",
    };
    const field = fieldByKey[key];
    if (!field) {
      return;
    }
    const value = event.detail.value;
    const nextData = { ...this.data, [field]: value } as BookExportData;
    this.setData({
      [field]: value,
      bookMetadataChanged: bookMetadataChanged(nextData),
      error: "",
    });
    this.scheduleBookDesignAutosave();
  },

  async suggestBookTitles() {
    if (!this.data.projectId || this.data.bookTitleSuggesting || this.data.bookTitleSaving) {
      return;
    }
    if (!this.data.bookContent) {
      this.setData({ error: "请先生成书稿" });
      return;
    }
    this.setData({ bookTitleSuggesting: true, error: "" });
    try {
      const response = await suggestMemoirBookOutputTitles(this.data.projectId, { count: 6 });
      const suggestions = (response.suggestions || []).map((item) => trimText(item)).filter(Boolean);
      this.setData({ bookTitleSuggestions: suggestions });
      if (!suggestions.length) {
        this.setData({ error: "暂时没有生成合适的书名" });
      }
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "小余起名失败" });
    } finally {
      this.setData({ bookTitleSuggesting: false });
    }
  },

  chooseBookTitleSuggestion(event: DatasetEvent) {
    const title = trimText(datasetString(event, "title"));
    if (!title) {
      return;
    }
    const storedTitle = latestBookTitle(this.data.bookOutput)
      || this.data.bookManuscriptTitle
      || this.data.bookPreviewTitle;
    this.setData({
      bookTitleInput: title,
      bookTitleChanged: title !== trimText(storedTitle),
      error: "",
    });
    this.scheduleBookDesignAutosave();
  },

  changeBookCoverStyle(event: PickerEvent) {
    const index = pickerIndex(event);
    const option = this.data.bookCoverStyleOptions[index];
    if (!option) {
      return;
    }
    this.setData({
      bookCoverStyle: option.value,
      bookCoverStylePreviewClass: option.previewClass,
      bookCoverStyleIndex: index,
      bookCoverStyleLabel: option.label,
      bookCoverStyleDetailText: option.detailText,
      bookCoverStyleChanged: option.value !== latestBookCoverStyle(this.data.bookOutput),
      error: "",
    });
    this.scheduleBookDesignAutosave();
  },

  scheduleBookDesignAutosave() {
    this.clearBookDesignAutosaveTimer();
    if (!this.data.bookOutput?.latest_version || this.data.bookViewingHistoricalVersion) {
      this.setData({ bookDesignSaveStatusText: bookDesignSaveStatusText(this.data) });
      return;
    }
    if (!bookDesignHasChanges(this.data)) {
      this.setData({ bookDesignSaveStatusText: "已保存" });
      return;
    }
    this.setData({ bookDesignSaveStatusText: "待自动保存" });
    if (this.data.bookTitleSaving) {
      return;
    }
    this.bookDesignAutosaveTimer = setTimeout(() => {
      this.bookDesignAutosaveTimer = null;
      void this.autosaveBookDesignSettings();
    }, BOOK_DESIGN_AUTOSAVE_DELAY_MS);
  },

  clearBookDesignAutosaveTimer() {
    if (this.bookDesignAutosaveTimer !== null) {
      clearTimeout(this.bookDesignAutosaveTimer);
      this.bookDesignAutosaveTimer = null;
    }
  },

  flushBookDesignAutosave() {
    const hadPendingAutosave = this.bookDesignAutosaveTimer !== null;
    this.clearBookDesignAutosaveTimer();
    if (hadPendingAutosave && bookDesignHasChanges(this.data)) {
      void this.autosaveBookDesignSettings();
    }
  },

  async autosaveBookDesignSettings() {
    this.clearBookDesignAutosaveTimer();
    if (!this.data.projectId || this.data.bookTitleSaving) {
      return;
    }
    if (!this.data.bookOutput?.latest_version) {
      this.setData({ bookDesignSaveStatusText: "生成时应用" });
      return;
    }
    if (this.data.bookViewingHistoricalVersion) {
      this.setData({
        error: "请切回最新版后再自动保存成书信息",
        bookDesignSaveStatusText: "切回最新版后自动保存",
      });
      return;
    }
    if (!bookDesignHasChanges(this.data)) {
      this.setData({ bookDesignSaveStatusText: "已保存" });
      return;
    }
    const title = trimText(this.data.bookTitleInput);
    if (!title) {
      this.setData({ error: "书名不能为空", bookDesignSaveStatusText: "保存失败" });
      return;
    }
    const coverStyle = this.data.bookCoverStyle;
    const metadata = currentBookMetadataPayload(this.data, true);
    this.setData({ bookTitleSaving: true, bookDesignSaveStatusText: "保存中", error: "" });
    try {
      const output = await updateMemoirBookOutput(this.data.projectId, {
        title,
        subtitle: metadata.subtitle,
        narrator_name: metadata.narrator_name,
        prepared_by: metadata.prepared_by,
        preface: metadata.preface,
        afterword: metadata.afterword,
        back_cover_quote: metadata.back_cover_quote,
        cover_style: coverStyle,
      });
      this.applyBookExportData(this.data.plan, output);
      const option = this.data.bookCoverStyleOptions.find((candidate) => candidate.value === coverStyle)
        || this.data.bookCoverStyleOptions[0];
      this.setData({
        bookTitleInput: title,
        bookSubtitleInput: metadata.subtitle,
        bookNarratorNameInput: metadata.narrator_name,
        bookPreparedByInput: metadata.prepared_by,
        bookPrefaceInput: metadata.preface,
        bookAfterwordInput: metadata.afterword,
        bookBackCoverQuoteInput: metadata.back_cover_quote,
        bookTitleChanged: false,
        bookMetadataChanged: false,
        bookCoverStyle: option.value,
        bookCoverStylePreviewClass: option.previewClass,
        bookCoverStyleIndex: Math.max(
          0,
          this.data.bookCoverStyleOptions.findIndex((candidate) => candidate.value === option.value),
        ),
        bookCoverStyleLabel: option.label,
        bookCoverStyleDetailText: option.detailText,
        bookCoverStyleChanged: false,
        bookDesignSaveStatusText: "已自动保存",
      });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "保存成书信息失败",
        bookDesignSaveStatusText: "保存失败",
      });
    } finally {
      this.setData({ bookTitleSaving: false });
    }
  },

  async saveBookDesignSettings() {
    if (this.data.bookTitleSaving || this.data.bookViewingHistoricalVersion) {
      return;
    }
    await this.autosaveBookDesignSettings();
  },

  async ensureBookDesignSavedForEditorialCopy() {
    this.clearBookDesignAutosaveTimer();
    if (!bookDesignHasChanges(this.data)) {
      return true;
    }
    await this.autosaveBookDesignSettings();
    return !bookDesignHasChanges(this.data);
  },

  async regenerateBookEditorialCopy(event: DatasetEvent) {
    const target = datasetString(event, "target");
    if (!isBookLevelEditorialCopyTarget(target)) {
      return;
    }
    if (!this.data.projectId || this.data.bookEditorialCopyBusyTarget === target || this.data.bookTitleSaving) {
      return;
    }
    const blockedMessage = bookEditorialCopyBlockedMessage(this.data);
    if (blockedMessage) {
      this.setData({ error: blockedMessage });
      return;
    }
    if (!(await this.ensureBookDesignSavedForEditorialCopy())) {
      return;
    }
    this.setData({ bookEditorialCopyBusyTarget: target, error: "" });
    try {
      await enqueueMemoirBookOutputEditorialCopyGeneration(this.data.projectId, { targets: [target] });
      wx.showToast({ title: "已后台生成", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "成书文案提交失败" });
    } finally {
      if (this.data.bookEditorialCopyBusyTarget === target) {
        this.setData({ bookEditorialCopyBusyTarget: "" });
      }
    }
  },

  async generateBook() {
    if (!this.data.projectId || this.data.generatingBook || this.data.bookTitleSaving) {
      return;
    }
    if (!this.data.bookContent) {
      this.setData({ error: "请先生成书稿" });
      return;
    }
    if (this.data.bookViewingHistoricalVersion) {
      this.setData({ error: "请切回最新版后再重新整理" });
      return;
    }
    if (!(await this.ensureBookDesignSavedForEditorialCopy())) {
      return;
    }
    const metadata = currentBookMetadataPayload(this.data, false);
    this.setData({ generatingBook: true, error: "" });
    try {
      await assembleMemoirBookOutput(this.data.projectId);
      const output = await updateMemoirBookOutput(this.data.projectId, {
        title: trimText(this.data.bookTitleInput) || undefined,
        ...metadata,
        cover_style: this.data.bookCoverStyle,
      });
      this.applyBookExportData(this.data.plan, output);
      this.setData({ exportNotice: "已根据各章确认正文更新全书稿。" });
      wx.showToast({ title: "书稿已更新", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "书稿整理提交失败" });
    } finally {
      this.setData({ generatingBook: false });
    }
  },

  changeBookOutputVersion(event: PickerEvent) {
    const index = pickerIndex(event);
    const option = this.data.bookVersionOptions[index];
    if (!option || !this.data.bookOutput) {
      return;
    }
    this.setData({
      ...bookOutputData(this.data.bookOutput, option.id),
      bookPreviewChaptersExpanded: true,
    });
  },

  toggleBookExportSourceAppendix(event: SwitchEvent) {
    this.setData({ bookExportShowSourceAppendix: Boolean(event.detail.value) });
  },

  previewExportActionHint(event: DatasetEvent) {
    this.setData({ exportActionHintText: exportActionHint(datasetString(event, "action")) });
  },

  toggleBookPreviewChapters() {
    this.setData({ bookPreviewChaptersExpanded: !this.data.bookPreviewChaptersExpanded });
  },

  openBookReader() {
    if (!this.data.projectId || !this.data.bookContent) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/book-reader/index", {
        projectId: this.data.projectId,
        versionId: this.data.bookSelectedVersionId,
      }),
    });
  },

  openBookHome() {
    wx.switchTab({ url: "/pages/book/index" });
  },

  toggleBookSettings() {
    this.setData({ showBookSettings: !this.data.showBookSettings });
  },

  async downloadBookOutputExport(event: DatasetEvent) {
    const format = datasetChoice(event, "format", BOOK_EXPORT_FORMATS, "pdf");
    const requestedLayout = datasetChoice(event, "layout", BOOK_EXPORT_LAYOUTS, "standard");
    const layout: MemoirBookOutputExportLayout = format === "pdf" && requestedLayout === "large_print"
      ? "large_print"
      : "standard";
    const action = format === "txt" ? "text" : (layout === "large_print" ? "print_pdf" : "reading_pdf");
    const target = `book-export:${format}:${layout}`;
    this.setData({ exportActionHintText: exportActionHint(action) });
    if (!this.data.projectId || this.data.bookExportBusyTarget) {
      return;
    }
    if (!this.data.bookContent) {
      this.setData({ error: "请先生成书稿" });
      return;
    }
    if (this.data.bookOutput?.is_stale) {
      this.setData({ error: "章节内容已更新，请先更新书稿再导出" });
      return;
    }
    this.setData({ bookExportBusyTarget: target, error: "" });
    try {
      const filePath = await downloadMemoirBookOutputExport(
        this.data.projectId,
        format,
        this.data.bookSelectedVersionId,
        layout,
        format === "pdf" ? this.data.bookExportShowSourceAppendix : true,
      );
      if (format === "pdf") {
        const wxWithDocument = wx as WxWithDocument;
        if (typeof wxWithDocument.openDocument === "function") {
          await new Promise<void>((resolve, reject) => {
            wxWithDocument.openDocument?.({
              filePath,
              fileType: "pdf",
              showMenu: true,
              success: () => resolve(),
              fail: (error) => reject(new Error(error.errMsg || "PDF 打开失败")),
            });
          });
        }
      }
      wx.showToast({ title: format === "pdf" ? "PDF 已打开" : "文本已下载", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "导出失败" });
    } finally {
      this.setData({ bookExportBusyTarget: "" });
    }
  },
});
