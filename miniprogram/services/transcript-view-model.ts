import type {
  MemoirVoiceTranscriptItem,
  MemoirVoiceTranscriptVoiceEditPreviewResponse,
} from "../domain/voice-chat";
import { formatBeijingDateTimeShort } from "./time";

const MAX_INLINE_DIFF_PRODUCT = 400000;

export interface TranscriptTextSegment {
  text: string;
  highlight: boolean;
}

export interface TranscriptVoiceEditChangeView {
  scopeLabel: string;
  before: string;
  after: string;
  reason: string;
}

export type TranscriptVoiceEditPhase =
  | "idle"
  | "listening"
  | "transcribing"
  | "generating"
  | "review"
  | "clarify"
  | "saving"
  | "error";

export interface TranscriptVoiceEditStateView {
  phase: TranscriptVoiceEditPhase;
  statusText: string;
  statusDetail: string;
  statusClass: string;
  statusVisible: boolean;
  actionText: string;
}

export type TranscriptVoiceEditDiffKind = "equal" | "delete" | "insert";

export interface TranscriptVoiceEditDiffSegment {
  text: string;
  kind: TranscriptVoiceEditDiffKind;
}

export interface TranscriptVoiceEditPreviewView {
  action: MemoirVoiceTranscriptVoiceEditPreviewResponse["action"];
  currentText: string;
  editedText: string;
  replyText: string;
  confidenceText: string;
  changes: TranscriptVoiceEditChangeView[];
  diffSegments: TranscriptVoiceEditDiffSegment[];
  canConfirm: boolean;
}

const VOICE_EDIT_STATE_COPY: Record<
  TranscriptVoiceEditPhase,
  Omit<TranscriptVoiceEditStateView, "phase">
> = {
  idle: {
    statusText: "可修改",
    statusDetail: "",
    statusClass: "idle",
    statusVisible: false,
    actionText: "说修改意见",
  },
  listening: {
    statusText: "正在听",
    statusDetail: "请直接说要改的词、句子或片段，说完后再点一次结束。",
    statusClass: "listening",
    statusVisible: true,
    actionText: "结束并生成预览",
  },
  transcribing: {
    statusText: "正在识别",
    statusDetail: "正在把录音识别成修改意见。",
    statusClass: "working",
    statusVisible: true,
    actionText: "识别修改意见中",
  },
  generating: {
    statusText: "正在生成预览",
    statusDetail: "正在定位原文，并核对修改是否只发生在指定位置。",
    statusClass: "working",
    statusVisible: true,
    actionText: "生成修改预览中",
  },
  review: {
    statusText: "等待确认",
    statusDetail: "修改预览已生成，确认后才会保存到素材。",
    statusClass: "ready",
    statusVisible: true,
    actionText: "重新说修改意见",
  },
  clarify: {
    statusText: "需要再说清楚",
    statusDetail: "请说出原词、完整句子或可以定位的具体片段。",
    statusClass: "warning",
    statusVisible: true,
    actionText: "重新说修改意见",
  },
  saving: {
    statusText: "正在保存",
    statusDetail: "正在把确认后的修改保存到素材。",
    statusClass: "working",
    statusVisible: true,
    actionText: "正在保存",
  },
  error: {
    statusText: "处理失败",
    statusDetail: "修改没有保存，可以重新说一次。",
    statusClass: "error",
    statusVisible: true,
    actionText: "重新说修改意见",
  },
};

export function transcriptVoiceEditStateView(
  phase: TranscriptVoiceEditPhase,
  statusDetail = "",
): TranscriptVoiceEditStateView {
  const state = VOICE_EDIT_STATE_COPY[phase];
  return {
    phase,
    ...state,
    statusDetail: statusDetail.trim() || state.statusDetail,
  };
}

