import type {
  MemoirBookChapterDraft,
  MemoirBookPlanChapter,
  MemoirChapterSectionSummary,
  MemoirImageAsset,
} from "../../domain/memoir-book";
import type { MemoirBookSnapshot } from "../../domain/memoir-snapshots";
import type { MemoirVoiceProgressResponse } from "../../domain/memoir-voice-session";
import { formatBeijingDateTimeShort, timestampMs } from "../../services/time";
import type {
  StoryChapterTone,
  StoryChapterView,
  StorySectionView,
  StoryViewData,
} from "./story-view-data";

const CHAPTER_TONES: StoryChapterTone[] = ["clay", "amber", "sage", "blue", "stone"];

interface ImageCounts {
  chapterImages: Record<string, number>;
  sectionImages: Record<string, number>;
}

function uniqueSourceCount(chapter: MemoirBookPlanChapter): number {
  return new Set((chapter.source_refs || []).map((item) => item.utterance_id).filter(Boolean)).size;
}

function activeSections(chapter: MemoirBookPlanChapter): MemoirChapterSectionSummary[] {
  return (chapter.sections || [])
    .filter((section) => section.status === "active")
    .sort((left, right) => left.sort_order - right.sort_order);
}

function increment(counts: Record<string, number>, id: string): void {
  counts[id] = (counts[id] || 0) + 1;
}

