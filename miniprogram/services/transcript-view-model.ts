import type { MemoirVoiceTranscriptItem } from "../domain/memoir-transcript";
import { formatBeijingDateTimeShort } from "./time";

export interface TranscriptTextSegment {
  text: string;
  highlight: boolean;
}

export function formatTranscriptDurationSeconds(value: number): string {
  const seconds = Math.max(0, Math.round(value));
  if (seconds < 60) {
    return `${seconds} 秒`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return remainingSeconds ? `${minutes} 分 ${remainingSeconds} 秒` : `${minutes} 分`;
}

export function transcriptDurationText(item: MemoirVoiceTranscriptItem): string {
  if (item.audio_duration_seconds !== null && item.audio_duration_seconds !== undefined) {
    return formatTranscriptDurationSeconds(item.audio_duration_seconds);
  }
  if (item.start_ms !== null && item.start_ms !== undefined && item.end_ms !== null && item.end_ms !== undefined) {
    return formatTranscriptDurationSeconds(Math.max(0, Math.round((item.end_ms - item.start_ms) / 1000)));
  }
  return "";
}

export function transcriptAudioText(
  item: MemoirVoiceTranscriptItem,
  options: { includeDurationLabel?: boolean } = {},
): string {
  const duration = transcriptDurationText(item);
  if (!duration) {
    return "时长未知";
  }
  return options.includeDurationLabel ? `时长 ${duration}` : duration;
}

export function transcriptCreatedAtText(item: Pick<MemoirVoiceTranscriptItem, "created_at">): string {
  return formatBeijingDateTimeShort(item.created_at) || "讲述记录";
}

export function transcriptTitleText(item: MemoirVoiceTranscriptItem): string {
  const summaryTitle = (item.summary_title || "").trim();
  if (summaryTitle) {
    return summaryTitle;
  }
  const displayText = (item.display_text || item.transcript_edited || item.transcript_raw || "").trim();
  return displayText ? displayText.slice(0, 32) : "讲述记录";
}

export function transcriptBookInclusionText(item: MemoirVoiceTranscriptItem): string {
  const assignment = item.section_assignment;
  if (assignment) {
    const chapter = `第 ${assignment.chapter_sort_order + 1} 章：${assignment.chapter_title.trim() || "未命名章节"}`;
    if (assignment.requires_user_assignment) {
      return `${chapter} · 待归类素材`;
    }
    const section = `小节 ${assignment.section_sort_order + 1}：${assignment.section_title.trim() || "未命名小节"}`;
    const included = Boolean(
      item.is_included_in_book
      || item.included_chapters?.length,
    );
    return `${included ? "已放入" : "已整理到"}${chapter} · ${section}${included ? "" : "（暂不收入本书）"}`;
  }
  const chapters = [...(item.included_chapters || [])]
    .sort((left, right) => left.sort_order - right.sort_order)
    .map((chapter) => ({
      title: chapter.title.trim() || "未命名章节",
      chapterNumber: chapter.sort_order + 1,
      includeInBook: chapter.include_in_book !== false,
    }));
  if (!chapters.length) {
    return "尚未入章";
  }
  const includedChapters = chapters.filter((chapter) => chapter.includeInBook);
  if (includedChapters.length) {
    const first = includedChapters[0];
    const suffix = includedChapters.length > 1 ? `等 ${includedChapters.length} 章` : "";
    return `已放入第 ${first.chapterNumber} 章：${first.title}${suffix}`;
  }
  const first = chapters[0];
  const suffix = chapters.length > 1 ? `等 ${chapters.length} 章` : "";
  return `已整理到第 ${first.chapterNumber} 章：${first.title}${suffix}（不收入本书）`;
}

export function transcriptMaterialChapterText(
  item: MemoirVoiceTranscriptItem,
): string {
  const assignment = item.section_assignment;
  if (assignment) {
    return `第 ${assignment.chapter_sort_order + 1} 章：${assignment.chapter_title.trim() || "未命名章节"}`;
  }
  const chapter = [...(item.included_chapters || [])]
    .sort((left, right) => left.sort_order - right.sort_order)[0];
  return chapter
    ? `第 ${chapter.sort_order + 1} 章：${chapter.title.trim() || "未命名章节"}`
    : "尚未归入章节";
}

export function transcriptMaterialSectionText(
  item: MemoirVoiceTranscriptItem,
): string {
  const assignment = item.section_assignment;
  if (!assignment) {
    return "尚未归入小节";
  }
  if (assignment.requires_user_assignment) {
    return "待归类素材（请选择小节）";
  }
  return `小节 ${assignment.section_sort_order + 1}：${assignment.section_title.trim() || "未命名小节"}`;
}

export function plainTranscriptSegments(text: string): TranscriptTextSegment[] {
  return text ? [{ text, highlight: false }] : [];
}
