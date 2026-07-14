import type {
  MemoirBookCompletionChapter,
  MemoirBookCompletionOverview,
} from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import {
  createMemoirVoiceSession,
  getMemoirBookCompletionOverview,
  prepareMemoirBookCompletionChapter,
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

interface ChapterView extends MemoirBookCompletionChapter {
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
}

interface ChapterDataset {
  chapterId?: string;
}

type ChapterTapEvent = PageDatasetEvent<ChapterDataset>;

function chapterView(item: MemoirBookCompletionChapter, preparingChapterId: string): ChapterView {
  const stateCopy = {
    ready: "可以补充",
    in_progress: "正在补充",
    preparing: "正在整理",
    needs_preparation: "等待整理",
    needs_review: "需要确认",
    complete: "目前完整",
    failed: "整理未完成",
  }[item.state];
  const actionText = {
    ready: "开始讲这一段",
    in_progress: "继续刚才的讲述",
    preparing: "正在整理",
    needs_preparation: "整理这一章",
    needs_review: "查看并确认",
    complete: "暂时不用补",
    failed: "重新整理",
  }[item.state];
  const busy = preparingChapterId === item.chapter_id || item.state === "preparing";
  return {
    ...item,
    stateText: busy ? "正在整理" : stateCopy,
    actionText: busy ? "正在整理" : actionText,
    chapterClass: `chapter-item ${item.state}`,
    disabled: busy || item.state === "complete",
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
      ? `${overview.completed_chapters} / ${overview.total_chapters} 章目前不需要再补`
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
    headline: "先看全书，再选择要补的章节",
    detail: "",
    progressText: "",
    chapters: [],
    preparingChapterId: "",
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
        error: error instanceof Error ? error.message : "全书补全情况加载失败",
      });
    }
  },

  async tapChapter(event: ChapterTapEvent) {
    const chapterId = datasetString(event, "chapterId");
    const chapter = this.data.chapters.find((item) => item.chapter_id === chapterId);
    if (!chapter || chapter.disabled || this.data.preparingChapterId) {
      return;
    }
    if (chapter.state === "ready" && chapter.task_id) {
      await this.startInterview(chapter);
      return;
    }
    if (chapter.state === "in_progress" && chapter.active_session_id) {
      this.openVoiceChat(chapter.active_session_id, chapter.title);
      return;
    }
    if (chapter.state === "needs_review") {
      wx.navigateTo({
        url: pageUrl("/pages/chapter-workspace/index", {
          projectId: this.data.projectId,
          chapterId: chapter.chapter_id,
        }),
      });
      return;
    }
    await this.prepareChapter(chapter.chapter_id);
  },

  async prepareChapter(chapterId: string) {
    this.setData({
      preparingChapterId: chapterId,
      error: "",
      chapters: this.data.chapters.map((item) => chapterView(item, chapterId)),
    });
    try {
      await prepareMemoirBookCompletionChapter(this.data.projectId, chapterId);
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

  async startInterview(chapter: ChapterView) {
    if (!readVoiceAuthorizationConsent()) {
      wx.navigateTo({ url: VOICE_AUTHORIZATION_PAGE_URL });
      return;
    }
    this.setData({ preparingChapterId: chapter.chapter_id, error: "" });
    try {
      await syncVoiceAuthorizationConsent();
      const detail = await createMemoirVoiceSession({
        guidance_mode: "guided_interview",
        interview_type: "ai_interview",
        session_goal: "book_completion",
        current_followup_task_id: chapter.task_id || undefined,
        provider: "volc_streaming_asr",
        input_format: "pcm_s16le_16000_mono",
        metadata_json: {
          entry: "miniprogram_book_completion_overview",
          chapter_id: chapter.chapter_id,
        },
      }, { projectId: this.data.projectId });
      this.openVoiceChat(detail.session.id, chapter.title);
    } catch (error) {
      if (routeAuthError(error)) {
        return;
      }
      this.setData({
        preparingChapterId: "",
        error: error instanceof Error ? error.message : "语音访谈创建失败",
      });
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
