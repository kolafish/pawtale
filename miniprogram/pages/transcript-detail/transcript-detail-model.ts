import type { MemoirBookPlan } from "../../domain/memoir-book";
import type {
  MemoirMaterialOfflineASRState,
  MemoirVoiceTranscriptItem,
} from "../../domain/memoir-transcript";
import type {
  MemoirSectionMaterialPlacementRequest,
} from "../../services/memoir-chapter-workspace-api";
import { materialOfflineASRView } from "../../services/material-offline-asr-view";
import { formatPlaybackClock } from "../../services/time";
import {
  plainTranscriptSegments,
  transcriptAudioText,
  transcriptRecoveryText,
  transcriptCreatedAtText,
  transcriptTitleText,
  type TranscriptTextSegment,
} from "../../services/transcript-view-model";

export interface MaterialAssignmentOption {
  kind: "existing_section" | "create_section" | "create_chapter";
  sectionId?: string;
  chapterId?: string;
  label: string;
}

export interface TranscriptDetailItemView extends MemoirVoiceTranscriptItem {
  titleText: string;
  savedText: string;
  editingText: string;
  editingCharCount: number;
  createdAtText: string;
  audioText: string;
  recordMetaText: string;
  transcriptionNoticeText: string;
  recoveryButtonText: string;
  heroStatusText: string;
  heroStatusClass: string;
  displayCountText: string;
  rawCountText: string;
  canRecoverTranscription: boolean;
  locationTitleText: string;
  showLocationPath: boolean;
  chapterLocationText: string;
  sectionLocationText: string;
  canPlayAudio: boolean;
  canPlayAnswerAudio: boolean;
  canPlayQuestionAudio: boolean;
  canConvertQuestionToAnswer: boolean;
  questionTitleText: string;
  questionCanExpand: boolean;
  rawCanExpand: boolean;
  questionSegments: TranscriptTextSegment[];
  rawSegments: TranscriptTextSegment[];
  displaySegments: TranscriptTextSegment[];
  changed: boolean;
  isEditing: boolean;
  showOfflineAsr: boolean;
  offlineAsrStatusText: string;
  offlineAsrStatusClass: string;
  offlineAsrActionText: string;
  offlineAsrMethodText: string;
  offlineAsrDetailText: string;
  offlineAsrErrorText: string;
  offlineAsrActive: boolean;
  canStartOfflineAsr: boolean;
}

export interface TranscriptDetailData {
  pageClass: string;
  loading: boolean;
  needsLogin: boolean;
  saving: boolean;
  deleting: boolean;
  convertingQuestionToAnswer: boolean;
  recovering: boolean;
  offlineAsrStarting: boolean;
  playing: boolean;
  playingAudioKind: TranscriptDetailAudioKind | "";
  answerPlaybackVisible: boolean;
  answerPlaybackPaused: boolean;
  answerPlaybackStatusText: string;
  answerPlaybackCurrentText: string;
  answerPlaybackDurationText: string;
  answerPlaybackProgressStyle: string;
  answerPlaybackCanTogglePause: boolean;
  answerPlaybackCanStop: boolean;
  answerPlaybackStopText: string;
  error: string;
  utteranceId: string;
  item: TranscriptDetailItemView | null;
  rawExpanded: boolean;
  questionExpanded: boolean;
  moreActionsExpanded: boolean;
  editReferenceExpanded: boolean;
  editPreviewVersion: "before" | "after";
  voiceEditGatewayUrl: string;
  voiceEditLoading: boolean;
  voiceEditInstructionText: string;
  voiceEditMessage: string;
  assignmentEditorOpen: boolean;
  assignmentLoading: boolean;
  assignmentSaving: boolean;
  assignmentError: string;
  assignmentOptions: MaterialAssignmentOption[];
  assignmentIndex: number;
  assignmentText: string;
  assignmentCanSave: boolean;
  assignmentPlacement: MemoirSectionMaterialPlacementRequest | null;
}