function voiceEditScopeLabel(scope: string | undefined): string {
  if (scope === "term") {
    return "名词替换";
  }
  if (scope === "sentence") {
    return "句子修改";
  }
  return "片段改写";
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

function pushDiffSegment(
  segments: TranscriptVoiceEditDiffSegment[],
  kind: TranscriptVoiceEditDiffKind,
  text: string,
) {
  if (!text) {
    return;
  }
  const last = segments[segments.length - 1];
  if (last?.kind === kind) {
    last.text += text;
    return;
  }
  segments.push({ kind, text });
}

function editedTextFromDiffSegments(segments: TranscriptVoiceEditDiffSegment[]): string {
  return segments
    .filter((segment) => segment.kind !== "delete")
    .map((segment) => segment.text)
    .join("");
}

function diffSegmentsFromChangeList(
  currentText: string,
  editedText: string,
  changes: TranscriptVoiceEditChangeView[],
): TranscriptVoiceEditDiffSegment[] | null {
  if (!changes.length) {
    return null;
  }
  const segments: TranscriptVoiceEditDiffSegment[] = [];
  let cursor = 0;
  for (const change of changes) {
    const before = change.before || "";
    const after = change.after || "";
    if (!before && !after) {
      continue;
    }
    if (!before) {
      return null;
    }
    const index = currentText.indexOf(before, cursor);
    if (index < 0) {
      return null;
    }
    pushDiffSegment(segments, "equal", currentText.slice(cursor, index));
    pushDiffSegment(segments, "delete", before);
    pushDiffSegment(segments, "insert", after);
    cursor = index + before.length;
  }
  pushDiffSegment(segments, "equal", currentText.slice(cursor));
  return editedTextFromDiffSegments(segments) === editedText ? segments : null;
}

function textDiffSegments(currentText: string, editedText: string): TranscriptVoiceEditDiffSegment[] {
  const left = Array.from(currentText);
  const right = Array.from(editedText);
  const segments: TranscriptVoiceEditDiffSegment[] = [];
  let prefixLength = 0;
  while (
    prefixLength < left.length
    && prefixLength < right.length
    && left[prefixLength] === right[prefixLength]
  ) {
    prefixLength += 1;
  }
  let suffixLength = 0;
  while (
    suffixLength < left.length - prefixLength
    && suffixLength < right.length - prefixLength
    && left[left.length - suffixLength - 1] === right[right.length - suffixLength - 1]
  ) {
    suffixLength += 1;
  }
  pushDiffSegment(segments, "equal", left.slice(0, prefixLength).join(""));
  const leftMiddle = left.slice(prefixLength, left.length - suffixLength);
  const rightMiddle = right.slice(prefixLength, right.length - suffixLength);
  if (!leftMiddle.length) {
    pushDiffSegment(segments, "insert", rightMiddle.join(""));
  } else if (!rightMiddle.length) {
    pushDiffSegment(segments, "delete", leftMiddle.join(""));
  } else if (leftMiddle.length * rightMiddle.length > MAX_INLINE_DIFF_PRODUCT) {
    pushDiffSegment(segments, "delete", leftMiddle.join(""));
    pushDiffSegment(segments, "insert", rightMiddle.join(""));
  } else {
    const width = rightMiddle.length + 1;
    const table = new Uint32Array((leftMiddle.length + 1) * width);
    for (let leftIndex = leftMiddle.length - 1; leftIndex >= 0; leftIndex -= 1) {
      for (let rightIndex = rightMiddle.length - 1; rightIndex >= 0; rightIndex -= 1) {
        const current = leftIndex * width + rightIndex;
        table[current] = leftMiddle[leftIndex] === rightMiddle[rightIndex]
          ? table[(leftIndex + 1) * width + rightIndex + 1] + 1
          : Math.max(table[(leftIndex + 1) * width + rightIndex], table[leftIndex * width + rightIndex + 1]);
      }
    }
    let leftIndex = 0;
    let rightIndex = 0;
    while (leftIndex < leftMiddle.length && rightIndex < rightMiddle.length) {
      if (leftMiddle[leftIndex] === rightMiddle[rightIndex]) {
        pushDiffSegment(segments, "equal", leftMiddle[leftIndex]);
        leftIndex += 1;
        rightIndex += 1;
      } else if (
        table[(leftIndex + 1) * width + rightIndex] >= table[leftIndex * width + rightIndex + 1]
      ) {
        pushDiffSegment(segments, "delete", leftMiddle[leftIndex]);
        leftIndex += 1;
      } else {
        pushDiffSegment(segments, "insert", rightMiddle[rightIndex]);
        rightIndex += 1;
      }
    }
    pushDiffSegment(segments, "delete", leftMiddle.slice(leftIndex).join(""));
    pushDiffSegment(segments, "insert", rightMiddle.slice(rightIndex).join(""));
  }
  pushDiffSegment(segments, "equal", suffixLength ? left.slice(left.length - suffixLength).join("") : "");
  return segments;
}

export function inlineTranscriptVoiceEditDiffSegments(
  currentText: string,
  editedText: string,
  changes: TranscriptVoiceEditChangeView[] = [],
): TranscriptVoiceEditDiffSegment[] {
  if (currentText === editedText) {
    return currentText ? [{ text: currentText, kind: "equal" }] : [];
  }
  const changeListSegments = diffSegmentsFromChangeList(currentText, editedText, changes);
  return changeListSegments || textDiffSegments(currentText, editedText);
}

export function transcriptVoiceEditPreviewView(
  response: MemoirVoiceTranscriptVoiceEditPreviewResponse,
): TranscriptVoiceEditPreviewView {
  const confidence = Number.isFinite(response.confidence)
    ? Math.round(Math.max(0, Math.min(1, response.confidence)) * 100)
    : 0;
  const changes = (response.changes || []).map((item) => ({
    scopeLabel: voiceEditScopeLabel(item.scope),
    before: item.before,
    after: item.after,
    reason: item.reason,
  }));
  return {
    action: response.action,
    currentText: response.current_text,
    editedText: response.edited_text,
    replyText: response.reply_text,
    confidenceText: `${confidence}%`,
    changes,
    diffSegments: inlineTranscriptVoiceEditDiffSegments(response.current_text, response.edited_text, changes),
    canConfirm: Boolean(response.action === "revise_transcript"
      && response.edited_text.trim()
      && response.edited_text.trim() !== response.current_text.trim()),
  };
}
