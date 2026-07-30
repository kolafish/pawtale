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
  cancelMemoirBookReviewAnswerApplicationJob,
  cancelMemoirBookEditorialRevisionJob,
  cancelMemoirBookCompletionJob,
  enqueueMemoirBookReviewAnswerApplicationJob,
  enqueueMemoirBookEditorialRevisionJob,
  enqueueMemoirBookCompletionJob,
  enqueueMemoirBookReviewJob,
  enqueueMemoirChapterPreparationJob,
  getMemoirBookCompletionJob,
  getMemoirBookCompletionOverview,
  getMemoirBookOutput,
  getMemoirBookReview,
  getMemoirBookReviewAnswerApplicationJob,
  getMemoirBookEditorialRevisionJob,
  getMemoirBookReviewJob,
  listMemoirCompletionIssues,
  type MemoirBookReview,
  type MemoirBookReviewAnswerApplicationJob,
  type MemoirBookEditorialAction,
  type MemoirBookEditorialRevisionJob,
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

interface EditorialActionView extends MemoirBookEditorialAction {
  typeLabel: string;
  selected: boolean;
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
  answerApplicationJob: MemoirBookReviewAnswerApplicationJob | null;
  answerApplicationProgressVisible: boolean;
  answerApplicationProgressTitle: string;
  answerApplicationProgressDetail: string;
  answerApplicationProgressMode: AITaskProgressMode;
  answerApplicationProgressClass: string;
  answerApplicationCanCancel: boolean;
  applyingAnswers: boolean;
  answerApplicationCancelling: boolean;
  editorialRevisionJob: MemoirBookEditorialRevisionJob | null;
  selectedEditorialActionIds: string[];
  editorialActionViews: EditorialActionView[];
  editorialRevisionProgressVisible: boolean;
  editorialRevisionProgressTitle: string;
  editorialRevisionProgressDetail: string;
  editorialRevisionProgressClass: string;
  editorialRevisionCanCancel: boolean;
  generatingEditorialRevision: boolean;
  editorialRevisionCancelling: boolean;
  bookOutputAvailable: boolean;
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
  const current = normalizedReview?.status === "ready"
    ? issues.filter((issue) => (
      issue.scope === "book"
      && issue.input_signature === normalizedReview.input_signature
      && issue.status !== "closed"
    ))
    : [];
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

function answerApplicationJobActive(
  job: MemoirBookReviewAnswerApplicationJob,
): boolean {
  return job.status === "pending" || job.status === "running";
}

function editorialRevisionJobActive(
  job: MemoirBookEditorialRevisionJob,
): boolean {
  return job.status === "pending" || job.status === "running";
}

export function editorialRevisionJobView(
  job: MemoirBookEditorialRevisionJob | null,
): Pick<
  BookCompletionData,
  | "editorialRevisionProgressVisible"
  | "editorialRevisionProgressTitle"
  | "editorialRevisionProgressDetail"
  | "editorialRevisionProgressClass"
  | "editorialRevisionCanCancel"
> {
  if (!job || job.status === "idle" || job.status === "succeeded") {
    return {
      editorialRevisionProgressVisible: false,
      editorialRevisionProgressTitle: "",
      editorialRevisionProgressDetail: "",
      editorialRevisionProgressClass: "",
      editorialRevisionCanCancel: false,
    };
  }
  if (editorialRevisionJobActive(job)) {
    const titles: Partial<
      Record<MemoirBookEditorialRevisionJob["stage"], string>
    > = {
      queued: "修改预览已提交",
      assembling: "正在准备当前书稿",
      revising: "正在执行所选编辑建议",
      saving: "正在保存修改预览",
    };
    return {
      editorialRevisionProgressVisible: true,
      editorialRevisionProgressTitle: (
        titles[job.stage] || "正在生成修改预览"
      ),
      editorialRevisionProgressDetail: "完成后仍需查看前后内容并确认",
      editorialRevisionProgressClass: "active",
      editorialRevisionCanCancel: job.can_cancel,
    };
  }
  return {
    editorialRevisionProgressVisible: true,
    editorialRevisionProgressTitle: (
      job.status === "failed" ? "修改预览未完成" : "已停止生成修改预览"
    ),
    editorialRevisionProgressDetail: (
      job.error_message || "所选建议仍然保留，可以重新生成。"
    ),
    editorialRevisionProgressClass: (
      job.status === "failed" ? "failed" : "cancelled"
    ),
    editorialRevisionCanCancel: false,
  };
}

export function answerApplicationJobView(
  job: MemoirBookReviewAnswerApplicationJob | null,
): Pick<
  BookCompletionData,
  | "answerApplicationProgressVisible"
  | "answerApplicationProgressTitle"
  | "answerApplicationProgressDetail"
  | "answerApplicationProgressMode"
  | "answerApplicationProgressClass"
  | "answerApplicationCanCancel"
> {
  if (!job || job.status === "idle" || job.status === "succeeded") {
    return {
      answerApplicationProgressVisible: false,
      answerApplicationProgressTitle: "",
      answerApplicationProgressDetail: "",
      answerApplicationProgressMode: "hidden",
      answerApplicationProgressClass: "",
      answerApplicationCanCancel: false,
    };
  }
  if (answerApplicationJobActive(job)) {
    const stageTitles: Partial<
      Record<MemoirBookReviewAnswerApplicationJob["stage"], string>
    > = {
      queued: "书稿调整已提交",
      assembling: "正在准备当前书稿",
      revising: "正在把回答写入相关章节",
      saving: "正在生成修改预览",
    };
    return {
      answerApplicationProgressVisible: true,
      answerApplicationProgressTitle: (
        stageTitles[job.stage] || "正在根据回答调整书稿"
      ),
      answerApplicationProgressDetail: (
        "任务在后台执行，完成后先预览，确认后才会修改书稿"
      ),
      answerApplicationProgressMode: "indeterminate",
      answerApplicationProgressClass: "active",
      answerApplicationCanCancel: job.can_cancel,
    };
  }
  if (job.status === "failed") {
    return {
      answerApplicationProgressVisible: true,
      answerApplicationProgressTitle: "书稿调整未完成",
      answerApplicationProgressDetail: (
        job.error_message || "回答仍然保留，可以重新调整。"
      ),
      answerApplicationProgressMode: "hidden",
      answerApplicationProgressClass: "failed",
      answerApplicationCanCancel: false,
    };
  }
  return {
    answerApplicationProgressVisible: true,
    answerApplicationProgressTitle: "书稿调整已停止",
    answerApplicationProgressDetail: "回答仍然保留，可以重新调整。",
    answerApplicationProgressMode: "hidden",
    answerApplicationProgressClass: "cancelled",
    answerApplicationCanCancel: false,
  };
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
  if (job.status === "succeeded") {
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
    answerApplicationJob: null,
    answerApplicationProgressVisible: false,
    answerApplicationProgressTitle: "",
    answerApplicationProgressDetail: "",
    answerApplicationProgressMode: "hidden",
    answerApplicationProgressClass: "",
    answerApplicationCanCancel: false,
    applyingAnswers: false,
    answerApplicationCancelling: false,
    editorialRevisionJob: null,
    selectedEditorialActionIds: [],
    editorialActionViews: [],
    editorialRevisionProgressVisible: false,
    editorialRevisionProgressTitle: "",
    editorialRevisionProgressDetail: "",
    editorialRevisionProgressClass: "",
    editorialRevisionCanCancel: false,
    generatingEditorialRevision: false,
    editorialRevisionCancelling: false,
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

  openAdjustedBook() {
    if (!this.data.projectId) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/book-export/index", {
        projectId: this.data.projectId,
        action: "preview",
      }),
    });
  },

  openBookRevision() {
    const proposalId = this.data.answerApplicationJob?.proposal_id;
    if (!this.data.projectId || !proposalId) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/book-revision/index", {
        projectId: this.data.projectId,
        proposalId,
      }),
    });
  },

  openEditorialRevision() {
    const proposalId = this.data.editorialRevisionJob?.proposal_id;
    if (!this.data.projectId || !proposalId) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/book-revision/index", {
        projectId: this.data.projectId,
        proposalId,
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
        answerApplicationJob,
        editorialRevisionJob,
      ] = view.allChaptersConfirmed
        ? await Promise.all([
          getMemoirBookReview(this.data.projectId),
          getMemoirBookReviewJob(this.data.projectId),
          listMemoirCompletionIssues(this.data.projectId),
          optionalApiResource(getMemoirBookOutput(this.data.projectId)),
          getMemoirBookReviewAnswerApplicationJob(this.data.projectId),
          getMemoirBookEditorialRevisionJob(this.data.projectId),
        ])
        : [null, null, { issues: [] }, null, null, null];
      const reviewJobActive = Boolean(
        bookReviewJob && bookReviewJobActive(bookReviewJob),
      );
      const applicationActive = Boolean(
        answerApplicationJob
        && answerApplicationJobActive(answerApplicationJob),
      );
      const editorialRevisionActive = Boolean(
        editorialRevisionJob
        && editorialRevisionJobActive(editorialRevisionJob),
      );
      const finishedBookReview = (
        this.data.checkingBook
        && !reviewJobActive
        && bookReviewJob?.status !== "idle"
      );
      const finishedChapterId = this.data.preparingChapterId;
      const finishedAnswerApplication = (
        this.data.applyingAnswers
        && !applicationActive
        && answerApplicationJob?.status !== "idle"
      );
      const finishedEditorialRevision = (
        this.data.generatingEditorialRevision
        && !editorialRevisionActive
        && editorialRevisionJob?.status !== "idle"
      );
      const finishedWholeBook = (
        this.data.recheckingAll
        && !batchActive
        && bulkCheckJob.status !== "idle"
      );
      const selectedEditorialActionIds = (
        this.data.selectedEditorialActionIds.filter(
          (actionId) => (
            bookReview?.review.editorial_actions?.some(
              (action) => action.id === actionId,
            ) === true
          ),
        )
      );
      this.setData({
        ...view,
        ...bookReviewViewPatch(bookReview, issueCollection.issues),
        selectedEditorialActionIds,
        editorialActionViews: editorialActionViews(
          bookReview,
          selectedEditorialActionIds,
        ),
        bookOutputAvailable: Boolean(bookOutput),
        ...bookReviewJobView(bookReviewJob),
        bookReviewJob,
        checkingBook: reviewJobActive,
        ...answerApplicationJobView(answerApplicationJob),
        answerApplicationJob,
        applyingAnswers: applicationActive,
        ...editorialRevisionJobView(editorialRevisionJob),
        editorialRevisionJob,
        generatingEditorialRevision: editorialRevisionActive,
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
        || applicationActive
        || editorialRevisionActive
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
        if (finishedAnswerApplication) {
          const toastTitle = answerApplicationJob?.status === "succeeded"
            ? "修改预览已生成"
            : (answerApplicationJob?.status === "cancelled"
              ? "已停止调整书稿"
              : "书稿调整未完成");
          wx.showToast({
            title: toastTitle,
            icon: answerApplicationJob?.status === "succeeded"
              ? "success"
              : "none",
          });
        }
        if (finishedEditorialRevision) {
          const succeeded = editorialRevisionJob?.status === "succeeded";
          wx.showToast({
            title: succeeded ? "修改预览已生成" : "修改预览未完成",
            icon: succeeded ? "success" : "none",
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
        || this.data.applyingAnswers
        || this.data.generatingEditorialRevision
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

  async applyBookReviewAnswers() {
    const state = this.data.answerApplicationJob;
    if (
      !state?.can_apply
      || this.data.applyingAnswers
      || this.data.answerApplicationCancelling
    ) {
      return;
    }
    this.setData({
      applyingAnswers: true,
      error: "",
      answerApplicationProgressVisible: true,
      answerApplicationProgressTitle: "正在提交书稿调整",
      answerApplicationProgressDetail: (
        "提交后可以离开页面，完成后仍需预览并确认"
      ),
      answerApplicationProgressMode: "indeterminate",
      answerApplicationProgressClass: "active",
      answerApplicationCanCancel: false,
    });
    try {
      await enqueueMemoirBookReviewAnswerApplicationJob(this.data.projectId);
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      const message = error instanceof Error
        ? error.message
        : "根据回答调整书稿失败";
      this.setData({ applyingAnswers: false });
      await this.loadOverview(true);
      if (
        !this.data.answerApplicationJob
        || !answerApplicationJobActive(this.data.answerApplicationJob)
      ) {
        this.setData({ error: message });
      }
    }
  },

  async cancelAnswerApplication() {
    const job = this.data.answerApplicationJob;
    if (
      !job?.job_id
      || !answerApplicationJobActive(job)
      || this.data.answerApplicationCancelling
    ) {
      return;
    }
    this.setData({
      answerApplicationCancelling: true,
      answerApplicationCanCancel: false,
      error: "",
    });
    try {
      await cancelMemoirBookReviewAnswerApplicationJob(
        this.data.projectId,
        job.job_id,
      );
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "停止书稿调整失败",
      });
    } finally {
      this.setData({ answerApplicationCancelling: false });
    }
  },

  toggleEditorialAction(event: ChapterTapEvent) {
    const actionId = datasetString(event, "actionId");
    if (
      !actionId
      || this.data.generatingEditorialRevision
      || this.data.editorialRevisionCancelling
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

  async generateEditorialRevision() {
    const actionIds = this.data.selectedEditorialActionIds;
    if (
      !actionIds.length
      || this.data.generatingEditorialRevision
      || this.data.editorialRevisionCancelling
      || (
        Boolean(this.data.answerApplicationJob?.answer_count)
        && !this.data.answerApplicationJob?.already_applied
      )
    ) {
      return;
    }
    this.setData({
      generatingEditorialRevision: true,
      error: "",
      editorialRevisionProgressVisible: true,
      editorialRevisionProgressTitle: "正在提交修改预览",
      editorialRevisionProgressDetail: "完成后仍需查看前后内容并确认",
      editorialRevisionProgressClass: "active",
      editorialRevisionCanCancel: false,
    });
    try {
      await enqueueMemoirBookEditorialRevisionJob(
        this.data.projectId,
        actionIds,
      );
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      const message = error instanceof Error
        ? error.message
        : "生成修改预览失败";
      this.setData({ generatingEditorialRevision: false });
      await this.loadOverview(true);
      if (
        !this.data.editorialRevisionJob
        || !editorialRevisionJobActive(this.data.editorialRevisionJob)
      ) {
        this.setData({ error: message });
      }
    }
  },

  async cancelEditorialRevision() {
    const job = this.data.editorialRevisionJob;
    if (
      !job?.job_id
      || !editorialRevisionJobActive(job)
      || this.data.editorialRevisionCancelling
    ) {
      return;
    }
    this.setData({
      editorialRevisionCancelling: true,
      editorialRevisionCanCancel: false,
      error: "",
    });
    try {
      await cancelMemoirBookEditorialRevisionJob(
        this.data.projectId,
        job.job_id,
      );
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "停止生成修改预览失败",
      });
    } finally {
      this.setData({ editorialRevisionCancelling: false });
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