const COLLAPSIBLE_QUESTION_CHAR_COUNT = 72;
const COLLAPSIBLE_RAW_CHAR_COUNT = 72;
export const OFFLINE_ASR_POLL_INTERVAL_MS = 2000;
export const OFFLINE_ASR_MAX_POLL_ATTEMPTS = 150;
export type TranscriptDetailAudioKind = "question" | "answer";

const DEFAULT_OFFLINE_ASR_STATE: MemoirMaterialOfflineASRState = {
  status: "idle",
  job_id: null,
  can_start: false,
  blocked_reason: "",
  providers: ["volc", "stepfun"],
  narrator_origin: "",
  dialect_code: "",
  dialect_label: "",
  dialect_strategy: "none",
  dialect_enhancement_enabled: false,
  last_applied_at: null,
  error_message: "",
};

export function positiveSeconds(value: unknown): number {
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
}

export function answerAudioFallbackDuration(item: MemoirVoiceTranscriptItem | null): number {
  if (!item) {
    return 0;
  }
  const durationSeconds = positiveSeconds(item.audio_duration_seconds);
  if (durationSeconds) {
    return durationSeconds;
  }
  if (item.start_ms !== null && item.start_ms !== undefined && item.end_ms !== null && item.end_ms !== undefined) {
    return positiveSeconds((item.end_ms - item.start_ms) / 1000);
  }
  return 0;
}

export function answerPlaybackPatch(
  currentSeconds: number,
  durationSeconds: number,
  statusText: string,
): Pick<
  TranscriptDetailData,
  | "answerPlaybackStatusText"
  | "answerPlaybackCurrentText"
  | "answerPlaybackDurationText"
  | "answerPlaybackProgressStyle"
> {
  const duration = positiveSeconds(durationSeconds);
  const current = Math.max(0, Math.min(positiveSeconds(currentSeconds), duration || positiveSeconds(currentSeconds)));
  const progressPercent = duration ? Math.max(0, Math.min(100, Math.round((current / duration) * 100))) : 0;
  return {
    answerPlaybackStatusText: statusText,
    answerPlaybackCurrentText: formatPlaybackClock(current),
    answerPlaybackDurationText: duration ? formatPlaybackClock(duration) : "时长未知",
    answerPlaybackProgressStyle: `width: ${progressPercent}%;`,
  };
}

export function materialAssignmentOptions(
  plan: MemoirBookPlan,
): MaterialAssignmentOption[] {
  const options = [...(plan.chapters || [])]
    .sort((left, right) => left.sort_order - right.sort_order)
    .flatMap((chapter) => {
      const chapterLabel = [
        `第 ${chapter.sort_order + 1} 章`,
        chapter.title.trim() || "未命名章节",
      ].join(" · ");
      const sectionOptions: MaterialAssignmentOption[] = [
        ...(chapter.sections || []),
      ]
        .filter((section) => (
          section.status === "active"
          && !section.requires_user_assignment
        ))
        .sort((left, right) => left.sort_order - right.sort_order)
        .map((section) => ({
          kind: "existing_section" as const,
          sectionId: section.id,
          label: [
            chapterLabel,
            `小节 ${section.sort_order + 1}`,
            section.title.trim() || "未命名小节",
          ].join(" · "),
        }));
      return [
        ...sectionOptions,
        {
          kind: "create_section" as const,
          chapterId: chapter.id,
          label: `${chapterLabel} · ＋ 新建小节`,
        },
      ];
    });
  options.push({
    kind: "create_chapter",
    label: "＋ 新建章节",
  });
  return options;
}

export function materialAssignmentPatch(
  options: MaterialAssignmentOption[],
  item: TranscriptDetailItemView,
  requestedIndex?: number,
): Pick<
  TranscriptDetailData,
  | "assignmentOptions"
  | "assignmentIndex"
  | "assignmentText"
  | "assignmentCanSave"
  | "assignmentPlacement"
