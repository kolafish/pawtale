import type {
  MemoirBookPlanChapter,
  MemoirImageAsset,
} from "../../domain/memoir-book";
import type { MemoirShareVideoCount } from "../../domain/memoir-snapshots";

export interface StoryVideoAsset {
  id: string;
  utteranceId: string;
}

export interface StoryMediaIndex {
  chapterImages: Record<string, MemoirImageAsset[]>;
  sectionImages: Record<string, MemoirImageAsset[]>;
  chapterVideos: Record<string, StoryVideoAsset[]>;
  sectionVideos: Record<string, StoryVideoAsset[]>;
  unassignedImages: MemoirImageAsset[];
  unassignedVideos: StoryVideoAsset[];
}

function append<T>(records: Record<string, T[]>, id: string, item: T): void {
  (records[id] ||= []).push(item);
}

export function buildStoryMediaIndex(
  chapters: MemoirBookPlanChapter[],
  assets: MemoirImageAsset[],
  videos: MemoirShareVideoCount[],
): StoryMediaIndex {
  const index: StoryMediaIndex = {
    chapterImages: {},
    sectionImages: {},
    chapterVideos: {},
    sectionVideos: {},
    unassignedImages: [],
    unassignedVideos: [],
  };
  const sectionByUtteranceId = new Map<string, string>();
  const chapterByUtteranceId = new Map<string, string>();
  const chapterIds = new Set(chapters.map((chapter) => chapter.id));

  chapters.forEach((chapter) => {
    chapter.source_refs.forEach((source) => {
      if (source.utterance_id && !chapterByUtteranceId.has(source.utterance_id)) {
        chapterByUtteranceId.set(source.utterance_id, chapter.id);
      }
    });
    chapter.sections
      .filter((section) => section.status === "active")
      .forEach((section) => {
        section.materials.forEach((material) => {
          if (material.utterance_id && !sectionByUtteranceId.has(material.utterance_id)) {
            sectionByUtteranceId.set(material.utterance_id, section.id);
          }
        });
      });
  });

  assets.filter((asset) => asset.status === "ready").forEach((asset) => {
    const utteranceId = asset.source_utterance_id || "";
    const sectionId = utteranceId ? sectionByUtteranceId.get(utteranceId) : "";
    if (sectionId) {
      append(index.sectionImages, sectionId, asset);
      return;
    }
    const chapterId = (
      (utteranceId ? chapterByUtteranceId.get(utteranceId) : "")
      || asset.source_chapter_id
      || ""
    );
    if (chapterIds.has(chapterId)) {
      append(index.chapterImages, chapterId, asset);
      return;
    }
    const placementChapterIds = new Set(
      (asset.placements || [])
        .filter((placement) => placement.enabled && placement.chapter_id)
        .map((placement) => placement.chapter_id || "")
        .filter((placementChapterId) => chapterIds.has(placementChapterId)),
    );
    if (placementChapterIds.size) {
      placementChapterIds.forEach((placementChapterId) => {
        append(index.chapterImages, placementChapterId, asset);
      });
      return;
    }
    index.unassignedImages.push(asset);
  });

  videos.forEach((video) => {
    const items = video.job_ids.map((id) => ({
      id,
      utteranceId: video.utterance_id,
    }));
    const sectionId = sectionByUtteranceId.get(video.utterance_id);
    if (sectionId) {
      items.forEach((item) => append(index.sectionVideos, sectionId, item));
      return;
    }
    const chapterId = chapterByUtteranceId.get(video.utterance_id) || "";
    if (chapterIds.has(chapterId)) {
      items.forEach((item) => append(index.chapterVideos, chapterId, item));
    } else {
      index.unassignedVideos.push(...items);
    }
  });

  return index;
}
