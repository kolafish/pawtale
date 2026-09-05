import { datasetString, type PageDatasetEvent } from "../../services/page-input";
import type { StoryTreeChapterEvent, StoryTreeMediaEvent } from "./events";
import { emptyStoryViewData } from "./story-view-data";

type TreeTapEvent = PageDatasetEvent<{
  id?: string;
  chapterId?: string;
  sectionId?: string;
}>;

Component({
  properties: {
    story: { type: Object, value: emptyStoryViewData() },
    largeText: { type: Boolean, value: false },
  },

  methods: {
    toggleChapter(event: TreeTapEvent) {
      const chapterId = datasetString(event, "id");
      if (chapterId) this.triggerEvent("togglechapter", { chapterId } satisfies StoryTreeChapterEvent["detail"]);
    },

    readChapter(event: TreeTapEvent) {
      const chapterId = datasetString(event, "id");
      if (chapterId) this.triggerEvent("readchapter", { chapterId } satisfies StoryTreeChapterEvent["detail"]);
    },

    openChapter(event: TreeTapEvent) {
      const chapterId = datasetString(event, "chapterId") || datasetString(event, "id");
      if (!chapterId) return;
      const sectionId = datasetString(event, "sectionId");
      this.triggerEvent("openchapter", { chapterId, sectionId } satisfies StoryTreeChapterEvent["detail"]);
    },

    openMedia(event: TreeTapEvent) {
      const chapterId = datasetString(event, "chapterId") || datasetString(event, "id");
      this.triggerEvent("openmedia", { chapterId } satisfies StoryTreeMediaEvent["detail"]);
    },

    openBook() {
      this.triggerEvent("openbook");
    },

    openTranscripts() {
      this.triggerEvent("opentranscripts");
    },

    openChapterManager() {
      this.triggerEvent("managechapters");
    },
  },
});
