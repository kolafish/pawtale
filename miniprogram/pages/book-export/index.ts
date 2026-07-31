import type {
  MemoirBookOutput,
  MemoirBookOutputExportFormat,
  MemoirBookOutputExportLayout,
  MemoirBookOutputVersion,
  MemoirBookPlan,
} from "../../domain/memoir-book";
import type { MemoirAutomationEvent } from "../../domain/memoir-voice-session";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { definePage } from "../../services/page-definition";
import {
  downloadMemoirBookOutputExport,
  assembleMemoirBookOutput,
  enqueueMemoirBookOutputEditorialCopyGeneration,
  getMemoirBookOutput,
  getMemoirBookOutputVersion,
  getMemoirBookPlan,
  suggestMemoirBookOutputTitles,
  updateMemoirBookOutput,
} from "../../services/memoir-book-api";
import { listMemoirAutomationEvents } from "../../services/memoir-voice-session-api";
import { optionalApiResource } from "../../services/request";
import { routeAuthError } from "../../services/navigation";
import { writeBookWorkspaceNavigationTarget } from "../../services/book-workspace-navigation";
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
  buildAutomationEventViews,
  trimText,
  type BookData,
  type BookAutomationEventView,
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
import { PollingTimer } from "../../services/polling-timer";
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
  | "planTitle"
  | "totalChapterCount"
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
  pageClass: string;
  requestedAction: string;
  exportNotice: string;
  exportActionHintText: string;
  bookEditorialCopyEventItem: BookAutomationEventView | null;
  bookOutputAvailable: boolean;
  showBookSettings: boolean;
};

const BOOK_DESIGN_AUTOSAVE_DELAY_MS = 900;
const BOOK_EDITORIAL_COPY_POLL_INTERVAL_MS = 3000;
const PAGE_CLASS = "page book-export-page ui-page-shell ui-accessible-page";
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
  return (
    latestBookTitle(output)
    || trimText(plan?.title || "").replace("章节规划", "").replace("主题回忆录", "回忆录")
    || "家庭回忆录"
  );
}

