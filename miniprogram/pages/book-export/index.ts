import type {
  MemoirBookCustomLayout,
  MemoirBookCoverStyle,
  MemoirCoverImageLayout,
  MemoirBookManuscriptTemplate,
  MemoirBookOutput,
  MemoirBookOutputExportFormat,
  MemoirBookOutputExportLayout,
  MemoirBookOutputVersion,
  MemoirBookPlan,
  MemoirBookEditorialCopyJob,
  MemoirBookEditorialCopyJobTarget,
  MemoirBookEditorialCopyJobStatus,
} from "../../domain/memoir-book";
import type { MemoirDraftProgress } from "../../domain/memoir-snapshots";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
} from "../../services/accessibility-settings";
import { definePage, pageRuntime } from "../../services/page-definition";
import {
  downloadMemoirBookOutputExport,
  assembleMemoirBookOutput,
  enqueueMemoirBookOutputEditorialCopyGeneration,
  getMemoirBookEditorialCopyJobs,
  getMemoirBookOutput,
  getMemoirBookOutputVersion,
  getMemoirBookPlan,
  uploadMemoirImageAsset,
  createMemoirBookImagePlacement,
  deleteMemoirBookImagePlacement,
  updateMemoirBookImagePlacement,
  listMemoirImageAssets,
  downloadMemoirImageAssetFile,
  suggestMemoirBookOutputTitles,
  updateMemoirBookOutput,
} from "../../services/memoir-book-api";
import { getMemoirDraftProgress } from "../../services/memoir-snapshot-api";
import { optionalApiResource } from "../../services/request";
import { returnToBook, routeAuthError } from "../../services/navigation";
import { isShareFileMessageCancelError, shareLocalFileMessage } from "../../services/share";
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
  trimText,
  type BookOutputData,
} from "../book/book-view-model";
import {
  bookDesignHasChanges,
  bookDesignSaveStatusText,
  bookEditorialCopyBlockedMessage,
  bookMetadataChanged,
  currentBookMetadataPayload,
  isBookLevelEditorialCopyTarget,
  latestBookTitle,
} from "../book/book-design-model";
import { chooseImageFile, cropImageFile, editImageFile } from "../../services/authenticated-media";
import { PollingTimer } from "../../services/polling-timer";
import { setPageNavigationTitle } from "../../services/page-navigation";

interface BookExportDataset {
  action?: string;
  format?: string;
  key?: string;
  layout?: string;
  target?: string;
  title?: string;
  section?: string;
  template?: string;
}

type DatasetEvent = PageDatasetEvent<BookExportDataset>;
type TextInputEvent = PageTextInputEvent<BookExportDataset>;
type PickerEvent = PagePickerEvent<BookExportDataset>;
type SwitchEvent = PageSwitchEvent<BookExportDataset>;
type BookExportSection = "" | "export" | "title" | "editorial" | "layout" | "history" | "advanced";

interface BookEditorialCopyJobView {
  jobId: string;
  status: MemoirBookEditorialCopyJobStatus | "idle";
  statusText: string;
  errorText: string;
  active: boolean;
  visible: boolean;
  updatedAt: string;
}

type BookEditorialCopyJobs = Record<MemoirBookEditorialCopyJobTarget, BookEditorialCopyJobView>;

const EDITORIAL_TARGETS: readonly MemoirBookEditorialCopyJobTarget[] = ["preface", "afterword", "back_cover_quote"];
const EDITORIAL_TARGET_LABELS: Record<MemoirBookEditorialCopyJobTarget, string> = {
  preface: "前言",
  afterword: "后记",
  back_cover_quote: "封底摘要",
};

function emptyEditorialCopyJobs(): BookEditorialCopyJobs {
  const empty = (): BookEditorialCopyJobView => ({
    jobId: "", status: "idle", statusText: "", errorText: "", active: false, visible: false, updatedAt: "",
  });
  return { preface: empty(), afterword: empty(), back_cover_quote: empty() };
}

function editorialCopyJobView(job: MemoirBookEditorialCopyJob): BookEditorialCopyJobView {
  const statusText: Record<MemoirBookEditorialCopyJobStatus, string> = {
    pending: "排队中",
    running: "执行中",
    succeeded: "已完成",
    failed: "失败，可重试",
    skipped: "失败，可重试",
  };
  return {
    jobId: job.job_id,
    status: job.status,
    statusText: statusText[job.status],
    errorText: job.status === "failed" ? job.error_message : "",
    active: job.status === "pending" || job.status === "running",
    visible: true,
    updatedAt: job.updated_at,
  };
}

const SECTION_TITLES: Record<Exclude<BookExportSection, "">, string> = {
  export: "导出设置",
  title: "书名与信息",
  editorial: "前言与后记",
  layout: "封面与版式",
  history: "历史版本",
  advanced: "重新整理书稿",
};

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

interface BookLayoutOption {
  value: MemoirBookManuscriptTemplate | "current";
  label: string;
  description: string;
  previewClass: string;
  coverPreviewClass?: string;
  coverStyle: MemoirBookCoverStyle;
  coverPreviewImage: string;
  ornamentThumbnail: string;
}

