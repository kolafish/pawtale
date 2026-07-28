import type {
  MemoirChapterCompletionSummary,
  MemoirBookCompletionOverview,
} from "../../domain/chapter-completion";
import type { MemoirCompletionIssue } from "../../domain/memoir-completion-issue";
import { aiTaskProgressPercent, type AITaskProgressMode } from "../../services/ai-task-progress";
import { definePage } from "../../services/page-definition";
import { PollingTimer } from "../../services/polling-timer";
import {
  cancelMemoirBookReviewJob,
  cancelMemoirBookCompletionJob,
  enqueueMemoirBookCompletionJob,
  enqueueMemoirBookReviewJob,
  enqueueMemoirChapterPreparationJob,
  getMemoirBookCompletionJob,
  getMemoirBookCompletionOverview,
  getMemoirBookOutput,
  getMemoirBookReview,
  getMemoirBookReviewJob,
  listMemoirCompletionIssues,
  type MemoirBookReview,
  type MemoirBookReviewJob,
  type MemoirBookCompletionJob,
} from "../../services/memoir-voice-api";
import { routeAuthError } from "../../services/navigation";
import { optionalApiResource } from "../../services/request";
import {
  datasetString,
  pageUrl,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
} from "../../services/page-input";


const POLL_INTERVAL_MS = 1500;

interface ChapterView extends MemoirChapterCompletionSummary {
  stateText: string;
  actionText: string;
  chapterClass: string;
  disabled: boolean;
}

interface BookCompletionData {
  projectId: string;
  projectTitle: string;
  loading: boolean;
  error: string;
  headline: string;
  detail: string;
  progressText: string;
  chapters: ChapterView[];
  allChaptersConfirmed: boolean;
  bulkCheckVisible: boolean;
  bulkCheckActionText: string;
  bulkCheckTitle: string;
  bulkCheckDetail: string;
  bulkCheckJob: MemoirBookCompletionJob | null;
  bulkCheckProgressVisible: boolean;
  bulkCheckProgressTitle: string;
  bulkCheckProgressDetail: string;
  bulkCheckProgressMode: AITaskProgressMode;
  bulkCheckProgressPercent: number;
  bulkCheckProgressClass: string;
  bulkCheckCanCancel: boolean;
  bulkCheckCancelling: boolean;
  preparingChapterId: string;
  recheckingAll: boolean;
  bookReview: MemoirBookReview | null;
  requiredBookIssues: MemoirCompletionIssue[];
  optionalBookIssues: MemoirCompletionIssue[];
  bookReviewJob: MemoirBookReviewJob | null;
  bookReviewProgressVisible: boolean;
  bookReviewProgressTitle: string;
  bookReviewProgressDetail: string;
  bookReviewProgressMode: AITaskProgressMode;
  bookReviewProgressPercent: number;
  bookReviewProgressClass: string;
  bookReviewCanCancel: boolean;
  bookReviewCancelling: boolean;
  checkingBook: boolean;
  bookOutputAvailable: boolean;
}

interface ChapterDataset {
  chapterId?: string;
}

type ChapterTapEvent = PageDatasetEvent<ChapterDataset>;

export function bookReviewViewPatch(
  review: MemoirBookReview | null,
  issues: MemoirCompletionIssue[],
): Pick<
  BookCompletionData,
  "bookReview" | "requiredBookIssues" | "optionalBookIssues"
> {
  const current = review?.status === "ready"
    ? issues.filter((issue) => (
      issue.scope === "book"
      && issue.input_signature === review.input_signature
      && issue.status !== "closed"
    ))
    : [];
  return {
    bookReview: review,
    requiredBookIssues: current.filter(
      (issue) => issue.channel === "manual_review",
    ),
    optionalBookIssues: current.filter(
      (issue) => issue.channel === "interview",
    ),
  };
}

function bookCompletionJobActive(job: MemoirBookCompletionJob): boolean {
  return job.status === "pending" || job.status === "running";
}

function bookReviewJobActive(job: MemoirBookReviewJob): boolean {
  return job.status === "pending" || job.status === "running";
}

