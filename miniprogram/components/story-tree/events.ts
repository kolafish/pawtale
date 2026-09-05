export interface StoryTreeChapterEvent {
  detail: {
    chapterId: string;
    sectionId?: string;
  };
}

export interface StoryTreeMediaEvent {
  detail: {
    /** Empty for material that has not been assigned to a chapter. */
    chapterId: string;
  };
}

export interface StoryTreeHandle {
  focusGrowth(chapterId: string, sectionId?: string): void;
}

export interface StoryTreeFocusEvent { detail: { top: number } }
