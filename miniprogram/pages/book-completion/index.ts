import type {
  MemoirChapterCompletionSummary,
  MemoirBookCompletionOverview,
} from "../../domain/chapter-completion";
import { trackAuditAction } from "../../services/audit-tracker";
import { definePage } from "../../services/page-definition";
import {
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
  preparingChapterId: string;
  startingTaskId: string;
}

interface ChapterDataset {
  chapterId?: string;
}

type ChapterTapEvent = PageDatasetEvent<ChapterDataset>;

function chapterView(
  item: MemoirChapterCompletionSummary,
  preparingChapterId: string,
): ChapterView {
  const stateText = {
    not_started: "等待整理",
    preparing: "正在整理",
    needs_input: "需要您补充",
    draft_ready: "等待通读",
    confirmed: "已经确认",
    failed: "整理未完成",
  }[item.state];
  const busy = preparingChapterId === item.chapter_id || item.state === "preparing";
  return {
    ...item,
    stateText: busy ? "正在整理" : stateText,
    actionText: busy
      ? "正在整理"
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
): Pick<BookCompletionData, "headline" | "detail" | "progressText" | "chapters"> {
  return {
    headline: overview.headline,
    detail: overview.detail,
    progressText: overview.total_chapters
      ? `${overview.completed_chapters} / ${overview.total_chapters} 章已确认`
      : "还没有章节",
    chapters: overview.chapters.map((item) => chapterView(item, preparingChapterId)),
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
    preparingChapterId: "",
    startingTaskId: "",
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
        ...bookCompletionViewPatch(overview, this.data.preparingChapterId),
        loading: false,
        error: "",
      });
      if (overview.chapters.some((item) => item.state === "preparing")) {
        this.schedulePolling();
      } else {
        this.stopPolling();
        if (this.data.preparingChapterId) {
          this.setData({
            preparingChapterId: "",
            chapters: overview.chapters.map((item) => chapterView(item, "")),
          });
        }
      }
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : "全书整理情况加载失败",
      });
    }
  },

  async tapChapter(event: ChapterTapEvent) {
    const chapterId = datasetString(event, "chapterId");
    const chapter = this.data.chapters.find((item) => item.chapter_id === chapterId);
    if (!chapter || chapter.disabled || this.data.preparingChapterId || this.data.startingTaskId) {
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
    wx.navigateTo({
      url: pageUrl("/pages/chapter-workspace/index", {
        projectId: this.data.projectId,
        chapterId: chapter.chapter_id,
      }),
    });
  },

  async prepareChapter(chapterId: string) {
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
        error: error instanceof Error ? error.message : "本章整理失败",
        chapters: this.data.chapters.map((item) => chapterView(item, "")),
      });
    }
  },

  async beginCompletionInterview(chapter: ChapterView) {
    const task = chapter.active_item;
    if (!task) {
      this.setData({ error: "补充问题还没有准备好，请下拉刷新" });
      return;
    }
    if (task.status === "in_progress" && task.active_session_id) {
      this.openVoiceChat(task.active_session_id, chapter.title);
      return;
    }
    if (!readVoiceAuthorizationConsent()) {
      wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
      return;
    }
    this.setData({ startingTaskId: task.id, error: "" });
    try {
      await syncVoiceAuthorizationConsent();
      const detail = await createMemoirVoiceSession({
        guidance_mode: "guided_interview",
        interview_type: "ai_interview",
        session_goal: "book_completion",
        current_completion_item_id: task.id,
        provider: "volc_streaming_asr",
        input_format: "pcm_s16le_16000_mono",
        metadata_json: {
          entry: "miniprogram_book_completion",
          chapter_id: chapter.chapter_id,
          completion_item_id: task.id,
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
      this.setData({ startingTaskId: "" });
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
