import type {
  MemoirChapterCompletionSummary,
  MemoirBookCompletionOverview,
} from "../../domain/chapter-completion";
import { aiTaskProgressPercent, type AITaskProgressMode } from "../../services/ai-task-progress";
import { definePage } from "../../services/page-definition";
import {
  cancelMemoirBookCompletionJob,
  enqueueMemoirBookCompletionJob,
  enqueueMemoirChapterPreparationJob,
  getMemoirBookCompletionJob,
  getMemoirBookCompletionOverview,
  type MemoirBookCompletionJob,
} from "../../services/memoir-voice-api";
import { routeAuthError } from "../../services/navigation";
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
}

interface ChapterDataset {
  chapterId?: string;
}

type ChapterTapEvent = PageDatasetEvent<ChapterDataset>;

function bookCompletionJobActive(job: MemoirBookCompletionJob): boolean {
  return job.status === "pending" || job.status === "running";
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
    bulkCheckVisible: recheckingAll || allChaptersConfirmed || hasCheckableChapters,
    bulkCheckActionText: allChaptersConfirmed
      ? "重新检查全书"
      : "检查待检查章节",
    bulkCheckTitle: allChaptersConfirmed
      ? "当前没有待处理的补充"
      : "先检查是否真的需要补充",
    bulkCheckDetail: allChaptersConfirmed
      ? "所有章节正文都已确认。需要时可以按当前素材重新检查。"
      : "系统会逐章判断，只展示每章当前最重要的一步。",
  };
}

definePage<BookCompletionData>()({
  overviewPollTimer: null as ReturnType<typeof setTimeout> | null,
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

  async loadOverview(silent = false) {
    if (!this.data.projectId) {
      this.setData({ loading: false, error: "没有找到这本书" });
      return;
    }
    if (!silent) {
      this.setData({ loading: true, error: "" });
    }
    try {
      const bulkCheckJob = await getMemoirBookCompletionJob(this.data.projectId);
      const overview = await getMemoirBookCompletionOverview(this.data.projectId);
      const batchActive = bookCompletionJobActive(bulkCheckJob);
      const finishedChapterId = this.data.preparingChapterId;
      const finishedWholeBook = (
        this.data.recheckingAll
        && !batchActive
        && bulkCheckJob.status !== "idle"
      );
      this.setData({
        ...bookCompletionViewPatch(
          overview,
          this.data.preparingChapterId,
          batchActive,
        ),
        ...bookCompletionJobView(bulkCheckJob),
        bulkCheckJob: bulkCheckJob.status === "idle" ? null : bulkCheckJob,
        recheckingAll: batchActive,
        loading: false,
        error: "",
      });
      if (
        batchActive
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
      }
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : "全书检查情况加载失败",
      });
      if (this.data.preparingChapterId || this.data.recheckingAll) {
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

  async recheckAllChapters() {
    if (
      !this.data.bulkCheckVisible
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
      bulkCheckProgressTitle: "正在提交全书检查",
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
        retryJobId
          ? "pending"
          : (this.data.allChaptersConfirmed ? "all" : "pending"),
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
    if (!this.pageVisible || this.overviewPollTimer !== null) {
      return;
    }
    this.overviewPollTimer = setTimeout(() => {
      this.overviewPollTimer = null;
      if (this.pageVisible) {
        void this.loadOverview(true);
      }
    }, POLL_INTERVAL_MS);
  },

  stopPolling() {
    if (this.overviewPollTimer !== null) {
      clearTimeout(this.overviewPollTimer);
      this.overviewPollTimer = null;
    }
  },
});
