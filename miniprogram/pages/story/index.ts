import { accessibilityPageClass } from "../../services/accessibility-settings";
import { writeBookWorkspaceNavigationTarget } from "../../services/book-workspace-navigation";
import { getMemoirBookSnapshot } from "../../services/memoir-snapshot-api";
import {
  acknowledgeMemoirAutomationEvent,
  getMemoirVoiceProgress,
} from "../../services/memoir-voice-session-api";
import {
  CHAPTER_WORKSPACE_PAGE_URL,
  isAuthError,
  navigateToBook,
  navigateToLogin,
  navigateToTranscripts,
} from "../../services/navigation";
import { definePage } from "../../services/page-definition";
import {
  storyGrowthEvent,
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
}

const STORY_PAGE_CLASS = "story-page ui-page-shell ui-accessible-page";

definePage<StoryPageData>()({
  loadSequence: 0,

  data: {
    pageClass: STORY_PAGE_CLASS,
    loading: true,
    error: "",
    needsLogin: false,
    projectId: "",
    growthNoticeText: "",
    ...emptyStoryViewData(),
  },

  async onShow() {
    this.setData({ pageClass: accessibilityPageClass(STORY_PAGE_CLASS) });
    await this.loadStory();
  },

  async loadStory() {
    const loadSequence = ++this.loadSequence;
    this.setData({ loading: true, error: "" });
    try {
      const snapshot = await getMemoirBookSnapshot();
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
        });
        return;
      }
      const progress = await getMemoirVoiceProgress(projectId);
      if (loadSequence !== this.loadSequence) {
        return;
      }
      const previousExpansion = new Map(
        this.data.chapters.map((chapter) => [chapter.id, chapter.expanded]),
      );
      const viewData = storyViewData(snapshot, progress);
      const growthEvent = storyGrowthEvent(snapshot.automation_events?.items || []);
      const growthTargets = storyGrowthTargets(growthEvent);
      const restoredChapters = viewData.chapters.map((chapter) => (
        previousExpansion.has(chapter.id)
          ? { ...chapter, expanded: previousExpansion.get(chapter.id) || false }
          : chapter
      ));
      this.setData({
        ...viewData,
        chapters: highlightStoryGrowth(
          restoredChapters,
          growthTargets.chapterIds,
          growthTargets.sectionIds,
        ),
        loading: false,
        needsLogin: false,
        projectId,
        growthNoticeText: growthEvent?.summary_text || "",
      });
      syncStoryTabRedDot([]);
      if (growthEvent) {
        void acknowledgeMemoirAutomationEvent(projectId, growthEvent.id).catch((error) => {
          console.warn("acknowledge story growth event failed", error);
        });
      }
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
        });
        return;
      }
      this.setData({
        loading: false,
        error: error instanceof Error ? error.message : "故事页面加载失败",
      });
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
    navigateToBook();
  },

  openChapterManager() {
    writeBookWorkspaceNavigationTarget("chapters");
    navigateToBook();
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
