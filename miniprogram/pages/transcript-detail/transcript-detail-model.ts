import type { MemoirBookPlan } from "../../domain/memoir-book";
import type {
  MemoirMaterialOfflineASRState,
  MemoirVoiceTranscriptItem,
} from "../../domain/memoir-transcript";
import type { MemoirSectionMaterialPlacementRequest } from "../../services/memoir-voice-api";
import { materialOfflineASRView } from "../../services/material-offline-asr-view";
import { formatPlaybackClock } from "../../services/time";
import {
  plainTranscriptSegments,
  transcriptAudioText,
  transcriptBookInclusionText,
  transcriptCreatedAtText,
  transcriptMaterialChapterText,
  transcriptMaterialSectionText,
  transcriptTitleText,
  transcriptVoiceEditStateView,
  type TranscriptTextSegment,
  type TranscriptVoiceEditPhase,
  type TranscriptVoiceEditPreviewView,
} from "../../services/transcript-view-model";
import type { TranscriptShareVideoData } from "../../services/transcript-share-video";
import type { TranscriptMaterialImageTaskState } from "../../services/transcript-material-image";

export interface MaterialAssignmentOption {
  kind: "existing_section" | "create_section" | "create_chapter";
  sectionId?: string;
  chapterId?: string;
  label: string;
}

export interface TranscriptDetailItemView extends MemoirVoiceTranscriptItem {
  titleText: string;
  editingText: string;
  editingCharCount: number;
  createdAtText: string;
  audioText: string;
  recordMetaText: string;
  transcriptionNoticeText: string;
  displayCountText: string;
  rawCountText: string;
  bookStatusClass: string;
  canRecoverTranscription: boolean;
  bookInclusionText: string;
  chapterLocationText: string;
  sectionLocationText: string;
  isIncludedInBook: boolean;
  canPlayAudio: boolean;
  canPlayAnswerAudio: boolean;
  canPlayQuestionAudio: boolean;
  canConvertQuestionToAnswer: boolean;
  questionTitleText: string;
  questionCanExpand: boolean;
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

export interface TranscriptDetailData
  extends TranscriptShareVideoData, TranscriptMaterialImageTaskState {
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
  questionExpanded: boolean;
  moreActionsExpanded: boolean;
  editReferenceExpanded: boolean;
  voiceEditRecording: boolean;
  voiceEditLoading: boolean;
  voiceEditSaving: boolean;
  voiceEditInstructionText: string;
  voiceEditPhase: TranscriptVoiceEditPhase;
  voiceEditStatusText: string;
  voiceEditStatusDetail: string;
  voiceEditStatusClass: string;
  voiceEditStatusVisible: boolean;
  voiceEditActionText: string;
  voiceEditPreview: TranscriptVoiceEditPreviewView | null;
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

type VoiceEditStateData = Pick<
  TranscriptDetailData,
  | "voiceEditPhase"
  | "voiceEditStatusText"
  | "voiceEditStatusDetail"
  | "voiceEditStatusClass"
  | "voiceEditStatusVisible"
  | "voiceEditActionText"
>;

export function voiceEditStateData(
  phase: TranscriptVoiceEditPhase,
  statusDetail = "",
): VoiceEditStateData {
  const state = transcriptVoiceEditStateView(phase, statusDetail);
  return {
    voiceEditPhase: state.phase,
    voiceEditStatusText: state.statusText,
    voiceEditStatusDetail: state.statusDetail,
    voiceEditStatusClass: state.statusClass,
    voiceEditStatusVisible: state.statusVisible,
    voiceEditActionText: state.actionText,
  };
}

const COLLAPSIBLE_QUESTION_CHAR_COUNT = 72;
export const VOICE_EDIT_MAX_RECORDING_MS = 30000;
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

export function shareVideoFileName(item: TranscriptDetailItemView | null): string {
  const title = (item?.titleText || item?.summary_title || "回忆录片段")
    .replace(/[\\/:*?"<>|]/g, "")
    .trim();
  const shortTitle = Array.from(title).slice(0, 24).join("");
  return `${shortTitle || "回忆录片段"}.mp4`;
}

export function toViewItem(
  item: MemoirVoiceTranscriptItem,
  previous?: TranscriptDetailItemView | null,
): TranscriptDetailItemView {
  const canRecoverTranscription = Boolean(
    item.can_recover_transcription || item.transcription_status === "pending_transcription",
  );
  const editingText = item.transcript_edited || item.transcript_raw;
  const isIncludedInBook = !canRecoverTranscription && Boolean(item.is_included_in_book || item.included_chapters?.length);
  const createdAtText = transcriptCreatedAtText(item);
  const audioText = transcriptAudioText(item);
  const questionText = item.question_text.trim();
  const recoveryText = item.summary_text || "这段录音已经保存，但还没有识别成文字。";
  const offlineAsrState = item.offline_asr || DEFAULT_OFFLINE_ASR_STATE;
  const offlineAsrView = materialOfflineASRView(offlineAsrState);
  return {
    ...item,
    offline_asr: offlineAsrState,
    titleText: transcriptTitleText(item),
    editingText: previous?.isEditing ? previous.editingText : editingText,
    editingCharCount: (previous?.isEditing ? previous.editingText : editingText).trim().length,
    createdAtText,
    audioText,
    recordMetaText: [createdAtText, audioText].filter(Boolean).join(" · "),
    transcriptionNoticeText: canRecoverTranscription ? "音频已保存，等待转写" : "",
    displayCountText: canRecoverTranscription ? "待转写" : `${item.display_char_count} 字`,
    rawCountText: canRecoverTranscription ? "待转写" : `${item.raw_char_count} 字`,
    bookStatusClass: canRecoverTranscription
      ? "pending"
      : (isIncludedInBook ? "included" : ""),
    canRecoverTranscription,
    bookInclusionText: canRecoverTranscription
      ? "等待转写后再入章"
      : transcriptBookInclusionText(item),
    chapterLocationText: canRecoverTranscription
      ? "等待转写后再归入章节"
      : transcriptMaterialChapterText(item),
    sectionLocationText: canRecoverTranscription
      ? "等待转写后再归入小节"
      : transcriptMaterialSectionText(item),
    isIncludedInBook,
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
    questionSegments: plainTranscriptSegments(item.question_text),
    rawSegments: plainTranscriptSegments(canRecoverTranscription ? "等待恢复转写后显示原始识别文字。" : item.transcript_raw),
    displaySegments: plainTranscriptSegments(canRecoverTranscription ? recoveryText : item.display_text),
    changed: previous?.isEditing
      ? previous.editingText.trim() !== editingText.trim()
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
    changed: editingText.trim() !== (item.transcript_edited || item.transcript_raw).trim(),
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

export function confirmDelete(item: TranscriptDetailItemView): Promise<boolean> {
  return new Promise((resolve) => {
    const preview = item.titleText || item.display_text || item.transcript_raw || "这条讲述记录";
    wx.showModal({
      title: "删除讲述记录",
      content: `确定删除“${preview.slice(0, 24)}${preview.length > 24 ? "..." : ""}”？删除后，这条原始讲述会从素材库移除；如果已放入章节，相关章节会标记为需重新确认，已生成正文不会自动改写。`,
      confirmText: "删除",
      confirmColor: "#b64545",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
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
      confirmColor: "#a84f43",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}
