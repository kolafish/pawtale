import { accessibilityPageClass } from "../../services/accessibility-settings";
import { writeBookWorkspaceNavigationTarget } from "../../services/book-workspace-navigation";
import { getMemoirBookSnapshot } from "../../services/memoir-snapshot-api";
import { getMemoirVoiceProgress } from "../../services/memoir-voice-session-api";
import {
  CHAPTER_WORKSPACE_PAGE_URL,
  isAuthError,
  navigateToBook,
  navigateToLogin,
  navigateToTranscripts,
} from "../../services/navigation";
import { definePage } from "../../services/page-definition";
import {
  datasetString,
  pageUrl,
  type PageDatasetEvent,
} from "../../services/page-input";
import { storyViewData } from "./story-view-adapter";
import {
  emptyStoryViewData,
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
        this.setData({
          ...emptyStoryViewData(),
          loading: false,
          needsLogin: false,
          projectId: "",
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
      this.setData({
        ...viewData,
        chapters: viewData.chapters.map((chapter) => (
          previousExpansion.has(chapter.id)
            ? { ...chapter, expanded: previousExpansion.get(chapter.id) || false }
            : chapter
        )),
        loading: false,
        needsLogin: false,
        projectId,
      });
    } catch (error) {
      if (loadSequence !== this.loadSequence) {
        return;
      }
      if (isAuthError(error)) {
        this.setData({
          ...emptyStoryViewData(),
          loading: false,
          needsLogin: true,
          projectId: "",
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

  openLogin() {
    navigateToLogin();
  },

  createProject() {
    wx.navigateTo({ url: "/pages/project-edit/index" });
  },
});