> {
  const matchedIndex = options.findIndex(
    (option) => (
      option.kind === "existing_section"
      && option.sectionId === item.section_assignment?.section_id
    ),
  );
  const assignmentIndex = Math.min(
    Math.max(
      requestedIndex ?? (matchedIndex >= 0 ? matchedIndex : 0),
      0,
    ),
    Math.max(options.length - 1, 0),
  );
  const selected = options[assignmentIndex];
  const placement = (
    selected?.kind === "existing_section"
    && selected.sectionId
  )
    ? { target_section_id: selected.sectionId }
    : null;
  return {
    assignmentOptions: options,
    assignmentIndex,
    assignmentText: selected?.label || "没有可选择的小节",
    assignmentCanSave: Boolean(
      placement
      && selected.sectionId !== item.section_assignment?.section_id,
    ),
    assignmentPlacement: placement,
  };
}

export function shareVideoFileName(title: string | null | undefined): string {
  const safeTitle = (title || "")
    .replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const shortTitle = Array.from(safeTitle).slice(0, 24).join("").trim();
  return `${shortTitle || "回忆录片段"}.mp4`;
}

export function toViewItem(
  item: MemoirVoiceTranscriptItem,
  previous?: TranscriptDetailItemView | null,
): TranscriptDetailItemView {
  const canRecoverTranscription = Boolean(
    item.can_recover_transcription || item.transcription_status === "pending_transcription",
  );
  const recovery = transcriptRecoveryText(item.transcription_status);
  const savedText = item.transcript_edited || item.transcript_raw;
  const assignment = item.section_assignment;
  const includedChapter = [...(item.included_chapters || [])]
    .sort((left, right) => left.sort_order - right.sort_order)[0];
  const hasLocation = !canRecoverTranscription && Boolean(assignment || includedChapter);
  const chapterLocationText = assignment?.chapter_title.trim()
    || includedChapter?.title.trim()
    || "未命名故事";
  const sectionLocationText = assignment
    ? (assignment.requires_user_assignment
      ? "还没有选择具体位置"
      : (assignment.section_title.trim() || "未命名故事"))
    : "";
  const createdAtText = transcriptCreatedAtText(item);
  const audioText = transcriptAudioText(item);
  const questionText = item.question_text.trim();
  const offlineAsrState = item.offline_asr || DEFAULT_OFFLINE_ASR_STATE;
  const offlineAsrView = materialOfflineASRView(offlineAsrState);
  return {
    ...item,
    offline_asr: offlineAsrState,
    titleText: canRecoverTranscription ? "音频已保存" : transcriptTitleText(item),
    savedText,
    editingText: previous?.isEditing ? previous.editingText : savedText,
    editingCharCount: (previous?.isEditing ? previous.editingText : savedText).trim().length,
    createdAtText,
    audioText,
    recordMetaText: [createdAtText, audioText].filter(Boolean).join(" · "),
    transcriptionNoticeText: canRecoverTranscription ? recovery.notice : "",
    recoveryButtonText: recovery.buttonText,
    heroStatusText: canRecoverTranscription ? recovery.statusText : "已整理",
    heroStatusClass: canRecoverTranscription ? "pending" : "ready",
    displayCountText: canRecoverTranscription ? "尚未生成" : `${item.display_char_count} 字`,
    rawCountText: canRecoverTranscription ? "尚未转写" : `${item.raw_char_count} 字`,
    canRecoverTranscription,
    locationTitleText: hasLocation
      ? "已收进故事树"
      : (canRecoverTranscription ? recovery.statusText : "尚未整理"),
    showLocationPath: hasLocation,
    chapterLocationText,
    sectionLocationText,
    canPlayAudio: Boolean(item.audio_file_id || item.question_audio_file_id),
    canPlayAnswerAudio: Boolean(item.audio_file_id),
    canPlayQuestionAudio: Boolean(item.question_audio_file_id),
    canConvertQuestionToAnswer: Boolean(
      !canRecoverTranscription &&
      item.interview_type === "human_interview" &&
      item.chat_message_role === "interviewer" &&
      item.chat_message_id,
    ),
    questionTitleText: item.interview_type === "human_interview" ? "采访者刚才问了" : "小余刚才问了",
    questionCanExpand: Array.from(questionText).length > COLLAPSIBLE_QUESTION_CHAR_COUNT,
    rawCanExpand: Array.from(item.transcript_raw.trim()).length > COLLAPSIBLE_RAW_CHAR_COUNT,
    questionSegments: plainTranscriptSegments(item.question_text),
    rawSegments: plainTranscriptSegments(canRecoverTranscription
      ? "这里会保留尽量忠于原话的识别文字。"
      : item.transcript_raw),
    displaySegments: plainTranscriptSegments(canRecoverTranscription
      ? "小余会帮您整理成适合阅读的文字。"
      : item.display_text),
    changed: previous?.isEditing
      ? previous.editingText.trim() !== savedText.trim()
      : false,
    isEditing: Boolean(previous?.isEditing),
    showOfflineAsr: Boolean(item.audio_file_id && item.chat_message_role !== "interviewer"),
    offlineAsrStatusText: offlineAsrView.statusText,
    offlineAsrStatusClass: offlineAsrView.statusClass,
    offlineAsrActionText: offlineAsrView.actionText,
    offlineAsrMethodText: offlineAsrView.methodText,
    offlineAsrDetailText: offlineAsrView.detailText,
    offlineAsrErrorText: offlineAsrView.errorText,
    offlineAsrActive: offlineAsrView.active,
    canStartOfflineAsr: offlineAsrView.canStart,
  };
}

