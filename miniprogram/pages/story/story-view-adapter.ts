import type {
  MemoirBookChapterDraft,
  MemoirBookPlanChapter,
  MemoirChapterSectionSummary,
} from "../../domain/memoir-book";
import type { MemoirBookSnapshot } from "../../domain/memoir-snapshots";
import type { MemoirVoiceProgressResponse } from "../../domain/memoir-voice-session";
import { formatBeijingDateTimeShort, timestampMs } from "../../services/time";
import type {
  StoryChapterView,
  StoryChapterState,
  StorySectionView,
  StoryViewData,
} from "./story-view-data";
import {
  buildStoryMediaIndex,
  type StoryMediaIndex,
} from "./story-media-index";

function uniqueSourceCount(chapter: MemoirBookPlanChapter): number {
  return new Set((chapter.source_refs || []).map((item) => item.utterance_id).filter(Boolean)).size;
}

function activeSections(chapter: MemoirBookPlanChapter): MemoirChapterSectionSummary[] {
  return (chapter.sections || [])
    .filter((section) => section.status === "active")
    .sort((left, right) => left.sort_order - right.sort_order);
}

function chapterOutcome(
  chapter: MemoirBookPlanChapter,
  draft: MemoirBookChapterDraft | null,
): { state: StoryChapterState; text: string } {
  if (chapter.include_in_book === false) {
    return { state: "muted", text: "暂不入书" };
  }
  if (chapter.pending_source_count || chapter.removed_source_count) {
    return { state: "attention", text: "有新内容" };
  }
  if (draft?.latest_version && draft.status === "confirmed") {
    return { state: "settled", text: "已整理" };
  }
  if (draft?.latest_version) {
    return { state: "draft", text: "已有正文" };
  }
  return { state: "muted", text: "待整理" };
}

function sectionView(
  section: MemoirChapterSectionSummary,
  media: StoryMediaIndex,
): StorySectionView {
  return {
    id: section.id,
    title: section.title || "未命名小节",
    materialText: `${section.material_count || 0} 段讲述`,
    imageCount: media.sectionImages[section.id]?.length || 0,
    videoCount: media.sectionVideos[section.id]?.length || 0,
  };
}

function chapterView(
  chapter: MemoirBookPlanChapter,
  draft: MemoirBookChapterDraft | null,
  expanded: boolean,
  media: StoryMediaIndex,
): StoryChapterView {
  const sections = activeSections(chapter).map((section) => sectionView(section, media));
  const outcome = chapterOutcome(chapter, draft);
  return {
    id: chapter.id,
    title: chapter.title || "未命名章节",
    timeRangeText: "",
    summaryText: `${sections.length} 小节 · ${uniqueSourceCount(chapter)} 段讲述`,
    outcomeText: outcome.text,
    imageCount: media.chapterImages[chapter.id]?.length || 0,
    videoCount: media.chapterVideos[chapter.id]?.length || 0,
    state: outcome.state,
    expanded,
    sections,
  };
}

function progressDetail(
  chapters: MemoirBookPlanChapter[],
  drafts: MemoirBookSnapshot["drafts_by_chapter_id"],
): string {
  const included = chapters.filter((chapter) => chapter.include_in_book !== false);
  if (!included.length) {
    return "开始整理";
  }
  const outcomes = included.map((chapter) => chapterOutcome(
    chapter,
    drafts[chapter.id] || null,
  ));
  if (outcomes.every((outcome) => outcome.state === "settled")) {
    return "已整理";
  }
  return outcomes.some((outcome) => outcome.state !== "muted")
    ? "继续整理"
    : "等待整理";
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

function unassignedMaterialCopy(counts: { total: number; pending: number }) {
  if (counts.pending) {
    return {
      unassignedMaterialTitle: "待归入故事的讲述",
      unassignedMaterialText: `已经记录了 ${counts.pending} 段讲述`,
      unassignedMaterialDetail: "小余会把它们整理成人生故事",
    };
  }
  if (counts.total) {
    return {
      unassignedMaterialTitle: "全部讲述",
      unassignedMaterialText: "所有讲述都已归入故事",
      unassignedMaterialDetail: "继续讲下新的回忆，它们会在这里等待整理",
    };
  }
  return {
    unassignedMaterialTitle: "从一段讲述开始",
    unassignedMaterialText: "还没有讲述",
    unassignedMaterialDetail: "讲下第一段回忆后，它会从这里长成故事",
  };
}

export function storyViewData(
  snapshot: MemoirBookSnapshot,
  progress: MemoirVoiceProgressResponse | null,
): StoryViewData {
  const project = snapshot.identity.active_project;
  const planChapters = [...(snapshot.plan?.chapters || [])]
    .sort((left, right) => left.sort_order - right.sort_order);
  const media = buildStoryMediaIndex(
    planChapters,
    snapshot.image_assets.items || [],
    snapshot.share_video_counts || [],
  );
  const chapters = planChapters.map((chapter, index) => chapterView(
    chapter,
    snapshot.drafts_by_chapter_id[chapter.id] || null,
    index === 0,
    media,
  ));
  const includedCount = planChapters.filter((chapter) => chapter.include_in_book !== false).length;
  const sectionCount = chapters.reduce((total, chapter) => total + chapter.sections.length, 0);
  const materialCounts = progressCounts(progress, planChapters);
  const createdAt = project?.created_at ? formatBeijingDateTimeShort(project.created_at) : "";
  const updatedAt = formatBeijingDateTimeShort(latestUpdatedAt(snapshot));

  return {
    memoir: {
      title: project?.title || "我的故事",
      createdText: createdAt ? `创建于 ${createdAt}` : "",
      updatedText: updatedAt ? `更新于 ${updatedAt}` : "",
      progressLabel: "书稿",
      progressValue: includedCount ? `${includedCount} 章` : "尚未生成",
      progressDetail: progressDetail(
        planChapters,
        snapshot.drafts_by_chapter_id,
      ),
    },
    overview: {
      title: "人生总览",
      summaryText: `${chapters.length} 章 · ${sectionCount} 小节 · ${materialCounts.total} 段讲述`,
      outcomeText: charCountText(bookCharCount(snapshot)),
    },
    chapters,
    ...unassignedMaterialCopy(materialCounts),
    unassignedImageCount: media.unassignedImages.length,
    unassignedVideoCount: media.unassignedVideos.length,
  };
}
