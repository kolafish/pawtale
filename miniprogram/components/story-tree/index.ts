import { datasetString, type PageDatasetEvent } from "../../services/page-input";
import type { StoryTreeChapterEvent } from "./events";
import { emptyStoryViewData } from "./story-view-data";

type TreeTapEvent = PageDatasetEvent<{ id?: string }>;

Component({
  options: { multipleSlots: true },

  properties: {
    story: { type: Object, value: emptyStoryViewData() },
    largeText: { type: Boolean, value: false },
    canOpenBook: { type: Boolean, value: false },
    canSwitchProject: { type: Boolean, value: false },
    profileText: { type: String, value: "" },
    canVoiceEdit: { type: Boolean, value: false },
    showBookHint: { type: Boolean, value: false },
    demoMode: { type: Boolean, value: false },
  },

  data: { showAllChapters: false },

  methods: {
    focusGrowth(chapterId: string) {
      const measure = () => {
        this.createSelectorQuery().select(`#story-chapter-${chapterId}`).boundingClientRect(rect => {
          if (rect) this.triggerEvent("focusgrowth", { top: rect.top });
        }).exec();
      };
      const chapters = Object.values(
        (this.properties.story as ReturnType<typeof emptyStoryViewData>).chapters || {},
      );
      const targetExists = chapters.some(chapter => chapter.id === chapterId);
      if (targetExists && !this.data.showAllChapters) {
        this.setData({ showAllChapters: true }, measure);
        return;
      }
      measure();
    },

    toggleAllChapters() {
      this.setData({ showAllChapters: !this.data.showAllChapters });
    },

    openProjects() {
      if (this.properties.canSwitchProject) this.triggerEvent("openprojects");
    },

    openProfile() {
      this.triggerEvent("openprofile");
    },

    openChapter(event: TreeTapEvent) {
      const chapterId = datasetString(event, "id");
      if (this.properties.demoMode) {
        const chapters = Object.values(
          (this.properties.story as ReturnType<typeof emptyStoryViewData>).chapters || {},
        );
        if (!chapters.some(chapter => chapter.id === chapterId && chapter.readable)) return;
      }
      if (chapterId) this.triggerEvent("openchapter", { chapterId } satisfies StoryTreeChapterEvent["detail"]);
    },

    openBook() {
      if (this.properties.canOpenBook) this.triggerEvent("openbook");
    },

    openTranscripts() {
      this.triggerEvent("opentranscripts");
    },

    openVoiceEdit() {
      this.triggerEvent("voiceedit");
    },
  },
});
