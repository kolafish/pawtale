import type {
  MemoirChapterCompletionSummary,
  MemoirBookCompletionOverview,
} from "../../domain/chapter-completion";
import { trackAuditAction } from "../../services/audit-tracker";
import { definePage } from "../../services/page-definition";
import {
  closeMemoirCompletionIssue,
  createMemoirVoiceSession,
  getMemoirBookCompletionOverview,
  prepareMemoirBookChapter,
} from "../../services/memoir-voice-api";
import { routeAuthError } from "../../services/navigation";
import {
  datasetString,
  pageUrl,
  queryParam,
  type PageDatasetEvent,
  type PageQuery,
} from "../../services/page-input";
import {
  readVoiceAuthorizationConsent,
  syncVoiceAuthorizationConsent,
  VOICE_AUTHORIZATION_PAGE_URL,
} from "../../services/voice-authorization";


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
  preparingChapterId: string;
  recheckingAll: boolean;
  startingIssueId: string;
}

interface ChapterDataset {
  chapterId?: string;
}

type ChapterTapEvent = PageDatasetEvent<ChapterDataset>;

function chapterView(
  item: MemoirChapterCompletionSummary,
  preparingChapterId: string,
  recheckingAll = false,
): ChapterView {
  const stateText = {
    not_started: "待检查",
    preparing: "正在检查",
    needs_input: "需要您补充",
    draft_ready: "等待通读",
    confirmed: "已经确认",
    failed: "检查未完成",
  }[item.state];
  const busy = (
    recheckingAll
    || preparingChapterId === item.chapter_id
    || item.state === "preparing"
  );
  return {
    ...item,
    stateText: busy ? "正在检查" : stateText,
    actionText: busy
      ? "正在检查"
      : (item.state === "confirmed"
        ? "查看正文"
        : (item.next_action.action_text || "查看本章")),
    chapterClass: `chapter-item ${item.state}`,
    disabled: busy,
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
    bulkCheckVisible: allChaptersConfirmed || hasCheckableChapters,
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
    preparingChapterId: "",
    recheckingAll: false,
    startingIssueId: "",
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
      const overview = await getMemoirBookCompletionOverview(this.data.projectId);
      this.setData({
        ...bookCompletionViewPatch(
          overview,
          this.data.preparingChapterId,
          this.data.recheckingAll,
        ),
        loading: false,
        error: "",
      });
      if (overview.chapters.some((item) => item.state === "preparing")) {
        this.schedulePolling();
      } else {
        this.stopPolling();
        const finishedChapterId = this.data.preparingChapterId;
        const finishedWholeBook = this.data.recheckingAll;
        if (finishedChapterId || finishedWholeBook) {
          this.setData({
            preparingChapterId: "",
            recheckingAll: false,
            chapters: overview.chapters.map((item) => chapterView(item, "")),
          });
          const checkedChapters = finishedWholeBook
            ? overview.chapters
            : overview.chapters.filter((item) => item.chapter_id === finishedChapterId);
          const toastTitle = checkedChapters.some((item) => item.state === "needs_input")
            ? "发现需要补充的内容"
            : (checkedChapters.every((item) => item.state === "confirmed")
              ? "暂未发现待补内容"
              : "检查完成，请查看结果");
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
      || this.data.preparingChapterId
      || this.data.recheckingAll
      || this.data.startingIssueId
    ) {
      return;
    }
    if (chapter.next_action.kind === "prepare") {
      await this.prepareChapter(chapter.chapter_id);
      return;
    }
    if (
      chapter.next_action.kind === "start_interview"
    ) {
      await this.beginCompletionInterview(chapter);
      return;
    }
    if (chapter.next_action.kind === "confirm") {
      this.confirmCompletionIssue(chapter);
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
      await prepareMemoirBookChapter(this.data.projectId, chapterId);
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
      || this.data.preparingChapterId
      || !this.data.chapters.length
    ) {
      return;
    }
    const targets = this.data.allChaptersConfirmed
      ? this.data.chapters
      : this.data.chapters.filter(
        (chapter) => chapter.next_action.kind === "prepare",
      );
    if (!targets.length) {
      return;
    }
    this.setData({
      recheckingAll: true,
      error: "",
      chapters: this.data.chapters.map((item) => chapterView(item, "", true)),
    });
    try {
      for (const chapter of targets) {
        await prepareMemoirBookChapter(this.data.projectId, chapter.chapter_id);
      }
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        recheckingAll: false,
        error: error instanceof Error ? error.message : "章节检查没有完成",
        chapters: this.data.chapters.map((item) => chapterView(item, "")),
      });
    }
  },

  async beginCompletionInterview(chapter: ChapterView) {
    const issue = chapter.active_issue;
    if (!issue) {
      this.setData({ error: "补充问题还没有准备好，请下拉刷新" });
      return;
    }
    if (issue.status === "in_progress" && issue.active_session_id) {
      this.openVoiceChat(issue.active_session_id, chapter.title);
      return;
    }
    if (!readVoiceAuthorizationConsent()) {
      wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
      return;
    }
    this.setData({ startingIssueId: issue.id, error: "" });
    try {
      await syncVoiceAuthorizationConsent();
      const detail = await createMemoirVoiceSession({
        guidance_mode: "guided_interview",
        interview_type: "ai_interview",
        session_goal: "book_completion",
        current_completion_issue_id: issue.id,
        provider: "volc_streaming_asr",
        input_format: "pcm_s16le_16000_mono",
        metadata_json: {
          entry: "miniprogram_book_completion",
          chapter_id: chapter.chapter_id,
          completion_issue_id: issue.id,
        },
      }, { projectId: this.data.projectId });
      trackAuditAction(
        "voice_chat.start",
        {
          guidance_mode: "guided_interview",
          interview_type: "ai_interview",
          session_goal: "book_completion",
          entry: "book_completion",
        },
        { projectId: this.data.projectId, voiceSessionId: detail.session.id },
      );
      this.openVoiceChat(detail.session.id, chapter.title);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "AI 访谈创建失败",
      });
    } finally {
      this.setData({ startingIssueId: "" });
    }
  },

  confirmCompletionIssue(chapter: ChapterView) {
    const issue = chapter.active_issue;
    if (!issue || this.data.startingIssueId) {
      return;
    }
    wx.showModal({
      title: "请确认这一点",
      content: issue.question,
      confirmText: "确认",
      cancelText: "先不处理",
      success: (result) => {
        if (result.confirm) {
          void this.applyCompletionIssueConfirmation(chapter, issue.id);
        }
      },
    });
  },

  async applyCompletionIssueConfirmation(chapter: ChapterView, issueId: string) {
    if (this.data.startingIssueId) {
      return;
    }
    this.setData({ startingIssueId: issueId, error: "" });
    try {
      await closeMemoirCompletionIssue(this.data.projectId, issueId, {
        outcome: "confirmed",
        note: "用户已确认当前处理方向",
      });
      await prepareMemoirBookChapter(this.data.projectId, chapter.chapter_id);
      await this.loadOverview(true);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "确认失败",
      });
    } finally {
      this.setData({ startingIssueId: "" });
    }
  },

  openVoiceChat(voiceSessionId: string, chapterTitle: string) {
    wx.navigateTo({
      url: pageUrl("/pages/voice-chat/index", {
        voiceSessionId,
        mode: "guided_interview",
        interviewType: "ai_interview",
        sessionGoal: "book_completion",
        projectTitle: chapterTitle || this.data.projectTitle,
      }),
    });
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