export function bookReviewJobView(
  job: MemoirBookReviewJob | null,
): Pick<
  BookCompletionData,
  | "bookReviewProgressVisible"
  | "bookReviewProgressTitle"
  | "bookReviewProgressDetail"
  | "bookReviewProgressMode"
  | "bookReviewProgressPercent"
  | "bookReviewProgressClass"
  | "bookReviewCanCancel"
> {
  if (!job || job.status === "idle") {
    return {
      bookReviewProgressVisible: false,
      bookReviewProgressTitle: "",
      bookReviewProgressDetail: "",
      bookReviewProgressMode: "hidden",
      bookReviewProgressPercent: 0,
      bookReviewProgressClass: "",
      bookReviewCanCancel: false,
    };
  }
  if (bookReviewJobActive(job)) {
    const preparing = job.stage === "preparing_chapters";
    const stageTitle = {
      queued: "全书检查已提交",
      preparing_chapters: job.current_chapter_title
        ? `正在准备《${job.current_chapter_title}》`
        : "正在准备章节摘要",
      reviewing: "正在检查全书",
      saving: "正在保存检查结果",
    }[job.stage] || "正在检查全书";
    const detail = preparing && job.total_chapter_count
      ? `已准备 ${job.processed_chapter_count}/${job.total_chapter_count} 章，离开页面后任务会继续`
      : (job.stage === "saving"
        ? "检查已经完成，正在保存结果"
        : "任务正在后台执行，离开页面后进度会继续保存");
    return {
      bookReviewProgressVisible: true,
      bookReviewProgressTitle: stageTitle,
      bookReviewProgressDetail: detail,
      bookReviewProgressMode: preparing && job.total_chapter_count
        ? "determinate"
        : "indeterminate",
      bookReviewProgressPercent: preparing
        ? aiTaskProgressPercent(
          job.processed_chapter_count,
          job.total_chapter_count,
        )
        : 0,
      bookReviewProgressClass: "active",
      bookReviewCanCancel: job.can_cancel,
    };
  }
  if (job.status === "failed") {
    return {
      bookReviewProgressVisible: true,
      bookReviewProgressTitle: "全书检查未完成",
      bookReviewProgressDetail: job.error_message || "可以重新检查当前版本。",
      bookReviewProgressMode: "hidden",
      bookReviewProgressPercent: 0,
      bookReviewProgressClass: "failed",
      bookReviewCanCancel: false,
    };
  }
  if (job.status === "cancelled") {
    return {
      bookReviewProgressVisible: true,
      bookReviewProgressTitle: "全书检查已停止",
      bookReviewProgressDetail: "已经保存的章节摘要仍会保留，可以重新检查。",
      bookReviewProgressMode: "hidden",
      bookReviewProgressPercent: 0,
      bookReviewProgressClass: "cancelled",
      bookReviewCanCancel: false,
    };
  }
  return {
    bookReviewProgressVisible: true,
    bookReviewProgressTitle: "全书检查已完成",
    bookReviewProgressDetail: "检查结果已经保存。",
    bookReviewProgressMode: "hidden",
    bookReviewProgressPercent: 0,
    bookReviewProgressClass: "succeeded",
    bookReviewCanCancel: false,
  };
}

export function bookCompletionJobView(
  job: MemoirBookCompletionJob | null,
): Pick<
  BookCompletionData,
  | "bulkCheckProgressVisible"
  | "bulkCheckProgressTitle"
  | "bulkCheckProgressDetail"
  | "bulkCheckProgressMode"
  | "bulkCheckProgressPercent"
  | "bulkCheckProgressClass"
  | "bulkCheckCanCancel"
