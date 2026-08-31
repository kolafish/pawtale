import { accessibilityPageClass } from "../../services/accessibility-settings";
import { definePage } from "../../services/page-definition";
import {
  datasetString,
  type PageDatasetEvent,
} from "../../services/page-input";
import {
  storyPreviewData,
  togglePreviewChapter,
  type StoryViewData,
} from "./story-view-data";

interface StoryDataset {
  id?: string;
  action?: string;
}

type DatasetEvent = PageDatasetEvent<StoryDataset>;

interface StoryPageData extends StoryViewData {
  pageClass: string;
}

const STORY_PAGE_CLASS = "story-page ui-page-shell ui-accessible-page";

definePage<StoryPageData>()({
  data: {
    pageClass: STORY_PAGE_CLASS,
    ...storyPreviewData(),
  },

  onShow() {
    this.setData({ pageClass: accessibilityPageClass(STORY_PAGE_CLASS) });
  },

  toggleChapter(event: DatasetEvent) {
    const chapterId = datasetString(event, "id");
    if (!chapterId) {
      return;
    }
    this.setData({
      chapters: togglePreviewChapter(this.data.chapters, chapterId),
    });
  },

  showPreviewNotice(event: DatasetEvent) {
    const action = datasetString(event, "action") || "这项功能";
    wx.showToast({
      title: `${action}将在接入数据后开放`,
      icon: "none",
    });
  },
});