const BOOK_LAYOUT_OPTIONS = [
  {
    value: "heritage_classic",
    label: "典藏文集版",
    description: "银杏纹样与典雅章首页，适合家族故事",
    previewClass: "heritage",
    coverStyle: "heritage_classic",
    coverPreviewImage: "/assets/book_covers/heritage_classic.jpg",
    ornamentThumbnail: "/assets/book_ornaments/heritage_classic.png",
  },
  {
    value: "warm_journal",
    label: "家书手记版",
    description: "信笺、桂花与温暖的阅读节奏",
    previewClass: "journal",
    coverStyle: "warm_journal",
    coverPreviewImage: "/assets/book_covers/warm_journal.jpg",
    ornamentThumbnail: "/assets/book_ornaments/warm_journal.png",
  },
  {
    value: "modern_minimal",
    label: "现代简约版",
    description: "深青封面与清晰的章节层级",
    previewClass: "modern",
    coverStyle: "modern_minimal",
    coverPreviewImage: "/assets/book_covers/modern_minimal.jpg",
    ornamentThumbnail: "/assets/book_ornaments/modern_minimal.png",
  },
] satisfies BookLayoutOption[];

const BOOK_LAYOUT_TEMPLATES: readonly MemoirBookManuscriptTemplate[] = [
  ...BOOK_LAYOUT_OPTIONS.map((option) => option.value),
];
const CUSTOM_FONT_FAMILY_OPTIONS: { value: MemoirBookCustomLayout["font_family"]; label: string }[] = [
  { value: "auto", label: "跟随版式" },
  { value: "serif", label: "宋体阅读" },
  { value: "sans", label: "黑体清晰" },
];
const CUSTOM_FONT_SCALE_OPTIONS: { value: MemoirBookCustomLayout["font_scale"]; label: string }[] = [
  { value: "standard", label: "标准" },
  { value: "large", label: "大字" },
];
const CUSTOM_LINE_SPACING_OPTIONS: { value: MemoirBookCustomLayout["line_spacing"]; label: string }[] = [
  { value: "compact", label: "紧凑" },
  { value: "standard", label: "标准" },
  { value: "relaxed", label: "舒展" },
];
const CUSTOM_PAGE_MARGIN_OPTIONS: { value: MemoirBookCustomLayout["page_margin"]; label: string }[] = [
  { value: "narrow", label: "窄" },
  { value: "standard", label: "标准" },
  { value: "wide", label: "宽" },
];
const COVER_IMAGE_LAYOUT_OPTIONS: { value: MemoirCoverImageLayout; label: string; description: string }[] = [
  { value: "cover_frame", label: "放进相框", description: "保留版式底纹，照片完整不裁切" },
  { value: "cover_full_bleed", label: "铺满封面", description: "适合竖版照片，文字在下方" },
];
const COVER_IMAGE_LAYOUTS = COVER_IMAGE_LAYOUT_OPTIONS.map((option) => option.value);
// Full-bleed photos fill the A5 cover, so they are framed close to its ratio.
const FULL_BLEED_COVER_CROP_SCALE = "3:4";

const DEFAULT_CUSTOM_LAYOUT: MemoirBookCustomLayout = {
  font_family: "auto",
  font_scale: "standard",
  line_spacing: "standard",
  page_margin: "standard",
};

function customLayoutFromOutput(output: MemoirBookOutput | null): MemoirBookCustomLayout {
  const settings = output?.latest_version?.manuscript?.render_settings?.custom_layout;
  return {
    font_family: CUSTOM_FONT_FAMILY_OPTIONS.find((option) => option.value === settings?.font_family)?.value || "auto",
    font_scale: settings?.font_scale === "large" ? "large" : "standard",
    line_spacing: CUSTOM_LINE_SPACING_OPTIONS.find((option) => option.value === settings?.line_spacing)?.value || "standard",
    page_margin: CUSTOM_PAGE_MARGIN_OPTIONS.find((option) => option.value === settings?.page_margin)?.value || "standard",
  };
}

function customLayoutIndex<T extends string>(options: { value: T }[], value: T): number {
  return Math.max(0, options.findIndex((option) => option.value === value));
}

