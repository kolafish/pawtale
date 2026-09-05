import { accessibilityPageClass } from "../../services/accessibility-settings";
import { writeBookWorkspaceNavigationTarget } from "../../services/book-workspace-navigation";
import type { MemoirDraftProgress } from "../../domain/memoir-snapshots";
import { getMemoirStorySnapshot, retryMemoirDraftProgress } from "../../services/memoir-snapshot-api";
import { PollingTimer } from "../../services/polling-timer";
import {
  acknowledgeMemoirAutomationEvent,
} from "../../services/memoir-voice-session-api";
import {
  CHAPTER_WORKSPACE_PAGE_URL,
  isAuthError,
  navigateToBook,
  navigateToLogin,
  navigateToTranscripts,
} from "../../services/navigation";
import { definePage, pageRuntime } from "../../services/page-definition";
import {
  storyProgressEvents,
  storyProgressTitle,
  storyGrowthTargets,
  syncStoryTabRedDot,
} from "../../services/story-growth-notice";
import {
  datasetString,
  pageUrl,
  type PageDatasetEvent,
} from "../../services/page-input";
import { storyViewData } from "./story-view-adapter";
import {
  emptyStoryViewData,
  highlightStoryGrowth,
  toggleStoryChapter,
  type StoryViewData,
} from "./story-view-data";

interface StoryDataset {
  chapterId?: string;
  id?: string;
  sectionId?: string;
}

type DatasetEvent = PageDatasetEvent<StoryDataset>;

interface StoryPageData extends StoryViewData {
  pageClass: string;
  loading: boolean;
  error: string;
  needsLogin: boolean;
  projectId: string;
  growthNoticeText: string;
  growthNoticeTitle: string;
  growthEventIds: string[];
  dismissingGrowth: boolean;
  progress: MemoirDraftProgress | null;
  bookReadable: boolean;
  retrying: boolean;
}

const STORY_PAGE_CLASS = "story-page ui-page-shell ui-accessible-page";