> {
  if (!job || job.status === "idle") {
    return {
      bulkCheckProgressVisible: false,
      bulkCheckProgressTitle: "",
      bulkCheckProgressDetail: "",
      bulkCheckProgressMode: "hidden",
      bulkCheckProgressPercent: 0,
      bulkCheckProgressClass: "",
      bulkCheckCanCancel: false,
    };
  }
  const processedText = `已处理 ${job.processed_chapter_count}/${job.total_chapter_count} 章`;
  if (bookCompletionJobActive(job)) {
    const failureText = job.failed_chapter_count
      ? ` · ${job.failed_chapter_count} 章未完成`
      : "";
    return {
      bulkCheckProgressVisible: true,
      bulkCheckProgressTitle: job.current_chapter_title
        ? `正在检查《${job.current_chapter_title}》`
        : "正在准备全书检查",
      bulkCheckProgressDetail: `${processedText}${failureText}，每章完成后都会保存`,
      bulkCheckProgressMode: job.total_chapter_count ? "determinate" : "indeterminate",
      bulkCheckProgressPercent: aiTaskProgressPercent(
        job.processed_chapter_count,
        job.total_chapter_count,
      ),
      bulkCheckProgressClass: "active",
      bulkCheckCanCancel: job.can_cancel,
    };
  }
  if (job.status === "failed") {
    return {
      bulkCheckProgressVisible: true,
      bulkCheckProgressTitle: `${job.failed_chapter_count} 章检查未完成`,
      bulkCheckProgressDetail: `${processedText}，已完成结果已保留，可以重新检查失败章节。`,
      bulkCheckProgressMode: "hidden",
      bulkCheckProgressPercent: 0,
      bulkCheckProgressClass: "failed",
      bulkCheckCanCancel: false,
    };
  }
  if (job.status === "cancelled") {
    return {
      bulkCheckProgressVisible: true,
      bulkCheckProgressTitle: "全书检查已停止",
      bulkCheckProgressDetail: `${processedText}，已完成结果已保留。`,
      bulkCheckProgressMode: "hidden",
      bulkCheckProgressPercent: 0,
      bulkCheckProgressClass: "cancelled",
      bulkCheckCanCancel: false,
    };
  }
  return {
    bulkCheckProgressVisible: true,
    bulkCheckProgressTitle: "全书检查已完成",
    bulkCheckProgressDetail: `${processedText}，请查看各章当前最重要的一步。`,
    bulkCheckProgressMode: "hidden",
    bulkCheckProgressPercent: 0,
    bulkCheckProgressClass: "succeeded",
    bulkCheckCanCancel: false,
  };
}

function chapterView(
  item: MemoirChapterCompletionSummary,
  preparingChapterId: string,
  recheckingAll = false,
): ChapterView {
  const stateText = {
    not_started: "待检查",
    preparing: "正在检查",
    needs_input: "需要确认",
    draft_ready: "等待通读",
    confirmed: "已经确认",
    failed: "检查未完成",
  }[item.state];
  const busy = (
    recheckingAll
    || preparingChapterId === item.chapter_id
    || item.state === "preparing"
  );
  const progressAvailable = item.state === "preparing";
  return {
    ...item,
    stateText: busy ? "正在检查" : stateText,
    actionText: progressAvailable
      ? "查看进度"
      : (busy
      ? "正在检查"
      : (item.state === "confirmed"
        ? "查看正文"
        : (item.next_action.action_text || "查看本章"))),
    chapterClass: `chapter-item ${item.state}`,
    disabled: busy && !progressAvailable,
  };
}

export function bookCompletionViewPatch(
  overview: MemoirBookCompletionOverview,
  preparingChapterId = "",
  recheckingAll = false,
): Pick<
  BookCompletionData,
  | "headline"
  | "detail"
  | "progressText"
  | "chapters"
  | "allChaptersConfirmed"
  | "bulkCheckVisible"
  | "bulkCheckActionText"
  | "bulkCheckTitle"
  | "bulkCheckDetail"