async function openBookPdf(filePath: string): Promise<void> {
  const wxWithDocument = wx as WxWithDocument;
  if (typeof wxWithDocument.openDocument !== "function") {
    throw new Error("当前微信版本暂不支持预览 PDF");
  }
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

type BookExportData = BookOutputData & {
  loading: boolean;
  loadError: string;
  error: string;
  projectId: string;
  planTitle: string;
  totalChapterCount: number;
  bookExportBusyTarget: string;
  bookExportBusyText: string;
  bookExportShowSourceAppendix: boolean;
  bookTitleSaving: boolean;
  bookTitleSuggesting: boolean;
  bookEditorialCopyBusyTarget: string;
  bookEditorialCopyStatusError: string;
  pageClass: string;
  generatingBook: boolean;
  exportNotice: string;
  bookEditorialCopyJobs: BookEditorialCopyJobs;
  bookOutputAvailable: boolean;
  activeSection: BookExportSection;
  pagePurpose: "files" | "settings" | "regenerate";
  bookLayoutOptions: BookLayoutOption[];
  bookLayoutTemplate: MemoirBookManuscriptTemplate;
  bookCustomLayout: MemoirBookCustomLayout;
  bookLayoutFontFamilyOptions: typeof CUSTOM_FONT_FAMILY_OPTIONS;
  bookLayoutFontFamilyIndex: number;
  bookUploadedCoverAssetId: string;
  bookUploadedCoverPath: string;
  bookCoverImageLayout: MemoirCoverImageLayout;
  bookCoverImageLayoutOptions: typeof COVER_IMAGE_LAYOUT_OPTIONS;
  bookLayoutFontScaleOptions: typeof CUSTOM_FONT_SCALE_OPTIONS;
  bookLayoutLineSpacingOptions: typeof CUSTOM_LINE_SPACING_OPTIONS;
  bookLayoutPageMarginOptions: typeof CUSTOM_PAGE_MARGIN_OPTIONS;
  bookLayoutFontScaleIndex: number;
  bookLayoutLineSpacingIndex: number;
  bookLayoutPageMarginIndex: number;
  bookLayoutCustomChanged: boolean;
  bookLayoutBusyTarget: string;
  bookLayoutFeedbackTarget: string;
  bookLayoutFeedbackText: string;
  bookLayoutFeedbackError: boolean;
  bookLayoutSampleTitle: string;
  bookLayoutSampleExcerpt: string;
  bookLayoutSampleSectionTitle: string;
  bookLayoutSampleQuote: string;
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
  return "";
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
  bookEditorialCopyRefreshing: false,
  bookEditorialCopyPageVisible: true,
  bookEditorialCopySubmissionVersion: 0,
  bookEditorialCopyPoller: pageRuntime(
    () => new PollingTimer(BOOK_EDITORIAL_COPY_POLL_INTERVAL_MS),
  ),
  bookOutput: null as MemoirBookOutput | null,
  bookOutputVersion: null as MemoirBookOutputVersion | null,
  bookPlan: null as MemoirBookPlan | null,
  bookDraftProgress: null as MemoirDraftProgress | null,
  overviewScrollTop: 0,

  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    loading: false,
    loadError: "",
    error: "",
    projectId: "",
    planTitle: "阅读与导出",
    totalChapterCount: 0,
    bookOutputAvailable: false,
    bookExportBusyTarget: "",
    bookExportBusyText: "",
    bookExportShowSourceAppendix: false,
    bookTitleSaving: false,
    bookTitleSuggesting: false,
    bookEditorialCopyBusyTarget: "",
    bookEditorialCopyStatusError: "",
    generatingBook: false,
    exportNotice: exportNotice("preview"),
    bookEditorialCopyJobs: emptyEditorialCopyJobs(),
    activeSection: "",
    pagePurpose: "files",
    bookLayoutOptions: BOOK_LAYOUT_OPTIONS,
    bookLayoutTemplate: "classic_family",
    bookCustomLayout: DEFAULT_CUSTOM_LAYOUT,
    bookLayoutFontFamilyOptions: CUSTOM_FONT_FAMILY_OPTIONS,
    bookLayoutFontFamilyIndex: 0,
    bookUploadedCoverAssetId: "",
    bookUploadedCoverPath: "",
    bookCoverImageLayout: "cover_frame",
    bookCoverImageLayoutOptions: COVER_IMAGE_LAYOUT_OPTIONS,
    bookLayoutFontScaleOptions: CUSTOM_FONT_SCALE_OPTIONS,
    bookLayoutLineSpacingOptions: CUSTOM_LINE_SPACING_OPTIONS,
    bookLayoutPageMarginOptions: CUSTOM_PAGE_MARGIN_OPTIONS,
    bookLayoutFontScaleIndex: 0,
    bookLayoutLineSpacingIndex: 1,
    bookLayoutPageMarginIndex: 1,
    bookLayoutCustomChanged: false,
    bookLayoutBusyTarget: "",
    bookLayoutFeedbackTarget: "",
    bookLayoutFeedbackText: "",
    bookLayoutFeedbackError: false,
    bookLayoutSampleTitle: "那些年的故事",
    bookLayoutSampleExcerpt: "回忆从家里的故事开始，慢慢写下那些值得留住的日子。",
    bookLayoutSampleSectionTitle: "院子里的夏天",
    bookLayoutSampleQuote: "家里的故事，总会有人记得。",
    ...bookOutputData(null),
  },

  async onLoad(query: PageQuery = {}) {
    const projectId = queryParam(query, "projectId");
    const action = queryParam(query, "action") || "preview";
    const pagePurpose = action === "settings" || queryParam(query, "settings") === "book"
      ? "settings"
      : action === "regenerate" ? "regenerate" : "files";
    const activeSection: BookExportSection = pagePurpose === "regenerate" ? "advanced" : "";
    this.setData({
      projectId,
      exportNotice: exportNotice(action),
      pagePurpose,
      activeSection,
    });
    if (!projectId) {
      this.setData({ loadError: "缺少回忆录项目信息" });
      return;
    }
    await this.loadBookExportPage(projectId);
  },

  onShow() {
    this.bookEditorialCopyPageVisible = true;
    this.setData(accessibilityPagePatch(PAGE_CLASS));
    setPageNavigationTitle(this.data.activeSection ? SECTION_TITLES[this.data.activeSection] : (this.data.pagePurpose === "settings" ? "书的设置" : "导出文件"));
    if (this.data.activeSection === "editorial" && this.bookOutput) {
      void this.refreshBookEditorialCopyProgress();
    }
  },

  onHide() {
    this.bookEditorialCopyPageVisible = false;
    this.clearBookEditorialCopyPoll();
  },

  onPageScroll(event: { scrollTop: number }) {
    if (!this.data.activeSection) {
      this.overviewScrollTop = event.scrollTop;
    }
  },

  onUnload() {
    this.clearBookEditorialCopyPoll();
    this.flushBookDesignAutosave();
  },

  async loadBookExportPage(projectId: string) {
    this.setData({ loading: true, loadError: "", error: "" });
    try {
      const [plan, output, progress] = await Promise.all([
        optionalApiResource(getMemoirBookPlan(projectId)),
        optionalApiResource(getMemoirBookOutput(projectId)),
        optionalApiResource(getMemoirDraftProgress(projectId)),
      ]);
      this.bookDraftProgress = progress;
      this.applyBookExportData(plan, output);
      if (this.data.activeSection === "editorial") {
        await this.refreshBookEditorialCopyProgress();
      }
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ loadError: error instanceof Error ? error.message : "阅读与导出页加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  applyBookEditorialCopyJobs(jobs: MemoirBookEditorialCopyJob[]) {
    const next = emptyEditorialCopyJobs();
    for (const job of jobs) {
      if (EDITORIAL_TARGETS.includes(job.target)) {
        next[job.target] = editorialCopyJobView(job);
      }
    }
    this.setData({ bookEditorialCopyJobs: next });
    this.clearBookEditorialCopyPoll();
    if (EDITORIAL_TARGETS.some((target) => next[target]?.active)) {
      this.scheduleBookEditorialCopyPoll();
    }
  },

  async refreshBookEditorialCopyProgress() {
    if (!this.data.projectId || this.bookEditorialCopyRefreshing) {
      return;
    }
    this.bookEditorialCopyRefreshing = true;
    const submissionVersion = this.bookEditorialCopySubmissionVersion;
    const previous = this.data.bookEditorialCopyJobs;
    try {
      const response = await getMemoirBookEditorialCopyJobs(this.data.projectId);
      if (submissionVersion !== this.bookEditorialCopySubmissionVersion) {
        return;
      }
      const jobs = response.items || [];
      const completed = jobs.filter((job) => (
        job.status === "succeeded" && (
          previous[job.target]?.active
          || (!previous[job.target].visible && job.updated_at > (this.bookOutput?.updated_at || job.updated_at))
        )
      ));
      const failed = jobs.filter((job) => (
        (job.status === "failed" || job.status === "skipped") && previous[job.target]?.active
      ));
      if (completed.length) {
        const output = await optionalApiResource(getMemoirBookOutput(this.data.projectId));
        if (submissionVersion !== this.bookEditorialCopySubmissionVersion) {
          return;
        }
        this.applyBookExportData(this.bookPlan, output, true);
      }
      this.applyBookEditorialCopyJobs(jobs);
      this.setData({ bookEditorialCopyStatusError: "" });
      if (completed.length) {
        wx.showToast({
          title: completed.length === 1 ? `${EDITORIAL_TARGET_LABELS[completed[0].target]}已更新` : "文案已更新",
          icon: "success",
        });
      } else if (failed.length) {
        wx.showToast({
          title: failed.length === 1 ? `${EDITORIAL_TARGET_LABELS[failed[0].target]}重写失败` : "文案重写失败",
          icon: "none",
        });
      }
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ bookEditorialCopyStatusError: "任务进度暂时无法更新，请稍后再试" });
      this.scheduleBookEditorialCopyPoll();
    } finally {
      this.bookEditorialCopyRefreshing = false;
    }
  },

  scheduleBookEditorialCopyPoll() {
    if (
      this.bookEditorialCopyPoller.pending
      || !this.bookEditorialCopyPageVisible
      || this.data.activeSection !== "editorial"
      || (
        !this.data.bookEditorialCopyStatusError
        && !EDITORIAL_TARGETS.some((target) => this.data.bookEditorialCopyJobs[target].active)
      )
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

  applyBookExportData(plan: MemoirBookPlan | null, output: MemoirBookOutput | null, preserveLayoutChanges = false) {
    this.bookPlan = plan;
    this.bookOutput = output;
    this.bookOutputVersion = output?.latest_version || null;
    const outputData = bookOutputData(output, this.bookOutputVersion, this.bookDraftProgress);
    const bookLayoutTemplate = output?.latest_version?.manuscript?.profile.template || "classic_family";
    const matchingLayout = BOOK_LAYOUT_OPTIONS.find((option) => option.value === bookLayoutTemplate);
    const bookLayoutOptions: BookLayoutOption[] = [...BOOK_LAYOUT_OPTIONS];
    if (outputData.bookManuscriptAvailable && matchingLayout?.coverStyle !== outputData.bookCoverStyle) {
      bookLayoutOptions.unshift({
        value: "current",
        label: outputData.bookManuscriptTemplateLabel,
        description: "当前封面与内页示意，完整效果请预览 PDF。",
        previewClass: matchingLayout?.previewClass || "classic",
        coverPreviewClass: outputData.bookCoverStylePreviewClass,
        coverStyle: outputData.bookCoverStyle,
        coverPreviewImage: outputData.bookCoverStylePreviewImage,
        ornamentThumbnail: matchingLayout?.ornamentThumbnail || "",
      });
    }
    const storedLayout = customLayoutFromOutput(output);
    const bookCustomLayout = preserveLayoutChanges && this.data.bookLayoutCustomChanged
      ? this.data.bookCustomLayout : storedLayout;
    if (!output && !outputData.bookTitleInput) {
      outputData.bookTitleInput = defaultTitle(plan, output);
    }
    this.setData({
      planTitle: defaultTitle(plan, output),
      totalChapterCount: outputData.bookPreviewChapters.length || outputData.bookPreviewTocItems.length,
      bookOutputAvailable: Boolean(output?.latest_version),
      ...outputData,
      bookLayoutTemplate,
      bookLayoutOptions,
      bookCustomLayout,
      bookLayoutFontFamilyIndex: customLayoutIndex(CUSTOM_FONT_FAMILY_OPTIONS, bookCustomLayout.font_family),
      bookLayoutFontScaleIndex: customLayoutIndex(CUSTOM_FONT_SCALE_OPTIONS, bookCustomLayout.font_scale),
      bookLayoutLineSpacingIndex: customLayoutIndex(CUSTOM_LINE_SPACING_OPTIONS, bookCustomLayout.line_spacing),
      bookLayoutPageMarginIndex: customLayoutIndex(CUSTOM_PAGE_MARGIN_OPTIONS, bookCustomLayout.page_margin),
      bookLayoutCustomChanged: bookCustomLayout.font_family !== storedLayout.font_family
        || bookCustomLayout.font_scale !== storedLayout.font_scale
        || bookCustomLayout.line_spacing !== storedLayout.line_spacing
        || bookCustomLayout.page_margin !== storedLayout.page_margin,
      bookLayoutSampleTitle: outputData.bookPreviewChapters[0]?.title || "那些年的故事",
      bookLayoutSampleExcerpt: outputData.bookPreviewChapters[0]?.excerpt || "回忆从家里的故事开始，慢慢写下那些值得留住的日子。",
      bookLayoutSampleSectionTitle: (outputData.bookPreviewChapters[0]?.sections?.[0]?.title || "院子里的夏天").slice(0, 30),
      bookLayoutSampleQuote: (output?.latest_version?.manuscript?.chapters?.[0]?.pull_quotes?.[0]?.text || "家里的故事，总会有人记得。").slice(0, 48),
      bookExportBusyTarget: "",
    });
    this.applyUploadedCoverPreview(this.bookOutputVersion);
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
      });
      this.applyBookExportData(this.bookPlan, output, true);
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
    if (this.data.bookEditorialCopyJobs[target]?.active) {
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
    this.bookEditorialCopySubmissionVersion += 1;
    try {
      const queued = await enqueueMemoirBookOutputEditorialCopyGeneration(
        this.data.projectId,
        { targets: [target] },
      );
      const job = editorialCopyJobView({
        target,
        job_id: queued.editing_job_id || "",
        status: "pending",
        error_message: "",
        updated_at: queued.updated_at,
      });
      this.setData({
        bookEditorialCopyJobs: { ...this.data.bookEditorialCopyJobs, [target]: job },
      });
      this.scheduleBookEditorialCopyPoll();
      wx.showToast({ title: "已加入后台任务", icon: "success" });
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
      const outputData = bookOutputData(this.bookOutput, version, this.bookDraftProgress);
      this.setData({
        ...outputData,
        totalChapterCount: outputData.bookPreviewChapters.length || outputData.bookPreviewTocItems.length,
      });
      this.applyUploadedCoverPreview(version);
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

  changeBookCustomLayout(event: PickerEvent) {
    const key = datasetString(event, "key");
    const index = pickerIndex(event);
    const next = { ...this.data.bookCustomLayout };
    if (key === "font_family") {
      const option = CUSTOM_FONT_FAMILY_OPTIONS[index];
      if (!option) return;
      next.font_family = option.value;
    } else if (key === "font_scale") {
      const option = CUSTOM_FONT_SCALE_OPTIONS[index];
      if (!option) return;
      next.font_scale = option.value;
    } else if (key === "line_spacing") {
      const option = CUSTOM_LINE_SPACING_OPTIONS[index];
      if (!option) return;
      next.line_spacing = option.value;
    } else if (key === "page_margin") {
      const option = CUSTOM_PAGE_MARGIN_OPTIONS[index];
      if (!option) return;
      next.page_margin = option.value;
    } else {
      return;
    }
    const stored = customLayoutFromOutput(this.bookOutput);
    this.setData({
      bookCustomLayout: next,
      bookLayoutFontFamilyIndex: customLayoutIndex(CUSTOM_FONT_FAMILY_OPTIONS, next.font_family),
      bookLayoutFontScaleIndex: customLayoutIndex(CUSTOM_FONT_SCALE_OPTIONS, next.font_scale),
      bookLayoutLineSpacingIndex: customLayoutIndex(CUSTOM_LINE_SPACING_OPTIONS, next.line_spacing),
      bookLayoutPageMarginIndex: customLayoutIndex(CUSTOM_PAGE_MARGIN_OPTIONS, next.page_margin),
      bookLayoutCustomChanged: next.font_family !== stored.font_family || next.font_scale !== stored.font_scale
        || next.line_spacing !== stored.line_spacing
        || next.page_margin !== stored.page_margin,
      error: "",
    });
  },

  async previewBookLayout(event: DatasetEvent) {
    const adjusting = datasetString(event, "template") === "adjust";
    const template = datasetChoice(event, "template", [...BOOK_LAYOUT_TEMPLATES, "current"] as const, "heritage_classic");
    const target = adjusting ? "adjust" : template;
    const pendingLayout = { ...this.data.bookCustomLayout };
    if (!this.data.projectId || this.data.bookLayoutBusyTarget || this.data.bookExportBusyTarget) return;
    if (!this.bookOutput?.latest_version) {
      this.setData({
        error: "请先生成书稿",
        bookLayoutFeedbackTarget: template,
        bookLayoutFeedbackText: "请先生成书稿",
        bookLayoutFeedbackError: true,
      });
      return;
    }
    if (!this.data.bookManuscriptAvailable) {
      this.setData({
        error: "请先更新书稿，再预览版式",
        bookLayoutFeedbackTarget: template,
        bookLayoutFeedbackText: "请先更新书稿，再预览版式",
        bookLayoutFeedbackError: true,
      });
      return;
    }
    const isStale = this.bookOutput.is_stale;
    this.setData({
      bookLayoutBusyTarget: `preview:${target}`,
      bookLayoutFeedbackTarget: target,
      bookLayoutFeedbackText: isStale ? "正在预览上次保存的书稿…" : "正在生成预览…",
      bookLayoutFeedbackError: false,
      error: "",
    });
    try {
      if (!(await this.ensureBookDesignSavedForEditorialCopy())) {
        this.setData({ bookLayoutFeedbackText: this.data.error || "请先保存成书信息", bookLayoutFeedbackError: true });
        return;
      }
      const filePath = await downloadMemoirBookOutputExport(
        this.data.projectId,
        "pdf",
        this.bookOutput.latest_version.id,
        "standard",
        this.data.bookExportShowSourceAppendix,
        adjusting ? { custom_layout: pendingLayout } : template === "current" ? undefined : { template },
        (phase) => this.setData({ bookLayoutFeedbackText: {
          checking: "正在检查预览…", generating: "正在生成预览…", downloading: "正在下载预览…", opening: "正在打开预览…",
        }[phase] }),
      );
      await openBookPdf(filePath);
      this.setData({
        bookLayoutFeedbackText: isStale ? "PDF 已打开；章节更新尚未收录" : "PDF 已打开",
      });
    } catch (error) {
      if (routeAuthError(error, "login")) return;
      const message = error instanceof Error ? error.message : "版式预览失败";
      this.setData({ error: message, bookLayoutFeedbackText: message, bookLayoutFeedbackError: true });
    } finally {
      this.setData({ bookLayoutBusyTarget: "" });
    }
  },

  async selectBookLayout(event: DatasetEvent) {
    if (datasetString(event, "template") === "current") return;
    const adjusting = datasetString(event, "template") === "adjust";
    const template = adjusting ? this.data.bookLayoutTemplate : datasetChoice(event, "template", BOOK_LAYOUT_TEMPLATES, "heritage_classic");
    const pendingLayout = { ...this.data.bookCustomLayout };
    const option = BOOK_LAYOUT_OPTIONS.find((candidate) => candidate.value === template);
    const coverStyle = adjusting ? undefined : option?.coverStyle;
    if (!this.data.projectId || this.data.bookLayoutBusyTarget || this.data.bookExportBusyTarget || this.data.bookTitleSaving) return;
    if (!this.bookOutput?.latest_version) {
      this.setData({ error: "请先生成书稿" });
      return;
    }
    if (!this.data.bookManuscriptAvailable) {
      this.setData({ error: "请先更新书稿，再选择版式" });
      return;
    }
    if (this.data.bookViewingHistoricalVersion) {
      this.setData({ error: "请切回最新版后再修改版式" });
      return;
    }
    if (adjusting && !this.data.bookLayoutCustomChanged) return;
    if (!adjusting && (
      template === this.data.bookLayoutTemplate
      && (!coverStyle || this.data.bookCoverStyle === coverStyle)
    )) return;
    const target = adjusting ? "adjust" : template;
    this.setData({
      bookLayoutBusyTarget: `save:${target}`,
      bookLayoutFeedbackTarget: target,
      bookLayoutFeedbackText: "正在应用版式…",
      bookLayoutFeedbackError: false,
      error: "",
    });
    try {
      if (!(await this.ensureBookDesignSavedForEditorialCopy())) {
        this.setData({ bookLayoutFeedbackText: this.data.error || "请先保存书名与信息", bookLayoutFeedbackError: true });
        return;
      }
      const output = await updateMemoirBookOutput(this.data.projectId, {
        ...(adjusting ? { custom_layout: pendingLayout } : { manuscript_template: template }),
        ...(coverStyle ? { cover_style: coverStyle } : {}),
      });
      this.applyBookExportData(this.bookPlan, output);
      this.setData({ bookLayoutFeedbackText: "版式已应用" });
      wx.showToast({ title: "书稿版式已保存", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) return;
      const message = error instanceof Error ? error.message : "保存书稿版式失败";
      this.setData({ error: message, bookLayoutFeedbackText: message, bookLayoutFeedbackError: true });
    } finally {
      this.setData({ bookLayoutBusyTarget: "" });
    }
  },

  applyUploadedCoverPreview(version: MemoirBookOutputVersion | null) {
    const coverImage = version?.manuscript?.render_settings.cover_image;
    const assetId = coverImage?.image_asset_id || "";
    const path = assetId === this.data.bookUploadedCoverAssetId ? this.data.bookUploadedCoverPath : "";
    this.setData({
      bookUploadedCoverAssetId: assetId,
      bookUploadedCoverPath: path,
      bookCoverImageLayout: coverImage?.display_size === "cover_full_bleed" ? "cover_full_bleed" : "cover_frame",
    });
    if (assetId && !path) void this.loadUploadedCoverPreview();
  },

  async loadUploadedCoverPreview() {
    const assetId = this.data.bookUploadedCoverAssetId;
    const projectId = this.data.projectId;
    try {
      const path = await downloadMemoirImageAssetFile(projectId, assetId, true);
      if (projectId === this.data.projectId && assetId === this.data.bookUploadedCoverAssetId) {
        this.setData({ bookUploadedCoverPath: path });
      }
    } catch (error) {
      if (routeAuthError(error, "login")) return;
      if (assetId === this.data.bookUploadedCoverAssetId) this.setData({ bookLayoutFeedbackTarget: "cover", bookLayoutFeedbackText: "封面预览加载失败，可预览 PDF 或重新打开页面", bookLayoutFeedbackError: true });
    }
  },

  async changeBookCoverImage(event: DatasetEvent) {
    if (!this.coverChangeAllowed()) return;
    const action = datasetString(event, "action");
    const restoring = action === "restore";
    const editing = action === "edit";
    if ((restoring || editing) && !this.data.bookUploadedCoverAssetId) return;
    const layout = this.data.bookCoverImageLayout;
    this.setData({ bookLayoutBusyTarget: "save:cover", bookLayoutFeedbackTarget: "cover", bookLayoutFeedbackText: "", bookLayoutFeedbackError: false });
    try {
      let filePath: string | null = null;
      if (editing) {
        this.setData({ bookLayoutFeedbackText: "正在打开照片…" });
        const source = await downloadMemoirImageAssetFile(this.data.projectId, this.data.bookUploadedCoverAssetId, false);
        filePath = layout === "cover_full_bleed"
          ? await cropImageFile(source, FULL_BLEED_COVER_CROP_SCALE)
          : await editImageFile(source);
        this.setData({ bookLayoutFeedbackText: "" });
      } else if (!restoring) {
        const chosen = await chooseImageFile();
        // Cancelling the crop page keeps the chosen photo instead of dropping it.
        filePath = chosen && layout === "cover_full_bleed"
          ? (await cropImageFile(chosen, FULL_BLEED_COVER_CROP_SCALE)) || chosen
          : chosen;
      }
      if (!restoring && !filePath) return;
      if (!(await this.ensureBookDesignSavedForEditorialCopy())) throw new Error(this.data.error || "请先保存书的信息");
      this.setData({ bookLayoutFeedbackText: restoring ? "正在恢复版式封面…" : "正在上传封面…" });
      if (restoring) {
        const { asset, placement } = await this.currentCoverPlacement();
        await deleteMemoirBookImagePlacement(this.data.projectId, asset.id, placement.id);
      } else {
        const asset = await uploadMemoirImageAsset(this.data.projectId, filePath!);
        await createMemoirBookImagePlacement(this.data.projectId, asset.id, { placement_type: "cover", display_size: layout });
      }
      const output = await getMemoirBookOutput(this.data.projectId);
      this.applyBookExportData(this.bookPlan, output, true);
      if (restoring && this.data.bookUploadedCoverAssetId) throw new Error("封面已在其他地方更新，请重新选择");
      this.setData({ bookLayoutFeedbackText: restoring ? "已恢复版式封面" : editing ? "封面照片已更新" : "封面图片已保存" });
    } catch (error) {
      if (routeAuthError(error, "login")) return;
      this.setData({ bookLayoutFeedbackText: error instanceof Error ? error.message : "保存封面失败", bookLayoutFeedbackError: true });
    } finally { this.setData({ bookLayoutBusyTarget: "" }); }
  },

  async changeBookCoverImageLayout(event: DatasetEvent) {
    const layout = datasetChoice(event, "layout", COVER_IMAGE_LAYOUTS, "cover_frame");
    if (!this.coverChangeAllowed() || !this.data.bookUploadedCoverAssetId || layout === this.data.bookCoverImageLayout) return;
    this.setData({ bookLayoutBusyTarget: "save:cover", bookLayoutFeedbackTarget: "cover", bookLayoutFeedbackText: "", bookLayoutFeedbackError: false });
    try {
      if (!(await this.ensureBookDesignSavedForEditorialCopy())) throw new Error(this.data.error || "请先保存书的信息");
      const { asset, placement } = await this.currentCoverPlacement();
      await updateMemoirBookImagePlacement(this.data.projectId, asset.id, placement.id, { display_size: layout });
      const output = await getMemoirBookOutput(this.data.projectId);
      this.applyBookExportData(this.bookPlan, output, true);
      this.setData({ bookLayoutFeedbackText: layout === "cover_full_bleed" ? "已改为照片铺满封面，可点调整取景" : "已改为照片放进相框" });
    } catch (error) {
      if (routeAuthError(error, "login")) return;
      this.setData({ bookLayoutFeedbackText: error instanceof Error ? error.message : "保存封面失败", bookLayoutFeedbackError: true });
    } finally { this.setData({ bookLayoutBusyTarget: "" }); }
  },

  coverChangeAllowed(): boolean {
    return Boolean(this.data.projectId) && !this.data.bookLayoutBusyTarget && !this.data.bookExportBusyTarget
      && !this.data.bookTitleSaving && !this.data.bookViewingHistoricalVersion && this.data.bookManuscriptAvailable;
  },

  async currentCoverPlacement() {
    const assets = await listMemoirImageAssets(this.data.projectId);
    const asset = assets.items.find((item) => item.id === this.data.bookUploadedCoverAssetId);
    const placement = asset?.placements.find((item) => item.enabled && item.placement_type === "cover");
    if (!asset || !placement) throw new Error("封面图片信息已变化，请重新打开页面后再试");
    return { asset, placement };
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
    returnToBook();
  },

  openImageAssetsPage() {
    if (!this.data.projectId) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/image-assets/index", { projectId: this.data.projectId }),
    });
  },

  openSection(event: DatasetEvent) {
    const section = datasetString(event, "section") as BookExportSection;
    if (!section || !Object.prototype.hasOwnProperty.call(SECTION_TITLES, section)) return;
    if (!this.data.bookContent) return;
    const permitted = this.data.pagePurpose === "files"
      ? section === "export"
      : this.data.pagePurpose === "settings"
        ? section !== "export" && section !== "advanced"
        : section === "advanced";
    if (!permitted) return;
    this.setData({ activeSection: section, error: "" }, () => {
      wx.pageScrollTo({ scrollTop: 0, duration: 0 });
    });
    setPageNavigationTitle(SECTION_TITLES[section]);
    if (section === "editorial") {
      void this.refreshBookEditorialCopyProgress();
    }
  },

  async closeSection() {
    if (this.data.pagePurpose === "regenerate") {
      wx.navigateBack({ fail: () => this.openBookHome() });
      return;
    }
    if (this.data.activeSection === "title" || this.data.activeSection === "editorial") {
      this.clearBookDesignAutosaveTimer();
      if (bookDesignHasChanges(this.data)) {
        await this.autosaveBookDesignSettings();
        if (bookDesignHasChanges(this.data)) return;
      }
    }
    if (this.data.activeSection === "history" && this.bookOutput) {
      this.applyBookExportData(this.bookPlan, this.bookOutput);
    }
    this.clearBookEditorialCopyPoll();
    const scrollTop = this.overviewScrollTop;
    this.setData({ activeSection: "", error: "" }, () => {
      wx.pageScrollTo({ scrollTop, duration: 0 });
    });
    setPageNavigationTitle(this.data.pagePurpose === "settings" ? "书的设置" : "导出文件");
  },

  async downloadBookOutputExport(event: DatasetEvent) {
    const format = datasetChoice(event, "format", BOOK_EXPORT_FORMATS, "pdf");
    const requestedLayout = datasetChoice(event, "layout", BOOK_EXPORT_LAYOUTS, "standard");
    const layout: MemoirBookOutputExportLayout = format === "pdf" && requestedLayout === "large_print"
      ? "large_print"
      : "standard";
    const target = `book-export:${format}:${layout}`;
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
    this.setData({ bookExportBusyTarget: target, bookExportBusyText: "正在检查…", error: "", exportNotice: "" });
    try {
      const filePath = await downloadMemoirBookOutputExport(
        this.data.projectId,
        format,
        this.data.bookSelectedVersionId,
        layout,
        format === "pdf" ? this.data.bookExportShowSourceAppendix : true,
        undefined,
        (phase) => this.setData({ bookExportBusyText: {
          checking: "正在检查…", generating: "正在生成…", downloading: "正在下载…", opening: "正在打开…",
        }[phase] }),
      );
      if (format === "pdf") {
        await openBookPdf(filePath);
      } else {
        await shareLocalFileMessage({ filePath, fileName: filePath.slice(filePath.lastIndexOf("/") + 1) });
      }
      wx.showToast({ title: format === "pdf" ? "PDF 已打开" : "已打开微信分享", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      if (isShareFileMessageCancelError(error)) {
        this.setData({ exportNotice: "已取消分享，可以重新导出文本。" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "导出失败" });
    } finally {
      this.setData({ bookExportBusyTarget: "" });
    }
  },
});