definePage<BookExportData>()({
  bookDesignAutosaveTimer: null as number | null,
  bookEditorialCopyEvent: null as MemoirAutomationEvent | null,
  bookEditorialCopyPoller: new PollingTimer(BOOK_EDITORIAL_COPY_POLL_INTERVAL_MS),
  bookOutput: null as MemoirBookOutput | null,
  bookOutputVersion: null as MemoirBookOutputVersion | null,
  bookPlan: null as MemoirBookPlan | null,

  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    loading: false,
    error: "",
    projectId: "",
    planTitle: "阅读与导出",
    totalChapterCount: 0,
    bookOutputAvailable: false,
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
    bookEditorialCopyEventItem: null,
    showBookSettings: false,
    ...bookOutputData(null),
  },

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    const action = queryParam(query, "action") || "preview";
    const showBookSettings = queryParam(query, "settings") === "book";
    this.setData({
      projectId,
      requestedAction: action,
      exportNotice: exportNotice(action),
      exportActionHintText: exportActionHint(action),
      showBookSettings,
    });
    if (!projectId) {
      this.setData({ error: "缺少回忆录项目信息" });
      return;
    }
    await this.loadBookExportPage(projectId);
  },

  onShow() {
    this.setData(accessibilityPagePatch(PAGE_CLASS));
  },

  onUnload() {
    this.clearBookEditorialCopyPoll();
    this.flushBookDesignAutosave();
  },

  async loadBookExportPage(projectId: string) {
    this.setData({ loading: true, error: "" });
    try {
      const [plan, output] = await Promise.all([
        optionalApiResource(getMemoirBookPlan(projectId)),
        optionalApiResource(getMemoirBookOutput(projectId)),
      ]);
      this.applyBookExportData(plan, output);
      if (this.data.showBookSettings) {
        await this.refreshBookEditorialCopyProgress();
      }
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "阅读与导出页加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  applyBookEditorialCopyEvent(event: MemoirAutomationEvent) {
    this.bookEditorialCopyEvent = event;
    this.setData({
      bookEditorialCopyEventItem: buildAutomationEventViews([event])[0] || null,
    });
    this.clearBookEditorialCopyPoll();
    if (event.status === "pending") {
      this.scheduleBookEditorialCopyPoll();
    }
  },

  async refreshBookEditorialCopyProgress() {
    if (!this.data.projectId) {
      return;
    }
    const previous = this.bookEditorialCopyEvent;
    try {
      const response = await listMemoirAutomationEvents(this.data.projectId, 20);
      const candidates = (response.items || [])
        .filter((candidate) => candidate.event_type === "book_editorial_copy_generated")
        .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
      const event = previous
        ? candidates.find((candidate) => candidate.id === previous.id)
        : candidates[0];
      if (!event) {
        return;
      }
      this.applyBookEditorialCopyEvent(event);
      const shouldReloadOutput = event.status === "completed" && (
        previous?.status === "pending"
        || (!previous && event.updated_at > (this.bookOutput?.updated_at || event.updated_at))
      );
      if (shouldReloadOutput) {
        const output = await optionalApiResource(getMemoirBookOutput(this.data.projectId));
        this.applyBookExportData(this.bookPlan, output);
      }
      if (previous?.status !== "pending" || event.status === "pending") {
        return;
      }
      const targetLabel = event.metadata_json?.target_label;
      const label = typeof targetLabel === "string" && targetLabel.trim()
        ? targetLabel.trim()
        : "成书文案";
      wx.showToast({
        title: event.status === "completed" ? `${label}已更新` : `${label}重写失败`,
        icon: event.status === "completed" ? "success" : "none",
      });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      if (previous?.status === "pending") {
        this.scheduleBookEditorialCopyPoll();
      }
    }
  },

  scheduleBookEditorialCopyPoll() {
    if (
      this.bookEditorialCopyPoller.pending
      || this.bookEditorialCopyEvent?.status !== "pending"
    ) {
      return;
    }
    this.bookEditorialCopyPoller.schedule(() => {
      void this.refreshBookEditorialCopyProgress();
    });
  },

  clearBookEditorialCopyPoll() {
    this.bookEditorialCopyPoller.cancel();
  },

  applyBookExportData(plan: MemoirBookPlan | null, output: MemoirBookOutput | null) {
    this.bookPlan = plan;
    this.bookOutput = output;
    this.bookOutputVersion = output?.latest_version || null;
    const includedChapterCount = (plan?.chapters || [])
      .filter((chapter) => chapter.include_in_book !== false)
      .length;
    const outputData = bookOutputData(output, this.bookOutputVersion);
    if (!output && !outputData.bookTitleInput) {
      outputData.bookTitleInput = defaultTitle(plan, output);
    }
    this.setData({
      planTitle: defaultTitle(plan, output),
      totalChapterCount: includedChapterCount,
      bookOutputAvailable: Boolean(output?.latest_version),
      ...outputData,
      bookPreviewChaptersExpanded: true,
      bookExportBusyTarget: "",
    });
  },

  changeBookTitle(event: TextInputEvent) {
    const value = event.detail.value;
    const storedTitle = latestBookTitle(this.bookOutput)
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
      bookMetadataChanged: bookMetadataChanged(nextData, this.bookOutput),
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
    const storedTitle = latestBookTitle(this.bookOutput)
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
      bookCoverStyleChanged: option.value !== latestBookCoverStyle(this.bookOutput),
      error: "",
    });
    this.scheduleBookDesignAutosave();
  },

  scheduleBookDesignAutosave() {
    this.clearBookDesignAutosaveTimer();
    if (!this.bookOutput?.latest_version || this.data.bookViewingHistoricalVersion) {
      this.setData({
        bookDesignSaveStatusText: bookDesignSaveStatusText(
          this.data,
          this.data.bookOutputAvailable,
        ),
      });
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
    if (!this.bookOutput?.latest_version) {
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
      this.applyBookExportData(this.bookPlan, output);
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
    if (!this.data.projectId || this.data.bookEditorialCopyBusyTarget || this.data.bookTitleSaving) {
      return;
    }
    const blockedMessage = bookEditorialCopyBlockedMessage(
      this.data,
      this.data.bookOutputAvailable,
    );
    if (blockedMessage) {
      this.setData({ error: blockedMessage });
      return;
    }
    if (!(await this.ensureBookDesignSavedForEditorialCopy())) {
      return;
    }
    this.setData({
      bookEditorialCopyBusyTarget: target,
      error: "",
    });
    try {
      const queued = await enqueueMemoirBookOutputEditorialCopyGeneration(
        this.data.projectId,
        { targets: [target] },
      );
      this.applyBookEditorialCopyEvent(queued);
      wx.showToast({ title: "已开始后台生成", icon: "success" });
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
      this.applyBookExportData(this.bookPlan, output);
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

  async changeBookOutputVersion(event: PickerEvent) {
    const index = pickerIndex(event);
    const option = this.data.bookVersionOptions[index];
    if (!option || !this.bookOutput || !this.data.projectId) {
      return;
    }
    this.setData({ bookExportBusyTarget: "book-version", error: "" });
    try {
      const version = option.isLatest
        ? this.bookOutput.latest_version || null
        : await getMemoirBookOutputVersion(this.data.projectId, option.id);
      this.bookOutputVersion = version;
      this.setData({
        ...bookOutputData(this.bookOutput, version),
        bookPreviewChaptersExpanded: true,
      });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "书稿版本加载失败" });
    } finally {
      this.setData({ bookExportBusyTarget: "" });
    }
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
    writeBookWorkspaceNavigationTarget("tools");
    wx.switchTab({ url: "/pages/book/index" });
  },

  toggleBookSettings() {
    const showBookSettings = !this.data.showBookSettings;
    this.setData({ showBookSettings });
    if (showBookSettings) {
      void this.refreshBookEditorialCopyProgress();
    }
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
    if (this.bookOutput?.is_stale) {
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
