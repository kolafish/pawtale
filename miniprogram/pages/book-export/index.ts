import type {
  MemoirBookOutput,
  MemoirBookOutputExportFormat,
  MemoirBookOutputExportLayout,
  MemoirBookPlan,
} from "../../domain/voice-chat";
import {
  downloadMemoirBookOutputExport,
  getMemoirBookOutput,
  getMemoirBookPlan,
} from "../../services/memoir-voice-api";
import { ApiError } from "../../services/request";
import {
  BOOK_EXPORT_LAYOUT_OPTIONS,
  bookOutputData,
  buildChapterViews,
  planStage,
  stageText,
  trimText,
  type BookData,
} from "../book/book-view-model";

interface DatasetEvent {
  currentTarget: {
    dataset: {
      format?: MemoirBookOutputExportFormat;
      layout?: MemoirBookOutputExportLayout;
    };
  };
}

interface PickerEvent extends DatasetEvent {
  detail: {
    value: string | number;
  };
}

interface SwitchEvent extends DatasetEvent {
  detail: {
    value: boolean;
  };
}

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
  | "bookVersionText"
  | "bookVersionOptions"
  | "bookSelectedVersionId"
  | "bookSelectedVersionIndex"
  | "bookViewingHistoricalVersion"
  | "bookVersionNotice"
  | "bookStaleNotice"
  | "bookActionText"
  | "bookExportBusyTarget"
  | "bookExportLayout"
  | "bookExportLayoutIndex"
  | "bookExportLayoutLabel"
  | "bookExportLayoutDetailText"
  | "bookExportLayoutOptions"
  | "bookExportShowSourceAppendix"
> & {
  requestedAction: string;
  exportNotice: string;
};

function isLoginError(error: unknown): boolean {
  return error instanceof ApiError && [401, 403].includes(error.statusCode);
}

function exportNotice(action: string): string {
  if (action === "reading_pdf") {
    return "阅读版适合发给家人在线阅读。";
  }
  if (action === "print_pdf") {
    return "打印版字号更大，适合装订保存。";
  }
  return "可以先预览全书，再导出阅读版或打印版。";
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

Page<BookExportData, {
  onLoad(query?: Record<string, string | undefined>): Promise<void>;
  loadBookExportPage(projectId: string): Promise<void>;
  loadBookOutput(projectId: string): Promise<MemoirBookOutput | null>;
  applyBookExportData(plan: MemoirBookPlan | null, output: MemoirBookOutput | null): void;
  changeBookOutputVersion(event: PickerEvent): void;
  changeBookExportLayout(event: PickerEvent): void;
  toggleBookExportSourceAppendix(event: SwitchEvent): void;
  toggleBookPreviewChapters(): void;
  downloadBookOutputExport(event: DatasetEvent): Promise<void>;
}>({
  data: {
    loading: false,
    error: "",
    projectId: "",
    plan: null,
    planTitle: "全书导出",
    stage: "empty",
    stageText: "尚未整理",
    chapters: [],
    totalChapterCount: 0,
    allChaptersConfirmed: false,
    bookPreviewChaptersExpanded: true,
    bookExportBusyTarget: "",
    bookExportLayout: "standard",
    bookExportLayoutIndex: 0,
    bookExportLayoutLabel: BOOK_EXPORT_LAYOUT_OPTIONS[0].label,
    bookExportLayoutDetailText: BOOK_EXPORT_LAYOUT_OPTIONS[0].detailText,
    bookExportLayoutOptions: BOOK_EXPORT_LAYOUT_OPTIONS,
    bookExportShowSourceAppendix: true,
    requestedAction: "preview",
    exportNotice: exportNotice("preview"),
    ...bookOutputData(null),
  },

  async onLoad(query = {}) {
    const projectId = String(query.projectId || "");
    const action = String(query.action || "preview");
    this.setData({
      projectId,
      requestedAction: action,
      exportNotice: exportNotice(action),
    });
    if (!projectId) {
      this.setData({ error: "缺少回忆录项目信息" });
      return;
    }
    await this.loadBookExportPage(projectId);
  },

  async loadBookExportPage(projectId) {
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
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "全书导出页加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadBookOutput(projectId) {
    try {
      return await getMemoirBookOutput(projectId);
    } catch (error) {
      if (error instanceof ApiError && error.statusCode === 404) {
        return null;
      }
      throw error;
    }
  },

  applyBookExportData(plan, output) {
    const chapters = plan ? buildChapterViews(plan, {}, null, {}) : [];
    const stage = planStage(plan);
    const outputData = bookOutputData(output);
    this.setData({
      plan,
      planTitle: defaultTitle(plan, output),
      stage,
      stageText: stageText(stage),
      chapters,
      totalChapterCount: chapters.length,
      allChaptersConfirmed: Boolean(output?.latest_version),
      ...outputData,
      bookPreviewChaptersExpanded: true,
      bookExportBusyTarget: "",
    });
  },

  changeBookOutputVersion(event) {
    const index = Number(event.detail.value);
    const option = this.data.bookVersionOptions[index];
    if (!option || !this.data.bookOutput) {
      return;
    }
    this.setData({
      ...bookOutputData(this.data.bookOutput, option.id),
      bookPreviewChaptersExpanded: true,
    });
  },

  changeBookExportLayout(event) {
    const index = Number(event.detail.value);
    const option = this.data.bookExportLayoutOptions[index];
    if (!option) {
      return;
    }
    this.setData({
      bookExportLayout: option.value,
      bookExportLayoutIndex: index,
      bookExportLayoutLabel: option.label,
      bookExportLayoutDetailText: option.detailText,
    });
  },

  toggleBookExportSourceAppendix(event) {
    this.setData({ bookExportShowSourceAppendix: Boolean(event.detail.value) });
  },

  toggleBookPreviewChapters() {
    this.setData({ bookPreviewChaptersExpanded: !this.data.bookPreviewChaptersExpanded });
  },

  async downloadBookOutputExport(event) {
    const format: MemoirBookOutputExportFormat = event.currentTarget.dataset.format === "txt" ? "txt" : "pdf";
    const layout: MemoirBookOutputExportLayout = format === "pdf" && event.currentTarget.dataset.layout === "large_print"
      ? "large_print"
      : (format === "pdf" ? this.data.bookExportLayout : "standard");
    const target = `book-export:${format}:${layout}`;
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
      if (isLoginError(error)) {
        wx.reLaunch({ url: "/pages/login/index" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "全书导出失败" });
    } finally {
      this.setData({ bookExportBusyTarget: "" });
    }
  },
});
