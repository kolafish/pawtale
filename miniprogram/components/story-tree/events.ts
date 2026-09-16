export interface StoryTreeChapterEvent {
  detail: {
    chapterId: string;
  };
}

export interface StoryTreeHandle {
  focusGrowth(chapterId: string): void;
}

export interface StoryTreeFocusEvent { detail: { top: number } }
