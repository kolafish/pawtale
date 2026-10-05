import type {
  MemoirChapterCompletionSummary,
  MemoirBookCompletionOverview,
} from "../../domain/chapter-completion";
import type { MemoirCompletionIssue } from "../../domain/memoir-completion-issue";
import {
  aiTaskProgressPercent,
  type AITaskProgressView,
} from "../../services/ai-task-progress";
import {
  accessibilityPageClass,
  accessibilityPagePatch,
  readLargeTextEnabled,
} from "../../services/accessibility-settings";
import { definePage, pageRuntime } from "../../services/page-definition";
import { PollingTimer } from "../../services/polling-timer";
import {
  cancelMemoirBookReviewJob,
  cancelMemoirBookRevisionJob,
  cancelMemoirBookCompletionJob,
  enqueueMemoirBookRevisionJob,
  enqueueMemoirBookCompletionJob,
  enqueueMemoirBookReviewJob,
  enqueueMemoirChapterPreparationJob,
  getMemoirBookCompletionOverview,
  type MemoirBookReview,
  type MemoirBookEditorialAction,
  type MemoirBookRevisionJob,
  type MemoirBookReviewJob,
  type MemoirBookCompletionJob,
} from "../../services/memoir-book-completion-api";
import { returnToBook, routeAuthError } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
} from "../../services/page-input";


const POLL_INTERVAL_MS = 1500;
const PAGE_CLASS = "completion-page ui-page-shell ui-accessible-page";

interface ChapterView extends MemoirChapterCompletionSummary {
  stateText: string;
  actionText: string;
  chapterClass: string;
  disabled: boolean;
}

interface EditorialActionView extends MemoirBookEditorialAction {
  typeLabel: string;
  selected: boolean;
}

interface TaskProgressView extends AITaskProgressView {
  visible: boolean;
  className: string;
  canCancel: boolean;
}

const HIDDEN_TASK_PROGRESS: TaskProgressView = {
  visible: false,
  title: "",
  detail: "",
  progressMode: "hidden",
  progress: 0,
  className: "",
  canCancel: false,
};

function activeTaskProgress(title: string, detail: string): TaskProgressView {
  return {
    ...HIDDEN_TASK_PROGRESS,
    visible: true,
    title,
    detail,
    progressMode: "indeterminate",
    className: "active",
  };
}

interface BookCompletionData {
  pageClass: string;
  largeTextEnabled: boolean;
  projectId: string;
  projectTitle: string;
  loading: boolean;
  error: string;
  headline: string;
  detail: string;
  progressText: string;
  blockingIssueCount: number;
  chapters: ChapterView[];
  canReviewBook: boolean;
  bulkCheckVisible: boolean;
  bulkCheckActionText: string;
  bulkCheckTitle: string;
  bulkCheckDetail: string;
  bulkCheckJob: MemoirBookCompletionJob | null;
  bulkCheckProgress: TaskProgressView;
  bulkCheckCancelling: boolean;
  preparingChapterId: string;
  recheckingAll: boolean;
  bookReview: MemoirBookReview | null;
  requiredBookIssues: MemoirCompletionIssue[];
  optionalBookIssues: MemoirCompletionIssue[];
  bookReviewJob: MemoirBookReviewJob | null;
  bookReviewProgress: TaskProgressView;
  bookReviewCancelling: boolean;
  checkingBook: boolean;
  revisionJob: MemoirBookRevisionJob | null;
  includeBookReviewAnswers: boolean;
  selectedEditorialActionIds: string[];
  editorialActionViews: EditorialActionView[];
  revisionProgress: TaskProgressView;
  generatingRevision: boolean;
  revisionCancelling: boolean;
}

interface ChapterDataset {
  chapterId?: string;
  actionId?: string;
}

type ChapterTapEvent = PageDatasetEvent<ChapterDataset>;

function editorialActionViews(
  review: MemoirBookReview | null,
  selectedIds: string[],
): EditorialActionView[] {
  const labels = {
    remove_repetition: "去重复",
    improve_transition: "补衔接",
    reorder_chapters: "调顺序",
    revise_book_profile: "成书信息",
  };
  return (review?.review.editorial_actions || []).map((action) => ({
    ...action,
    typeLabel: labels[action.action_type],
    selected: selectedIds.includes(action.id),
  }));
}