> {
  const allChaptersConfirmed = (
    overview.chapters.length > 0
    && overview.chapters.every((item) => item.state === "confirmed")
  );
  const hasCheckableChapters = overview.chapters.some(
    (item) => item.next_action.kind === "prepare",
  );
  return {
    headline: overview.headline,
    detail: overview.detail,
    progressText: overview.total_chapters
      ? `${overview.completed_chapters} / ${overview.total_chapters} 章正文已确认`
      : "还没有章节",
    chapters: overview.chapters.map(
      (item) => chapterView(item, preparingChapterId, recheckingAll),
    ),
    allChaptersConfirmed,
    bulkCheckVisible: recheckingAll || hasCheckableChapters,
    bulkCheckActionText: "检查待检查章节",
    bulkCheckTitle: "先检查是否真的需要补充",
    bulkCheckDetail: "系统会逐章判断，只展示每章当前最重要的一步。",
  };
}

definePage<BookCompletionData>()({
  overviewPoller: new PollingTimer(POLL_INTERVAL_MS),
  pageVisible: false,

  data: {
    projectId: "",
    projectTitle: "",
    loading: true,
    error: "",
    headline: "逐章整理这本书",
    detail: "",
    progressText: "",
    chapters: [],
    allChaptersConfirmed: false,
    bulkCheckVisible: false,
    bulkCheckActionText: "",
    bulkCheckTitle: "",
    bulkCheckDetail: "",
    bulkCheckJob: null,
    bulkCheckProgressVisible: false,
    bulkCheckProgressTitle: "",
    bulkCheckProgressDetail: "",
    bulkCheckProgressMode: "hidden",
    bulkCheckProgressPercent: 0,
    bulkCheckProgressClass: "",
    bulkCheckCanCancel: false,
    bulkCheckCancelling: false,
    preparingChapterId: "",
    recheckingAll: false,
    bookReview: null,
    requiredBookIssues: [],
    optionalBookIssues: [],
    bookReviewJob: null,
    bookReviewProgressVisible: false,
    bookReviewProgressTitle: "",
    bookReviewProgressDetail: "",
    bookReviewProgressMode: "hidden",
    bookReviewProgressPercent: 0,
    bookReviewProgressClass: "",
    bookReviewCanCancel: false,
    bookReviewCancelling: false,
    checkingBook: false,
    bookOutputAvailable: false,
  },

  onLoad(options: PageQuery) {
    this.setData({
      projectId: queryParam(options, "projectId"),
      projectTitle: queryParam(options, "projectTitle"),
    });
  },

  async onShow() {
    this.pageVisible = true;
    await this.loadOverview();
  },

  onHide() {
    this.pageVisible = false;
    this.stopPolling();
  },

  onUnload() {
    this.pageVisible = false;
    this.stopPolling();
  },

  async onPullDownRefresh() {
    await this.loadOverview();
    wx.stopPullDownRefresh();
  },

  openBookHome() {
    wx.switchTab({ url: "/pages/book/index" });
  },

  openBookSettings() {
    if (!this.data.projectId) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/book-export/index", {
        projectId: this.data.projectId,
        action: "preview",
        settings: "book",
      }),
    });
  },

  async loadOverview(silent = false) {
    if (!this.data.projectId) {
      this.setData({ loading: false, error: "没有找到这本书" });
      return;
    }
    if (!silent) {
      this.setData({ loading: true, error: "" });
    }
    try {
      const [bulkCheckJob, overview] = await Promise.all([
        getMemoirBookCompletionJob(this.data.projectId),
        getMemoirBookCompletionOverview(this.data.projectId),
      ]);
      const initialView = bookCompletionViewPatch(
        overview,
        this.data.preparingChapterId,
        false,
      );
      const batchActive = (
        !initialView.allChaptersConfirmed
        && bookCompletionJobActive(bulkCheckJob)
      );
      const view = bookCompletionViewPatch(
        overview,
        this.data.preparingChapterId,
        batchActive,
      );
      const [
        bookReview,
        bookReviewJob,
        issueCollection,
        bookOutput,
      ] = view.allChaptersConfirmed
        ? await Promise.all([
          getMemoirBookReview(this.data.projectId),
          getMemoirBookReviewJob(this.data.projectId),
          listMemoirCompletionIssues(this.data.projectId),
          optionalApiResource(getMemoirBookOutput(this.data.projectId)),
        ])
        : [null, null, { issues: [] }, null];
      const reviewJobActive = Boolean(
        bookReviewJob && bookReviewJobActive(bookReviewJob),
      );
      const finishedBookReview = (
        this.data.checkingBook
        && !reviewJobActive
        && bookReviewJob?.status !== "idle"
      );
      const finishedChapterId = this.data.preparingChapterId;
      const finishedWholeBook = (
        this.data.recheckingAll
        && !batchActive
        && bulkCheckJob.status !== "idle"
      );
      this.setData({
        ...view,
        ...bookReviewViewPatch(bookReview, issueCollection.issues),
        bookOutputAvailable: Boolean(bookOutput),
        ...bookReviewJobView(
          bookReviewJob?.status === "succeeded" && bookReview
            ? null
            : bookReviewJob,
        ),
        bookReviewJob,
        checkingBook: reviewJobActive,
        ...bookCompletionJobView(
          view.allChaptersConfirmed ? null : bulkCheckJob,
        ),
        bulkCheckJob: (
          view.allChaptersConfirmed || bulkCheckJob.status === "idle"
            ? null
            : bulkCheckJob
        ),
        recheckingAll: batchActive,
        loading: false,
        error: "",
      });
      if (
        batchActive
        || reviewJobActive
        || overview.chapters.some((item) => item.state === "preparing")
      ) {
        this.schedulePolling();
      } else {
        this.stopPolling();
        if (finishedChapterId || finishedWholeBook) {
          this.setData({
            preparingChapterId: "",
            chapters: overview.chapters.map((item) => chapterView(item, "")),
          });
          const checkedChapters = overview.chapters.filter(
            (item) => (
              finishedWholeBook
              || item.chapter_id === finishedChapterId
            ),
          );
          const toastTitle = finishedWholeBook && bulkCheckJob.status === "failed"
            ? "有章节检查未完成"
            : (finishedWholeBook && bulkCheckJob.status === "cancelled"
              ? "已停止全书检查"
              : (checkedChapters.some((item) => item.state === "needs_input")
                ? "发现需要补充的内容"
                : (checkedChapters.every((item) => item.state === "confirmed")
                  ? "暂未发现待补内容"
                  : "检查完成，请查看结果")));
          wx.showToast({ title: toastTitle, icon: "none" });
        }
        if (finishedBookReview) {
          const toastTitle = bookReviewJob?.status === "succeeded"
            ? "全书检查完成"
            : (bookReviewJob?.status === "cancelled"
              ? "已停止全书检查"
              : "全书检查未完成");
          wx.showToast({
            title: toastTitle,
            icon: bookReviewJob?.status === "succeeded" ? "success" : "none",
          });
        }
      }
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : "全书检查情况加载失败",
      });
      if (
        this.data.preparingChapterId
        || this.data.recheckingAll
        || this.data.checkingBook
      ) {
        this.schedulePolling();
      }
    }
  },

  async tapChapter(event: ChapterTapEvent) {
    const chapterId = datasetString(event, "chapterId");
    const chapter = this.data.chapters.find((item) => item.chapter_id === chapterId);
    if (
      !chapter
      || chapter.disabled
      || (
        (this.data.preparingChapterId || this.data.recheckingAll)
        && chapter.state !== "preparing"
      )
    ) {
      return;
    }
    if (chapter.next_action.kind === "prepare") {
      await this.prepareChapter(chapter.chapter_id);
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/chapter-workspace/index", {
        projectId: this.data.projectId,
        chapterId: chapter.chapter_id,
      }),
    });
  },

  async prepareChapter(chapterId: string) {
    if (this.data.recheckingAll || this.data.preparingChapterId) {
      return;
    }
    this.setData({
      preparingChapterId: chapterId,
      error: "",
      chapters: this.data.chapters.map((item) => chapterView(item, chapterId)),
    });
    try {
      await enqueueMemoirChapterPreparationJob(
        this.data.projectId,
        chapterId,
        "prepare",
      );
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        preparingChapterId: "",
        error: error instanceof Error ? error.message : "本章检查失败",
        chapters: this.data.chapters.map((item) => chapterView(item, "")),
      });
    }
  },

  async checkPendingChapters() {
    if (
      !this.data.bulkCheckVisible
      || this.data.allChaptersConfirmed
      || this.data.recheckingAll
      || this.data.bulkCheckCancelling
      || this.data.preparingChapterId
      || !this.data.chapters.length
    ) {
      return;
    }
    this.setData({
      recheckingAll: true,
      error: "",
      bulkCheckProgressVisible: true,
      bulkCheckProgressTitle: "正在提交章节检查",
      bulkCheckProgressDetail: "任务提交后可以离开页面，进度会继续保存",
      bulkCheckProgressMode: "indeterminate",
      bulkCheckProgressPercent: 0,
      bulkCheckProgressClass: "active",
      bulkCheckCanCancel: false,
      chapters: this.data.chapters.map((item) => chapterView(item, "", true)),
    });
    try {
      const retryJobId = this.data.bulkCheckJob?.can_retry
        ? this.data.bulkCheckJob.job_id
        : null;
      await enqueueMemoirBookCompletionJob(
        this.data.projectId,
        retryJobId,
      );
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      const message = error instanceof Error ? error.message : "章节检查没有完成";
      this.setData({ recheckingAll: false });
      await this.loadOverview(true);
      if (
        !this.data.bulkCheckJob
        || !bookCompletionJobActive(this.data.bulkCheckJob)
      ) {
        this.setData({ error: message });
      }
    }
  },

  async reviewBook() {
    if (
      !this.data.allChaptersConfirmed
      || this.data.checkingBook
    ) {
      return;
    }
    this.setData({
      checkingBook: true,
      error: "",
      bookReviewProgressVisible: true,
      bookReviewProgressTitle: "正在提交全书检查",
      bookReviewProgressDetail: "任务提交后可以离开页面，进度会继续保存",
      bookReviewProgressMode: "indeterminate",
      bookReviewProgressPercent: 0,
      bookReviewProgressClass: "active",
      bookReviewCanCancel: false,
    });
    try {
      await enqueueMemoirBookReviewJob(
        this.data.projectId,
        Boolean(this.data.bookReview),
      );
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      const message = error instanceof Error ? error.message : "全书检查没有完成";
      this.setData({ checkingBook: false });
      await this.loadOverview(true);
      if (
        !this.data.bookReviewJob
        || !bookReviewJobActive(this.data.bookReviewJob)
      ) {
        this.setData({ error: message });
      }
    }
  },

  async cancelBookReview() {
    const job = this.data.bookReviewJob;
    if (
      !job?.job_id
      || !bookReviewJobActive(job)
      || this.data.bookReviewCancelling
    ) {
      return;
    }
    this.setData({
      bookReviewCancelling: true,
      bookReviewCanCancel: false,
      error: "",
    });
    try {
      await cancelMemoirBookReviewJob(
        this.data.projectId,
        job.job_id,
      );
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "停止全书检查失败",
      });
    } finally {
      this.setData({ bookReviewCancelling: false });
    }
  },

  async cancelBulkCheck() {
    const job = this.data.bulkCheckJob;
    if (
      !job?.job_id
      || !bookCompletionJobActive(job)
      || this.data.bulkCheckCancelling
    ) {
      return;
    }
    this.setData({
      bulkCheckCancelling: true,
      bulkCheckCanCancel: false,
      error: "",
    });
    try {
      await cancelMemoirBookCompletionJob(
        this.data.projectId,
        job.job_id,
      );
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "停止全书检查失败",
      });
    } finally {
      this.setData({ bulkCheckCancelling: false });
    }
  },

  schedulePolling() {
    if (!this.pageVisible || this.overviewPoller.pending) {
      return;
    }
    this.overviewPoller.schedule(() => {
      if (this.pageVisible) {
        void this.loadOverview(true);
      }
    });
  },

  stopPolling() {
    this.overviewPoller.cancel();
  },
});