export function changedItem(item: TranscriptDetailItemView, editingText: string): TranscriptDetailItemView {
  return {
    ...item,
    editingText,
    editingCharCount: editingText.trim().length,
    changed: editingText.trim() !== item.savedText.trim(),
    isEditing: true,
  };
}

export function syncEditUnloadAlert(item: TranscriptDetailItemView | null): void {
  if (typeof wx === "undefined") {
    return;
  }
  if (item?.isEditing && item.changed) {
    wx.enableAlertBeforeUnload?.({
      message: "尚未保存的文字修改会丢失，确定离开吗？",
    });
    return;
  }
  wx.disableAlertBeforeUnload?.();
}

export function confirmConvertQuestionToAnswer(item: TranscriptDetailItemView): Promise<boolean> {
  return new Promise((resolve) => {
    const preview = item.titleText || item.display_text || item.transcript_raw || "这段内容";
    wx.showModal({
      title: "改成回答？",
      content: `“${preview.slice(0, 24)}${preview.length > 24 ? "..." : ""}”现在被系统当作采访者提问。改成回答后，会作为讲述素材保留，之后可以放入章节；原音频和识别文字不会丢。`,
      confirmText: "改成回答",
      confirmColor: "#5a7454",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}

export function confirmDiscardEdit(): Promise<boolean> {
  return new Promise((resolve) => {
    wx.showModal({
      title: "放弃这次修改？",
      content: "尚未保存的文字修改会丢失，原来的成书文字不会变化。",
      confirmText: "放弃修改",
      confirmColor: "#b64545",
      cancelText: "继续编辑",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}

export function confirmOfflineAsr(item: TranscriptDetailItemView): Promise<boolean> {
  const state = item.offline_asr;
  const dialectLine = state.dialect_enhancement_enabled && state.dialect_label
    ? `系统会根据籍贯“${state.narrator_origin}”启用${state.dialect_label}加强识别。`
    : (state.narrator_origin
      ? `籍贯“${state.narrator_origin}”没有匹配专门方言规则，本次只做双模型口音校对。`
      : "项目尚未填写籍贯，本次只做双模型口音校对。");
  return new Promise((resolve) => {
    wx.showModal({
      title: item.offline_asr.status === "succeeded" ? "再次识别这条素材？" : "离线加强识别？",
      content: `将使用火山和 StepFun 重新识别原音频，再由 AI 校对。${dialectLine}`,
      confirmText: "开始识别",
      confirmColor: "#b65d49",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}