function imageMediaCounts(
  chapters: MemoirBookPlanChapter[],
  assets: MemoirImageAsset[],
): ImageCounts {
  const chapterImages: Record<string, number> = {};
  const sectionImages: Record<string, number> = {};
  const sectionByUtteranceId = new Map<string, string>();
  const chapterByUtteranceId = new Map<string, string>();
  const chapterIds = new Set(chapters.map((chapter) => chapter.id));

  chapters.forEach((chapter) => {
    chapter.source_refs.forEach((source) => {
      if (source.utterance_id && !chapterByUtteranceId.has(source.utterance_id)) {
        chapterByUtteranceId.set(source.utterance_id, chapter.id);
      }
    });
    activeSections(chapter).forEach((section) => {
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
      increment(sectionImages, sectionId);
      return;
    }
    const chapterId = (
      (utteranceId ? chapterByUtteranceId.get(utteranceId) : "")
      || asset.source_chapter_id
      || ""
    );
    if (chapterIds.has(chapterId)) {
      increment(chapterImages, chapterId);
    }
  });

  return { chapterImages, sectionImages };
}

function chapterOutcome(
  chapter: MemoirBookPlanChapter,
  draft: MemoirBookChapterDraft | null,
): string {
  if (chapter.include_in_book === false) {
    return "暂不入书";
  }
  if (chapter.pending_source_count || chapter.removed_source_count) {
    return "需更新";
  }
  if (draft?.latest_version && draft.status === "confirmed") {
    return "已成稿";
  }
  if (draft?.latest_version) {
    return "已有正文";
  }
  return "待成稿";
}

function sectionView(
  section: MemoirChapterSectionSummary,
  media: ImageCounts,
): StorySectionView {
  return {
    id: section.id,
    title: section.title || "未命名小节",
    materialText: `${section.material_count || 0} 段讲述`,
    imageCount: media.sectionImages[section.id] || 0,
    videoCount: 0,
  };
}

function chapterView(
  chapter: MemoirBookPlanChapter,
  draft: MemoirBookChapterDraft | null,
  index: number,
  media: ImageCounts,
): StoryChapterView {
  const sections = activeSections(chapter).map((section) => sectionView(section, media));
  return {
    id: chapter.id,
    title: chapter.title || "未命名章节",
    timeRangeText: "",
    summaryText: `${sections.length} 小节 · ${uniqueSourceCount(chapter)} 段讲述`,
    outcomeText: chapterOutcome(chapter, draft),
    imageCount: media.chapterImages[chapter.id] || 0,
    videoCount: 0,
    tone: CHAPTER_TONES[index % CHAPTER_TONES.length],
    expanded: index === 0,
    sections,
  };
}

function confirmedIncludedChapterCount(
  chapters: MemoirBookPlanChapter[],
  drafts: MemoirBookSnapshot["drafts_by_chapter_id"],
): number {
  return chapters.filter((chapter) => {
    const draft = drafts[chapter.id];
    return chapter.include_in_book !== false
      && draft?.status === "confirmed"
      && Boolean(draft.latest_version)
      && !chapter.pending_source_count
      && !chapter.removed_source_count;
  }).length;
}

function progressDetail(
  chapters: MemoirBookPlanChapter[],
  includedCount: number,
  confirmedCount: number,
  drafts: MemoirBookSnapshot["drafts_by_chapter_id"],
): string {
  if (!includedCount) {
    return "等待整理";
  }
  if (confirmedCount === includedCount) {
    return "已经成稿";
  }
  if (confirmedCount) {
    return `${confirmedCount} 章已成稿`;
  }
  const draftedCount = chapters.filter((chapter) => (
    chapter.include_in_book !== false
    && Boolean(drafts[chapter.id]?.latest_version)
  )).length;
  return draftedCount ? `${draftedCount} 章已有正文` : "等待成稿";
}

function bookCharCount(snapshot: MemoirBookSnapshot): number {
  const outputContent = snapshot.output?.latest_version?.content.trim() || "";
  if (outputContent) {
    return outputContent.length;
  }
  return Object.values(snapshot.drafts_by_chapter_id).reduce(
    (total, draft) => total + (draft?.latest_version?.content.trim().length || 0),
    0,
  );
}

function charCountText(count: number): string {
  if (!count) {
    return "还没有正文";
  }
  if (count < 10_000) {
    return `${count} 字`;
  }
  return `约 ${(count / 10_000).toFixed(1)} 万字`;
}

function latestUpdatedAt(snapshot: MemoirBookSnapshot): string {
  const candidates = [
    snapshot.identity.active_project?.updated_at,
    snapshot.plan?.updated_at,
    snapshot.output?.updated_at,
    ...Object.values(snapshot.drafts_by_chapter_id).map((draft) => draft?.updated_at),
    ...snapshot.image_assets.items.map((asset) => asset.updated_at),
  ].filter((value): value is string => Boolean(value));
  return candidates.sort((left, right) => timestampMs(right) - timestampMs(left))[0] || "";
}

function progressCounts(
  progress: MemoirVoiceProgressResponse | null,
  chapters: MemoirBookPlanChapter[],
): {
  total: number;
  pending: number;
} {
  const total = progress?.snapshot?.valid_utterance_count ?? progress?.utterance_count ?? 0;
  const assignedIds = new Set(
    chapters
      .flatMap((chapter) => chapter.source_refs.map((source) => source.utterance_id))
      .filter(Boolean),
  );
  const pending = progress?.snapshot?.pending_utterance_count
    ?? Math.max(0, total - assignedIds.size);
  return {
    total: Math.max(0, total),
    pending: Math.max(0, Math.min(total, pending)),
  };
}

export function storyViewData(
  snapshot: MemoirBookSnapshot,
  progress: MemoirVoiceProgressResponse | null,
): StoryViewData {
  const project = snapshot.identity.active_project;
  const planChapters = [...(snapshot.plan?.chapters || [])]
    .sort((left, right) => left.sort_order - right.sort_order);
  const media = imageMediaCounts(planChapters, snapshot.image_assets.items || []);
  const chapters = planChapters.map((chapter, index) => chapterView(
    chapter,
    snapshot.drafts_by_chapter_id[chapter.id] || null,
    index,
    media,
  ));
  const includedCount = planChapters.filter((chapter) => chapter.include_in_book !== false).length;
  const confirmedCount = confirmedIncludedChapterCount(
    planChapters,
    snapshot.drafts_by_chapter_id,
  );
  const sectionCount = chapters.reduce((total, chapter) => total + chapter.sections.length, 0);
  const materialCounts = progressCounts(progress, planChapters);
  const createdAt = project?.created_at ? formatBeijingDateTimeShort(project.created_at) : "";
  const updatedAt = formatBeijingDateTimeShort(latestUpdatedAt(snapshot));

  return {
    memoir: {
      title: project?.title || "我的故事",
      createdText: createdAt ? `创建于 ${createdAt}` : "",
      updatedText: updatedAt ? `更新于 ${updatedAt}` : "",
      progressLabel: "成书进度",
      progressValue: includedCount ? `${confirmedCount}/${includedCount} 章` : "0 章",
      progressDetail: progressDetail(
        planChapters,
        includedCount,
        confirmedCount,
        snapshot.drafts_by_chapter_id,
      ),
    },
    overview: {
      title: "人生总览",
      summaryText: `${chapters.length} 章 · ${sectionCount} 小节 · ${materialCounts.total} 段讲述`,
      outcomeText: charCountText(bookCharCount(snapshot)),
    },
    chapters,
    unassignedMaterialText: materialCounts.pending
      ? `还有 ${materialCounts.pending} 段讲述等待整理`
      : "所有讲述都已归入章节",
  };
}