definePage<StoryPageData>()({
  loadSequence: 0,
  pageVisible: false,
  progressTimer: pageRuntime(() => new PollingTimer(3000)),

  data: {
    pageClass: STORY_PAGE_CLASS,
    loading: true,
    error: "",
    needsLogin: false,
    projectId: "",
    growthNoticeText: "",
    growthNoticeTitle: "",
    growthEventIds: [],
    dismissingGrowth: false,
    progress: null,
    bookReadable: false,
    retrying: false,
    ...emptyStoryViewData(),
  },

  async onShow() {
    this.pageVisible = true;
    this.setData({ pageClass: accessibilityPageClass(STORY_PAGE_CLASS) });
    await this.loadStory();
  },

  onHide() {
    this.pageVisible = false;
    this.loadSequence += 1;
    this.progressTimer.cancel();
  },

  onUnload() {
    this.onHide();
  },

  async loadStory(background = false) {
    const loadSequence = ++this.loadSequence;
    this.progressTimer.cancel();
    if (!background) this.setData({ loading: true, error: "" });
    try {
      const snapshot = await getMemoirStorySnapshot();
      if (loadSequence !== this.loadSequence) {
        return;
      }
      const projectId = snapshot.identity.active_project?.id || "";
      if (!projectId) {
        syncStoryTabRedDot([]);
        this.setData({
          ...emptyStoryViewData(),
          loading: false,
          needsLogin: false,
          projectId: "",
          growthNoticeText: "",
          progress: null,
          bookReadable: false,
        });
        return;
      }
      const previousExpansion = new Map(
        (projectId === this.data.projectId ? this.data.chapters : []).map((chapter) => [chapter.id, chapter.expanded]),
      );
      const viewData = storyViewData(snapshot);
      const growthEvents = storyProgressEvents(snapshot.automation_events?.items || []);
      const growthEvent = growthEvents[0];
      const growthTargets = growthEvents.map(storyGrowthTargets);
      const restoredChapters = viewData.chapters.map((chapter) => (
        previousExpansion.has(chapter.id)
          ? { ...chapter, expanded: previousExpansion.get(chapter.id) || false }
          : chapter
      ));
      this.setData({
        ...viewData,
        chapters: highlightStoryGrowth(
          restoredChapters,
          growthTargets.flatMap((item) => item.chapterIds),
          growthTargets.flatMap((item) => item.sectionIds),
        ),
        loading: false,
        needsLogin: false,
        projectId,
        growthNoticeText: growthEvents.map((event) => event.summary_text).join("\n"),
        growthNoticeTitle: growthEvents.length > 1 ? `${growthEvents.length} 项书稿新进展` : growthEvent ? storyProgressTitle(growthEvent) : "",
        growthEventIds: growthEvents.map((event) => event.id),
        progress: snapshot.progress,
        bookReadable: snapshot.book_readable,
        error: "",
      });
      this.scheduleProgressPolling();
      syncStoryTabRedDot(growthEvents);
    } catch (error) {
      if (loadSequence !== this.loadSequence) {
        return;
      }
      if (isAuthError(error)) {
        syncStoryTabRedDot([]);
        this.setData({
          ...emptyStoryViewData(),
          loading: false,
          needsLogin: true,
          projectId: "",
          growthNoticeText: "",
          progress: null,
          bookReadable: false,
        });
        return;
      }
      this.setData({
        loading: false,
        error: background ? "进度暂未刷新，后台会继续处理" : error instanceof Error ? error.message : "故事页面加载失败",
      });
      this.scheduleProgressPolling();
    }
  },

  scheduleProgressPolling() {
    if (this.pageVisible && this.data.progress?.status === "processing") {
      this.progressTimer.schedule(() => { void this.loadStory(true); });
    }
  },

  async dismissGrowthNotice() {
    const projectId = this.data.projectId;
    const eventIds = this.data.growthEventIds;
    if (!projectId || !eventIds.length || this.data.dismissingGrowth) return;
    this.setData({ dismissingGrowth: true });
    try {
      await Promise.all(eventIds.map((id) => acknowledgeMemoirAutomationEvent(projectId, id)));
      if (this.pageVisible && this.data.projectId === projectId) await this.loadStory(true);
    } catch (error) {
      if (this.pageVisible && this.data.projectId === projectId) {
        this.setData({ error: error instanceof Error ? error.message : "提醒确认失败，请重试" });
      }
    } finally {
      this.setData({ dismissingGrowth: false });
    }
  },

  async retryDraft() {
    const projectId = this.data.projectId;
    if (!projectId || this.data.retrying || !this.data.progress?.can_retry) return;
    this.setData({ retrying: true, error: "" });
    try {
      const progress = await retryMemoirDraftProgress(projectId);
      if (this.pageVisible && projectId === this.data.projectId) {
        this.setData({ progress });
        await this.loadStory(true);
      }
    } catch (error) {
      if (this.pageVisible && projectId === this.data.projectId) {
        this.setData({ error: error instanceof Error ? error.message : "继续整理失败" });
      }
    } finally {
      this.setData({ retrying: false });
    }
  },

  toggleChapter(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    if (!chapterId) {
      return;
    }
    this.setData({
      chapters: toggleStoryChapter(this.data.chapters, chapterId),
    });
  },

  openBook() {
    if (this.data.bookReadable) {
      wx.navigateTo({ url: pageUrl("/pages/book-reader/index", { projectId: this.data.projectId }) });
    } else {
      navigateToBook();
    }
  },

  readChapter(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    if (!this.data.projectId || !this.data.chapters.some((chapter) => chapter.id === chapterId && chapter.readable)) return;
    wx.navigateTo({ url: pageUrl("/pages/book-reader/index", { projectId: this.data.projectId, chapterId }) });
  },

  openChapterManager() {
    writeBookWorkspaceNavigationTarget("chapters");
    navigateToBook();
  },

  openReviewIssues() {
    if (!this.data.projectId) return;
    wx.navigateTo({ url: pageUrl("/pages/book-completion/index", { projectId: this.data.projectId, projectTitle: this.data.memoir.title }) });
  },

  openTranscripts() {
    navigateToTranscripts();
  },

  openChapter(event: DatasetEvent) {
    const chapterId = (
      datasetString(event, "chapterId") || datasetString(event, "id")
    );
    if (!this.data.projectId || !chapterId) {
      return;
    }
    const sectionId = datasetString(event, "sectionId");
    wx.navigateTo({
      url: pageUrl(CHAPTER_WORKSPACE_PAGE_URL, {
        projectId: this.data.projectId,
        chapterId,
        sectionId,
      }),
    });
  },

  openMedia(event: DatasetEvent) {
    if (!this.data.projectId) {
      return;
    }
    const chapterId = (
      datasetString(event, "chapterId") || datasetString(event, "id")
    );
    wx.navigateTo({
      url: pageUrl("/pages/image-assets/index", {
        projectId: this.data.projectId,
        scope: chapterId ? "chapter" : "unassigned",
        chapterId,
      }),
    });
  },

  openLogin() {
    navigateToLogin();
  },

  createProject() {
    wx.navigateTo({ url: "/pages/project-edit/index" });
  },
});