export function bookReviewViewPatch(
  review: MemoirBookReview | null,
  issues: MemoirCompletionIssue[],
): Pick<
  BookCompletionData,
  "bookReview" | "requiredBookIssues" | "optionalBookIssues"
> {
  const normalizedReview = review
    ? {
      ...review,
      review: {
        ...review.review,
        editorial_actions: review.review.editorial_actions || [],
      },
    }
    : null;
  const current = issues.filter((issue) => (
    issue.scope === "book"
    && issue.status !== "closed"
    && (issue.channel === "manual_review" || (normalizedReview?.status === "ready"
      && issue.input_signature === normalizedReview.input_signature))
  ));
  return {
    bookReview: normalizedReview,
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

function revisionJobActive(job: MemoirBookRevisionJob): boolean {
  return job.status === "pending" || job.status === "running";
}

export function revisionJobView(
  job: MemoirBookRevisionJob | null,
): TaskProgressView {
  if (!job || job.status === "idle" || job.status === "succeeded") {
    return HIDDEN_TASK_PROGRESS;
  }
  if (revisionJobActive(job)) {
    const titles: Partial<
      Record<MemoirBookRevisionJob["stage"], string>
    > = {
      queued: "修改思路任务已提交",
      assembling: "正在准备当前书稿",
      revising: "正在根据所选内容制定修改思路",
      saving: "正在保存修改思路",
    };
    return {
      visible: true,
      title: titles[job.stage] || "正在生成修改思路",
      detail: "任务在后台执行，完成后仍需查看并确认整体思路",
      progressMode: "indeterminate",
      progress: 0,
      className: "active",
      canCancel: job.can_cancel,
    };
  }
  const failed = job.status === "failed";
  return {
    visible: true,
    title: failed ? "修改思路未完成" : "已停止生成修改思路",
    detail: job.error_message || (
      "选择仍然保留，可以重新生成。"
    ),
    progressMode: "hidden",
    progress: 0,
    className: failed ? "failed" : "cancelled",
    canCancel: false,
  };
}

export function bookReviewJobView(
  job: MemoirBookReviewJob | null,
): TaskProgressView {
  if (!job || job.status === "idle" || job.status === "succeeded") {
    return HIDDEN_TASK_PROGRESS;
  }
  if (bookReviewJobActive(job)) {
    const preparing = job.stage === "preparing_chapters";
    const stageTitles: Partial<
      Record<MemoirBookReviewJob["stage"], string>
    > = {
      queued: "全书检查已提交",
      preparing_chapters: job.current_chapter_title
        ? `正在准备《${job.current_chapter_title}》`
        : "正在准备章节摘要",
      reviewing: "正在检查全书",
      saving: "正在保存检查结果",
    };
    const stageTitle = stageTitles[job.stage] || "正在检查全书";
    const detail = preparing && job.total_chapter_count
      ? `已准备 ${job.processed_chapter_count}/${job.total_chapter_count} 章，离开页面后任务会继续`
      : (job.stage === "saving"
        ? "检查已经完成，正在保存结果"
        : "任务正在后台执行，离开页面后进度会继续保存");
    return {
      visible: true,
      title: stageTitle,
      detail,
      progressMode: preparing && job.total_chapter_count
        ? "determinate"
        : "indeterminate",
      progress: preparing
        ? aiTaskProgressPercent(
          job.processed_chapter_count,
          job.total_chapter_count,
        )
        : 0,
      className: "active",
      canCancel: job.can_cancel,
    };
  }
  if (job.status === "failed") {
    return {
      visible: true,
      title: "全书检查未完成",
      detail: job.error_message || "可以重新检查当前版本。",
      progressMode: "hidden",
      progress: 0,
      className: "failed",
      canCancel: false,
    };
  }
  if (job.status === "cancelled") {
    return {
      visible: true,
      title: "全书检查已停止",
      detail: "已经保存的章节摘要仍会保留，可以重新检查。",
      progressMode: "hidden",
      progress: 0,
      className: "cancelled",
      canCancel: false,
    };
  }
  return {
    visible: true,
    title: "全书检查已完成",
    detail: "检查结果已经保存。",
    progressMode: "hidden",
    progress: 0,
    className: "succeeded",
    canCancel: false,
  };
}

export function bookCompletionJobView(
  job: MemoirBookCompletionJob | null,
): TaskProgressView {
  if (!job || job.status === "idle") {
    return HIDDEN_TASK_PROGRESS;
  }
  const processedText = `已处理 ${job.processed_chapter_count}/${job.total_chapter_count} 章`;
  if (bookCompletionJobActive(job)) {
    const failureText = job.failed_chapter_count
      ? ` · ${job.failed_chapter_count} 章未完成`
      : "";
    return {
      visible: true,
      title: job.current_chapter_title
        ? `正在检查《${job.current_chapter_title}》`
        : "正在准备全书检查",
      detail: `${processedText}${failureText}，每章完成后都会保存`,
      progressMode: job.total_chapter_count ? "determinate" : "indeterminate",
      progress: aiTaskProgressPercent(
        job.processed_chapter_count,
        job.total_chapter_count,
      ),
      className: "active",
      canCancel: job.can_cancel,
    };
  }
  if (job.status === "failed") {
    return {
      visible: true,
      title: `${job.failed_chapter_count} 章检查未完成`,
      detail: `${processedText}，已完成结果已保留，可以重新检查失败章节。`,
      progressMode: "hidden",
      progress: 0,
      className: "failed",
      canCancel: false,
    };
  }
  if (job.status === "cancelled") {
    return {
      visible: true,
      title: "全书检查已停止",
      detail: `${processedText}，已完成结果已保留。`,
      progressMode: "hidden",
      progress: 0,
      className: "cancelled",
      canCancel: false,
    };
  }
  return {
    visible: true,
    title: "全书检查已完成",
    detail: `${processedText}，请查看各章当前最重要的一步。`,
    progressMode: "hidden",
    progress: 0,
    className: "succeeded",
    canCancel: false,
  };
}

function chapterView(
  item: MemoirChapterCompletionSummary,
  preparingChapterId: string,
  recheckingAll = false,
): ChapterView {
  const stateText = item.state === "needs_input" && item.next_action.kind === "wait"
    ? "待补充讲述"
    : {
      not_started: "待检查",
      preparing: "正在检查",
      needs_input: "需要确认",
      draft_ready: "正文可读",
      confirmed: "正文可读",
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
      : (item.state === "confirmed" || item.state === "draft_ready"
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
  | "blockingIssueCount"
  | "chapters"
  | "canReviewBook"
  | "bulkCheckVisible"
  | "bulkCheckActionText"
  | "bulkCheckTitle"
  | "bulkCheckDetail"
> {
  const canReviewBook = (
    overview.chapters.length > 0
    && overview.chapters.every((item) => item.state === "confirmed" || item.state === "draft_ready")
  );
  const hasCheckableChapters = overview.chapters.some(
    (item) => item.next_action.kind === "prepare",
  );
  return {
    headline: overview.headline,
    blockingIssueCount: overview.blocking_issue_count || 0,
    detail: overview.detail,
    progressText: overview.total_chapters
      ? `${overview.completed_chapters} / ${overview.total_chapters} 章正文已更新`
      : "还没有章节",
    chapters: overview.chapters.map(
      (item) => chapterView(item, preparingChapterId, recheckingAll),
    ),
    canReviewBook,
    bulkCheckVisible: recheckingAll || hasCheckableChapters,
    bulkCheckActionText: "检查待检查章节",
    bulkCheckTitle: "先检查是否真的需要补充",
    bulkCheckDetail: "系统会逐章判断，只展示每章当前最重要的一步。",
  };
}

definePage<BookCompletionData>()({
  overviewPoller: pageRuntime(() => new PollingTimer(POLL_INTERVAL_MS)),
  overviewLoadSequence: 0,
  pageVisible: false,

  data: {
    pageClass: accessibilityPageClass(PAGE_CLASS),
    largeTextEnabled: readLargeTextEnabled(),
    projectId: "",
    projectTitle: "",
    loading: true,
    error: "",
    headline: "逐章整理这本书",
    detail: "",
    progressText: "",
    blockingIssueCount: 0,
    chapters: [],
    canReviewBook: false,
    bulkCheckVisible: false,
    bulkCheckActionText: "",
    bulkCheckTitle: "",
    bulkCheckDetail: "",
    bulkCheckJob: null,
    bulkCheckProgress: HIDDEN_TASK_PROGRESS,
    bulkCheckCancelling: false,
    preparingChapterId: "",
    recheckingAll: false,
    bookReview: null,
    requiredBookIssues: [],
    optionalBookIssues: [],
    bookReviewJob: null,
    bookReviewProgress: HIDDEN_TASK_PROGRESS,
    bookReviewCancelling: false,
    checkingBook: false,
    revisionJob: null,
    includeBookReviewAnswers: false,
    selectedEditorialActionIds: [],
    editorialActionViews: [],
    revisionProgress: HIDDEN_TASK_PROGRESS,
    generatingRevision: false,
    revisionCancelling: false,
  },

  onLoad(options: PageQuery) {
    this.setData({
      projectId: queryParam(options, "projectId"),
      projectTitle: queryParam(options, "projectTitle"),
    });
  },

  async onShow() {
    this.setData({ ...accessibilityPagePatch(PAGE_CLASS), largeTextEnabled: readLargeTextEnabled() });
    this.pageVisible = true;
    await this.loadOverview();
  },

  onHide() {
    this.pageVisible = false;
    this.overviewLoadSequence += 1;
    this.stopPolling();
  },

  onUnload() {
    this.pageVisible = false;
    this.overviewLoadSequence += 1;
    this.stopPolling();
  },

  async onPullDownRefresh() {
    await this.loadOverview();
    wx.stopPullDownRefresh();
  },

  openBookHome() {
    returnToBook();
  },

  openBookRevision() {
    const proposalId = this.data.revisionJob?.proposal_id;
    if (!this.data.projectId || !proposalId) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/revision-workbench/index", {
        scope: "book",
        projectId: this.data.projectId,
        proposalId,
      }),
    });
  },

  async loadOverview(silent = false) {
    const loadSequence = ++this.overviewLoadSequence;
    if (!this.data.projectId) {
      this.setData({ loading: false, error: "没有找到这本书" });
      return;
    }
    if (!silent) {
      this.setData({ loading: true, error: "" });
    }
    try {
      const overview = await getMemoirBookCompletionOverview(
        this.data.projectId,
      );
      if (loadSequence !== this.overviewLoadSequence) {
        return;
      }
      const bulkCheckJob = overview.completion_job;
      const batchActive = bookCompletionJobActive(bulkCheckJob);
      const view = bookCompletionViewPatch(
        overview,
        this.data.preparingChapterId,
        batchActive,
      );
      const bookReview = overview.book_review;
      const bookReviewJob = overview.book_review_job;
      const bookIssues = overview.book_issues;
      const revisionJob = overview.revision_job;
      const reviewJobActive = Boolean(
        bookReviewJob && bookReviewJobActive(bookReviewJob),
      );
      const revisionActive = Boolean(
        revisionJob
        && revisionJobActive(revisionJob),
      );
      const restoreRevisionSelection = Boolean(
        revisionActive || revisionJob?.proposal_status === "ready",
      );
      const finishedBookReview = (
        this.data.checkingBook
        && !reviewJobActive
        && bookReviewJob?.status !== "idle"
      );
      const finishedChapterId = this.data.preparingChapterId;
      const finishedRevision = (
        this.data.generatingRevision
        && !revisionActive
        && revisionJob?.status !== "idle"
      );
      const finishedWholeBook = (
        this.data.recheckingAll
        && !batchActive
        && bulkCheckJob.status !== "idle"
      );
      const selectedEditorialActionIds = (
        (
          restoreRevisionSelection
            ? revisionJob?.action_ids || []
            : this.data.selectedEditorialActionIds
        ).filter(
          (actionId) => (
            bookReview?.review.editorial_actions?.some(
              (action) => action.id === actionId,
            ) === true
          ),
        )
      );
      const revisionProgress = revisionJobView(revisionJob);
      this.setData({
        ...view,
        ...bookReviewViewPatch(bookReview, bookIssues),
        selectedEditorialActionIds,
        editorialActionViews: editorialActionViews(
          bookReview,
          selectedEditorialActionIds,
        ),
        bookReviewProgress: bookReviewJobView(bookReviewJob),
        bookReviewJob,
        checkingBook: reviewJobActive,
        includeBookReviewAnswers: (
          restoreRevisionSelection && revisionJob
            ? revisionJob.include_answers
            : this.data.includeBookReviewAnswers
        ),
        revisionProgress,
        revisionJob,
        generatingRevision: revisionActive,
        bulkCheckProgress: bookCompletionJobView(bulkCheckJob),
        bulkCheckJob: bulkCheckJob.status === "idle" ? null : bulkCheckJob,
        recheckingAll: batchActive,
        loading: false,
        error: "",
      });
      if (
        batchActive
        || reviewJobActive
        || revisionActive
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
                : (checkedChapters.every((item) => item.state === "confirmed" || item.state === "draft_ready")
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
        if (finishedRevision) {
          const succeeded = revisionJob?.status === "succeeded";
          wx.showToast({
            title: succeeded ? "修改思路已生成" : "修改思路未完成",
            icon: succeeded ? "success" : "none",
          });
        }
      }
    } catch (error) {
      if (loadSequence !== this.overviewLoadSequence) {
        return;
      }
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
        || this.data.generatingRevision
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
      bulkCheckProgress: activeTaskProgress(
        "正在提交章节检查",
        "任务提交后可以离开页面，进度会继续保存",
      ),
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
      !this.data.canReviewBook
      || this.data.checkingBook
    ) {
      return;
    }
    this.setData({
      checkingBook: true,
      error: "",
      bookReviewProgress: activeTaskProgress(
        "正在提交全书检查",
        "任务提交后可以离开页面，进度会继续保存",
      ),
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
      bookReviewProgress: {
        ...this.data.bookReviewProgress,
        canCancel: false,
      },
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

  toggleBookReviewAnswers() {
    const job = this.data.revisionJob;
    if (
      !job?.answer_count
      || job.already_applied
      || job.proposal_status === "ready"
      || this.data.generatingRevision
      || this.data.revisionCancelling
    ) {
      return;
    }
    this.setData({
      includeBookReviewAnswers: !this.data.includeBookReviewAnswers,
    });
  },

  toggleEditorialAction(event: ChapterTapEvent) {
    const actionId = datasetString(event, "actionId");
    if (
      !actionId
      || this.data.revisionJob?.proposal_status === "ready"
      || this.data.generatingRevision
      || this.data.revisionCancelling
    ) {
      return;
    }
    const selected = this.data.selectedEditorialActionIds;
    const selectedEditorialActionIds = selected.includes(actionId)
      ? selected.filter((value) => value !== actionId)
      : [...selected, actionId];
    this.setData({
      selectedEditorialActionIds,
      editorialActionViews: editorialActionViews(
        this.data.bookReview,
        selectedEditorialActionIds,
      ),
    });
  },

  async generateBookRevision() {
    const actionIds = this.data.selectedEditorialActionIds;
    if (
      (!this.data.includeBookReviewAnswers && !actionIds.length)
      || this.data.generatingRevision
      || this.data.revisionCancelling
    ) {
      return;
    }
    this.setData({
      generatingRevision: true,
      error: "",
      revisionProgress: activeTaskProgress(
        "正在提交修改思路",
        "完成后仍需查看并确认整体思路",
      ),
    });
    try {
      await enqueueMemoirBookRevisionJob(
        this.data.projectId,
        this.data.includeBookReviewAnswers,
        actionIds,
      );
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      const message = error instanceof Error
        ? error.message
        : "生成修改思路失败";
      this.setData({ generatingRevision: false });
      await this.loadOverview(true);
      if (
        !this.data.revisionJob
        || !revisionJobActive(this.data.revisionJob)
      ) {
        this.setData({ error: message });
      }
    }
  },

  async cancelBookRevision() {
    const job = this.data.revisionJob;
    if (
      !job?.job_id
      || !revisionJobActive(job)
      || this.data.revisionCancelling
    ) {
      return;
    }
    this.setData({
      revisionCancelling: true,
      revisionProgress: {
        ...this.data.revisionProgress,
        canCancel: false,
      },
      error: "",
    });
    try {
      await cancelMemoirBookRevisionJob(
        this.data.projectId,
        job.job_id,
      );
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "停止生成修改思路失败",
      });
    } finally {
      this.setData({ revisionCancelling: false });
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
      bulkCheckProgress: {
        ...this.data.bulkCheckProgress,
        canCancel: false,
      },
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
