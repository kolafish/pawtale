import type {
  MemoirBookPlan,
  MemoirShareVideoBackgroundMode,
  MemoirShareVideoBackgroundStyle,
  MemoirShareVideoJob,
  MemoirTranscriptShareVideoContext,
  MemoirTranscriptShareVideoSegment,
  MemoirVoiceTranscriptItem,
} from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import type { ChapterPickerOption } from "../../services/memoir-book-source-actions";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  assignMemoirBookSources,
  createMemoirTranscriptShareVideoJob,
  deleteMemoirVoiceTranscript,
  downloadMemoirShareVideoJob,
  getMemoirBookPlan,
  getMemoirShareVideoJob,
  getMemoirTranscriptShareVideoContext,
  getMemoirVoiceTranscript,
  moveMemoirBookSource,
  previewMemoirVoiceTranscriptVoiceEdit,
  recoverMemoirVoiceTranscript,
  updateMemoirVoiceTranscript,
} from "../../services/memoir-voice-api";
import { AudioPlaybackController } from "../../services/audio-playback";
import {
  isShareFileMessageCancelError,
  shareLocalFileMessage,
} from "../../services/share";
import {
  sourceAssignmentRequest,
  transcriptChapterAssignOptions,
  transcriptChapterMoveOptions,
  transcriptSourceMoveRequest,
} from "../../services/memoir-book-source-actions";
import { ApiError } from "../../services/request";
import {
  isAuthError,
  routeAuthError,
  switchToMe,
} from "../../services/navigation";
import {
  recordMemoirVoiceSearchQuery,
  stopMemoirVoiceSearchRecording,
} from "../../services/memoir-voice-search";
import {
  formatTranscriptDurationSeconds,
  plainTranscriptSegments,
  transcriptAudioText,
  transcriptBookInclusionText,
  transcriptCreatedAtText,
  transcriptTitleText,
  transcriptVoiceEditPreviewView,
  type TranscriptTextSegment,
  type TranscriptVoiceEditPreviewView,
} from "../../services/transcript-view-model";
import {
  datasetNumber,
  datasetString,
  pickerIndex,
  type PageDatasetEvent,
  queryParam,
  type PagePickerEvent,
  type PageQuery,
  type PageTextInputEvent,
} from "../../services/page-input";
import {
  SHARE_VIDEO_BACKGROUND_MODE_OPTIONS,
  SHARE_VIDEO_BACKGROUND_STYLE_OPTIONS,
  readSelectedShareVideoBackgroundMode,
  readSelectedShareVideoBackgroundStyle,
  saveSelectedShareVideoBackgroundMode,
  saveSelectedShareVideoBackgroundStyle,
  shareVideoBackgroundModeLabel,
  shareVideoBackgroundStyleLabel,
} from "../../services/share-video-background-settings";

type TextAreaEvent = PageTextInputEvent;
type PickerEvent = PagePickerEvent;
type ShareVideoSegmentEvent = PageDatasetEvent<{ index?: string | number }>;
type ShareVideoBackgroundEvent = PageDatasetEvent<{ backgroundMode?: string; backgroundStyle?: string }>;

interface TranscriptDetailItemView extends MemoirVoiceTranscriptItem {
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
  isIncludedInBook: boolean;
  canAssignChapterSource: boolean;
  chapterAssignOptions: ChapterPickerOption[];
  canMoveChapterSource: boolean;
  chapterMoveOptions: ChapterPickerOption[];
  canPlayAudio: boolean;
  canPlayAnswerAudio: boolean;
  canPlayQuestionAudio: boolean;
  questionTitleText: string;
  questionCanExpand: boolean;
  questionSegments: TranscriptTextSegment[];
  rawSegments: TranscriptTextSegment[];
  displaySegments: TranscriptTextSegment[];
  changed: boolean;
  isEditing: boolean;
}

interface TranscriptShareVideoSegmentView extends MemoirTranscriptShareVideoSegment {
  arrayIndex: number;
  selected: boolean;
  durationText: string;
  timeRangeText: string;
  className: string;
}

interface TranscriptDetailData {
  loading: boolean;
  needsLogin: boolean;
  saving: boolean;
  deleting: boolean;
  recovering: boolean;
  assigning: boolean;
  moving: boolean;
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
  bookPlan: MemoirBookPlan | null;
  questionExpanded: boolean;
  moreActionsExpanded: boolean;
  voiceEditRecording: boolean;
  voiceEditLoading: boolean;
  voiceEditSaving: boolean;
  voiceEditInstructionText: string;
  voiceEditStatusText: string;
  voiceEditPreview: TranscriptVoiceEditPreviewView | null;
  shareVideoPanelOpen: boolean;
  shareVideoLoadingContext: boolean;
  shareVideoCreating: boolean;
  shareVideoPolling: boolean;
  shareVideoDownloadingPreview: boolean;
  shareVideoSharing: boolean;
  shareVideoError: string;
  shareVideoContext: MemoirTranscriptShareVideoContext | null;
  shareVideoSegments: TranscriptShareVideoSegmentView[];
  shareVideoSelectedStartIndex: number;
  shareVideoSelectedEndIndex: number;
  shareVideoSelectedStartChar: number;
  shareVideoSelectedEndChar: number;
  shareVideoSelectedText: string;
  shareVideoSelectedMetaText: string;
  shareVideoSelectedCanGenerate: boolean;
  shareVideoBackgroundStyle: string;
  shareVideoBackgroundStyleLabel: string;
  shareVideoBackgroundStyleOptions: MemoirShareVideoBackgroundStyle[];
  shareVideoBackgroundMode: string;
  shareVideoBackgroundModeLabel: string;
  shareVideoBackgroundModeOptions: MemoirShareVideoBackgroundMode[];
  shareVideoStatusText: string;
  shareVideoStatusClass: string;
  shareVideoJob: MemoirShareVideoJob | null;
  shareVideoPreviewPath: string;
}

const COLLAPSIBLE_QUESTION_CHAR_COUNT = 72;
const SHARE_VIDEO_POLL_INTERVAL_MS = 1500;
const SHARE_VIDEO_MAX_POLL_FAILURES = 3;
type TranscriptDetailAudioKind = "question" | "answer";

function positiveSeconds(value: unknown): number {
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
}

function answerAudioFallbackDuration(item: MemoirVoiceTranscriptItem | null): number {
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

function playbackClockText(seconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${minutes}:${remainingSeconds.toString().padStart(2, "0")}`;
}

function answerPlaybackPatch(
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
    answerPlaybackCurrentText: playbackClockText(current),
    answerPlaybackDurationText: duration ? playbackClockText(duration) : "时长未知",
    answerPlaybackProgressStyle: `width: ${progressPercent}%;`,
  };
}

function shareVideoClockText(ms: number): string {
  return playbackClockText(Math.floor(Math.max(0, ms) / 1000));
}

function shareVideoRangeText(startMs: number, endMs: number): string {
  return `${shareVideoClockText(startMs)}-${shareVideoClockText(endMs)}`;
}

function sliceShareVideoText(text: string, startChar: number, endChar: number): string {
  return Array.from(text).slice(startChar, endChar).join("");
}

function shareVideoSegmentViews(
  context: MemoirTranscriptShareVideoContext | null,
  selectedStartIndex: number,
  selectedEndIndex: number,
): TranscriptShareVideoSegmentView[] {
  const normalizedStart = Math.min(selectedStartIndex, selectedEndIndex);
  const normalizedEnd = Math.max(selectedStartIndex, selectedEndIndex);
  return (context?.segments || []).map((segment, arrayIndex) => {
    const selected =
      normalizedStart >= 0 &&
      normalizedEnd >= 0 &&
      arrayIndex >= normalizedStart &&
      arrayIndex <= normalizedEnd;
    const classes = [
      selected ? "selected" : "",
      segment.selectable ? "" : "disabled",
    ].filter(Boolean);
    return {
      ...segment,
      arrayIndex,
      selected,
      durationText: formatTranscriptDurationSeconds(Math.max(0, segment.end_ms - segment.start_ms) / 1000),
      timeRangeText: shareVideoRangeText(segment.start_ms, segment.end_ms),
      className: classes.join(" "),
    };
  });
}

function shareVideoSelectionPatch(
  context: MemoirTranscriptShareVideoContext | null,
  selectedStartIndex: number,
  selectedEndIndex: number,
): Pick<
  TranscriptDetailData,
  | "shareVideoSegments"
  | "shareVideoSelectedStartIndex"
  | "shareVideoSelectedEndIndex"
  | "shareVideoSelectedStartChar"
  | "shareVideoSelectedEndChar"
  | "shareVideoSelectedText"
  | "shareVideoSelectedMetaText"
  | "shareVideoSelectedCanGenerate"
  | "shareVideoStatusText"
  | "shareVideoStatusClass"
> {
  const segments = context?.segments || [];
  const hasValidStart = selectedStartIndex >= 0 && selectedStartIndex < segments.length;
  const hasValidEnd = selectedEndIndex >= 0 && selectedEndIndex < segments.length;
  const normalizedStart = hasValidStart && hasValidEnd ? Math.min(selectedStartIndex, selectedEndIndex) : -1;
  const normalizedEnd = hasValidStart && hasValidEnd ? Math.max(selectedStartIndex, selectedEndIndex) : -1;
  const selectedSegments = normalizedStart >= 0 ? segments.slice(normalizedStart, normalizedEnd + 1) : [];
  const first = selectedSegments[0];
  const last = selectedSegments[selectedSegments.length - 1];
  const startChar = first?.start_char ?? 0;
  const endChar = last?.end_char ?? 0;
  const selectedText = context && first && last
    ? sliceShareVideoText(context.transcript_text, startChar, endChar).trim()
    : "";
  const durationMs = first && last ? Math.max(0, last.end_ms - first.start_ms) : 0;
  const allSelectable = selectedSegments.length > 0 && selectedSegments.every((segment) => segment.selectable);
  const minSelectMs = context?.min_select_ms ?? 0;
  const maxSelectMs = context?.max_select_ms ?? Number.MAX_SAFE_INTEGER;
  const selectedCanGenerate =
    Boolean(selectedText) &&
    allSelectable &&
    durationMs >= minSelectMs &&
    durationMs <= maxSelectMs;
  let statusText = "";
  let statusClass = "";
  if (context && !segments.length) {
    statusText = "暂无可截取片段";
    statusClass = "failed";
  } else if (context && !selectedSegments.length) {
    statusText = "选择一段校正文后生成视频";
  } else if (!allSelectable) {
    statusText = "这段文字暂不能截取";
    statusClass = "failed";
  } else if (durationMs < minSelectMs) {
    statusText = `已选 ${formatTranscriptDurationSeconds(durationMs / 1000)}，至少 ${formatTranscriptDurationSeconds(minSelectMs / 1000)}`;
  } else if (durationMs > maxSelectMs) {
    statusText = `已选 ${formatTranscriptDurationSeconds(durationMs / 1000)}，最多 ${formatTranscriptDurationSeconds(maxSelectMs / 1000)}`;
    statusClass = "failed";
  } else if (selectedText) {
    statusText = "可以生成视频";
    statusClass = "ready";
  }
  return {
    shareVideoSegments: shareVideoSegmentViews(context, normalizedStart, normalizedEnd),
    shareVideoSelectedStartIndex: normalizedStart,
    shareVideoSelectedEndIndex: normalizedEnd,
    shareVideoSelectedStartChar: startChar,
    shareVideoSelectedEndChar: endChar,
    shareVideoSelectedText: selectedText,
    shareVideoSelectedMetaText: selectedText
      ? `已选 ${formatTranscriptDurationSeconds(durationMs / 1000)} · ${Array.from(selectedText).length} 字`
      : "",
    shareVideoSelectedCanGenerate: selectedCanGenerate,
    shareVideoStatusText: statusText,
    shareVideoStatusClass: statusClass,
  };
}

function shareVideoBackgroundOptions(
  context: MemoirTranscriptShareVideoContext | null,
): MemoirShareVideoBackgroundStyle[] {
  return context?.background_styles?.length
    ? context.background_styles
    : SHARE_VIDEO_BACKGROUND_STYLE_OPTIONS;
}

function shareVideoBackgroundModeOptions(
  context: MemoirTranscriptShareVideoContext | null,
): MemoirShareVideoBackgroundMode[] {
  return context?.background_modes?.length
    ? context.background_modes
    : SHARE_VIDEO_BACKGROUND_MODE_OPTIONS;
}

function shareVideoBackgroundPatch(
  context: MemoirTranscriptShareVideoContext | null,
  preferredStyle: string = readSelectedShareVideoBackgroundStyle(),
  preferredMode: string = readSelectedShareVideoBackgroundMode(),
): Pick<
  TranscriptDetailData,
  | "shareVideoBackgroundStyle"
  | "shareVideoBackgroundStyleLabel"
  | "shareVideoBackgroundStyleOptions"
  | "shareVideoBackgroundMode"
  | "shareVideoBackgroundModeLabel"
  | "shareVideoBackgroundModeOptions"
> {
  const options = shareVideoBackgroundOptions(context);
  const modeOptions = shareVideoBackgroundModeOptions(context);
  const fallbackStyle = context?.default_background_style || SHARE_VIDEO_BACKGROUND_STYLE_OPTIONS[0].value;
  const fallbackMode = context?.default_background_mode || SHARE_VIDEO_BACKGROUND_MODE_OPTIONS[0].value;
  const optionValues = new Set(options.map((option) => option.value));
  const modeOptionValues = new Set(modeOptions.map((option) => option.value));
  const normalizedStyle = optionValues.has(preferredStyle)
    ? preferredStyle
    : (optionValues.has(fallbackStyle) ? fallbackStyle : options[0]?.value || fallbackStyle);
  const normalizedMode = modeOptionValues.has(preferredMode)
    ? preferredMode
    : (modeOptionValues.has(fallbackMode) ? fallbackMode : modeOptions[0]?.value || fallbackMode);
  const label = options.find((option) => option.value === normalizedStyle)?.label
    || shareVideoBackgroundStyleLabel(normalizedStyle);
  const modeLabel = modeOptions.find((option) => option.value === normalizedMode)?.label
    || shareVideoBackgroundModeLabel(normalizedMode);
  return {
    shareVideoBackgroundStyle: normalizedStyle,
    shareVideoBackgroundStyleLabel: label,
    shareVideoBackgroundStyleOptions: options,
    shareVideoBackgroundMode: normalizedMode,
    shareVideoBackgroundModeLabel: modeLabel,
    shareVideoBackgroundModeOptions: modeOptions,
  };
}

function shareVideoJobStatusText(job: MemoirShareVideoJob): string {
  if (job.status === "succeeded") {
    if (job.background_status === "failed" || job.background_status === "fallback") {
      return "视频已生成，已使用经典背景";
    }
    if (job.background_effective_mode === "smart_multi" && (job.background_scene_count || 0) > 1) {
      if (job.background_status === "partial") {
        return `视频已生成，已使用 ${job.background_scene_count} 张背景图（部分沿用）`;
      }
      return `视频已生成，已使用 ${job.background_scene_count} 张背景图`;
    }
    return "视频已生成";
  }
  if (job.status === "failed") {
    return job.error_message || "视频生成失败";
  }
  if (job.status === "running") {
    if (job.background_status === "planning") {
      return "正在分析口述画面";
    }
    if (job.background_status === "generating") {
      return (job.background_scene_count || 0) > 1 ? "正在生成多张背景图" : "正在生成背景图";
    }
    if (job.background_status === "ready" || job.background_status === "partial") {
      return "正在合成视频";
    }
    if (job.background_status === "pending") {
      return "正在生成背景图";
    }
    return "正在生成视频";
  }
  return "视频任务已创建，等待生成";
}

function isTransientShareVideoPollError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.statusCode === 0 || error.statusCode >= 500;
  }
  return true;
}

function shareVideoFileName(item: TranscriptDetailItemView | null): string {
  const title = (item?.titleText || item?.summary_title || "回忆录片段")
    .replace(/[\\/:*?"<>|]/g, "")
    .trim();
  const shortTitle = Array.from(title).slice(0, 24).join("");
  return `${shortTitle || "回忆录片段"}.mp4`;
}

async function loadTranscriptBookPlan(projectId: string): Promise<MemoirBookPlan | null> {
  try {
    return await getMemoirBookPlan(projectId);
  } catch (error) {
    if (error instanceof ApiError && error.statusCode === 404) {
      return null;
    }
    throw error;
  }
}

function toViewItem(
  item: MemoirVoiceTranscriptItem,
  bookPlan: MemoirBookPlan | null,
  previous?: TranscriptDetailItemView | null,
): TranscriptDetailItemView {
  const canRecoverTranscription = Boolean(
    item.can_recover_transcription || item.transcription_status === "pending_transcription",
  );
  const editingText = item.transcript_edited || item.transcript_raw;
  const isIncludedInBook = !canRecoverTranscription && Boolean(item.is_included_in_book || item.included_chapters?.length);
  const chapterAssignOptions = canRecoverTranscription ? [] : transcriptChapterAssignOptions(item, bookPlan);
  const chapterMoveOptions = canRecoverTranscription ? [] : transcriptChapterMoveOptions(item, bookPlan);
  const createdAtText = transcriptCreatedAtText(item);
  const audioText = transcriptAudioText(item);
  const questionText = item.question_text.trim();
  const recoveryText = item.summary_text || "这段录音已经保存，但还没有识别成文字。";
  return {
    ...item,
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
    bookInclusionText: canRecoverTranscription ? "等待转写后再入章" : transcriptBookInclusionText(item),
    isIncludedInBook,
    canAssignChapterSource: chapterAssignOptions.length > 0,
    chapterAssignOptions,
    canMoveChapterSource: chapterMoveOptions.length > 0,
    chapterMoveOptions,
    canPlayAudio: Boolean(item.audio_file_id || item.question_audio_file_id),
    canPlayAnswerAudio: Boolean(item.audio_file_id),
    canPlayQuestionAudio: Boolean(item.question_audio_file_id),
    questionTitleText: item.interview_type === "human_interview" ? "采访者刚才问了" : "小余刚才问了",
    questionCanExpand: Array.from(questionText).length > COLLAPSIBLE_QUESTION_CHAR_COUNT,
    questionSegments: plainTranscriptSegments(item.question_text),
    rawSegments: plainTranscriptSegments(canRecoverTranscription ? "等待恢复转写后显示原始识别文字。" : item.transcript_raw),
    displaySegments: plainTranscriptSegments(canRecoverTranscription ? recoveryText : item.display_text),
    changed: previous?.isEditing
      ? previous.editingText.trim() !== editingText.trim()
      : false,
    isEditing: Boolean(previous?.isEditing),
  };
}

function changedItem(item: TranscriptDetailItemView, editingText: string): TranscriptDetailItemView {
  return {
    ...item,
    editingText,
    editingCharCount: editingText.trim().length,
    changed: editingText.trim() !== (item.transcript_edited || item.transcript_raw).trim(),
    isEditing: true,
  };
}

function confirmDelete(item: TranscriptDetailItemView): Promise<boolean> {
  return new Promise((resolve) => {
    const preview = item.titleText || item.display_text || item.transcript_raw || "这条讲述记录";
    wx.showModal({
      title: "删除讲述记录",
      content: `确定删除“${preview.slice(0, 24)}${preview.length > 24 ? "..." : ""}”？删除后，这条原始讲述会从素材库移除；如果已放入章节，相关章节会标记为需重新确认，已生成正文不会自动改写。`,
      confirmText: "删除",
      confirmColor: "#b84a5a",
      success: (result) => resolve(Boolean(result.confirm)),
      fail: () => resolve(false),
    });
  });
}

definePage<TranscriptDetailData>()({
  audioPlayback: new AudioPlaybackController(),
  loadToken: 0,
  voiceEditToken: 0,
  shareVideoPollTimer: null as ReturnType<typeof setTimeout> | null,
  shareVideoPollToken: 0,
  shareVideoPollFailureCount: 0,
  shareVideoFileCache: {} as Record<string, string>,

  data: {
    loading: false,
    needsLogin: false,
    saving: false,
    deleting: false,
    recovering: false,
    assigning: false,
    moving: false,
    playing: false,
    playingAudioKind: "",
    answerPlaybackVisible: false,
    answerPlaybackPaused: false,
    answerPlaybackStatusText: "",
    answerPlaybackCurrentText: "0:00",
    answerPlaybackDurationText: "时长未知",
    answerPlaybackProgressStyle: "width: 0%;",
    answerPlaybackCanTogglePause: false,
    answerPlaybackCanStop: false,
    answerPlaybackStopText: "停止",
    error: "",
    utteranceId: "",
    item: null,
    bookPlan: null,
    questionExpanded: false,
    moreActionsExpanded: false,
    voiceEditRecording: false,
    voiceEditLoading: false,
    voiceEditSaving: false,
    voiceEditInstructionText: "",
    voiceEditStatusText: "",
    voiceEditPreview: null,
    shareVideoPanelOpen: false,
    shareVideoLoadingContext: false,
    shareVideoCreating: false,
    shareVideoPolling: false,
    shareVideoDownloadingPreview: false,
    shareVideoSharing: false,
    shareVideoError: "",
    shareVideoContext: null,
    shareVideoSegments: [],
    shareVideoSelectedStartIndex: -1,
    shareVideoSelectedEndIndex: -1,
    shareVideoSelectedStartChar: 0,
    shareVideoSelectedEndChar: 0,
    shareVideoSelectedText: "",
    shareVideoSelectedMetaText: "",
    shareVideoSelectedCanGenerate: false,
    ...shareVideoBackgroundPatch(null),
    shareVideoStatusText: "",
    shareVideoStatusClass: "",
    shareVideoJob: null,
    shareVideoPreviewPath: "",
  },

  async onLoad(options?: PageQuery) {
    const utteranceId = queryParam(options, "id");
    this.setData({
      utteranceId,
      questionExpanded: false,
      moreActionsExpanded: false,
    });
    await this.loadTranscript();
    if (queryParam(options, "mode") === "edit") {
      this.beginEdit();
    }
  },

  async loadTranscript() {
    if (!this.data.utteranceId) {
      this.setData({ error: "缺少讲述记录" });
      return;
    }
    this.loadToken += 1;
    const requestToken = this.loadToken;
    this.setData({ loading: true, error: "" });
    try {
      const transcript = await getMemoirVoiceTranscript(this.data.utteranceId);
      const bookPlan = await loadTranscriptBookPlan(transcript.project_id);
      if (requestToken !== this.loadToken) {
        return;
      }
      this.setData({
        needsLogin: false,
        item: toViewItem(transcript, bookPlan, this.data.item),
        bookPlan,
      });
      trackAuditAction("transcript.detail_view", {}, { projectId: transcript.project_id });
    } catch (error) {
      if (requestToken !== this.loadToken) {
        return;
      }
      if (isAuthError(error)) {
        this.setData({ needsLogin: true, item: null, bookPlan: null, error: "" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "讲述记录加载失败" });
    } finally {
      if (requestToken === this.loadToken) {
        this.setData({ loading: false });
      }
    }
  },

  beginEdit() {
    const item = this.data.item;
    if (!item || item.canRecoverTranscription || this.data.saving || this.data.deleting) {
      return;
    }
    this.resetShareVideoState();
    const current = item.transcript_edited || item.transcript_raw;
    this.setData({
      item: {
        ...item,
        editingText: current,
        editingCharCount: current.trim().length,
        changed: false,
        isEditing: true,
      },
      error: "",
    });
  },

  toggleQuestionExpanded() {
    if (!this.data.item?.questionCanExpand) {
      return;
    }
    this.setData({ questionExpanded: !this.data.questionExpanded });
  },

  toggleMoreActionsExpanded() {
    this.setData({ moreActionsExpanded: !this.data.moreActionsExpanded });
  },

  clearShareVideoPollTimer() {
    if (this.shareVideoPollTimer) {
      clearTimeout(this.shareVideoPollTimer);
      this.shareVideoPollTimer = null;
    }
  },

  cancelShareVideoPolling() {
    this.clearShareVideoPollTimer();
    this.shareVideoPollFailureCount = 0;
    this.shareVideoPollToken += 1;
    if (this.data.shareVideoPolling) {
      this.setData({ shareVideoPolling: false });
    }
  },

  resetShareVideoState() {
    this.cancelShareVideoPolling();
    this.shareVideoFileCache = {};
    this.setData({
      shareVideoPanelOpen: false,
      shareVideoLoadingContext: false,
      shareVideoCreating: false,
      shareVideoPolling: false,
      shareVideoDownloadingPreview: false,
      shareVideoSharing: false,
      shareVideoError: "",
      shareVideoContext: null,
      ...shareVideoSelectionPatch(null, -1, -1),
      ...shareVideoBackgroundPatch(null),
      shareVideoStatusText: "",
      shareVideoStatusClass: "",
      shareVideoJob: null,
      shareVideoPreviewPath: "",
    });
  },

  async toggleShareVideoPanel() {
    const item = this.data.item;
    if (
      !item ||
      item.canRecoverTranscription ||
      item.isEditing ||
      this.data.saving ||
      this.data.deleting ||
      this.data.shareVideoSharing
    ) {
      return;
    }
    if (this.data.shareVideoPanelOpen) {
      this.cancelShareVideoPolling();
      this.setData({ shareVideoPanelOpen: false, shareVideoError: "" });
      return;
    }
    await this.openShareVideoPanel();
  },

  async openShareVideoPanel() {
    const item = this.data.item;
    if (!item || item.canRecoverTranscription || item.isEditing) {
      return;
    }
    if (this.data.shareVideoContext?.utterance_id === item.id) {
      this.setData({
        shareVideoPanelOpen: true,
        shareVideoError: "",
        ...shareVideoBackgroundPatch(this.data.shareVideoContext),
      });
      const job = this.data.shareVideoJob;
      if (job && (job.status === "pending" || job.status === "running")) {
        this.startShareVideoPolling(job.id);
      }
      return;
    }
    this.cancelShareVideoPolling();
    this.setData({
      shareVideoPanelOpen: true,
      shareVideoLoadingContext: true,
      shareVideoCreating: false,
      shareVideoDownloadingPreview: false,
      shareVideoSharing: false,
      shareVideoError: "",
      shareVideoContext: null,
      ...shareVideoSelectionPatch(null, -1, -1),
      ...shareVideoBackgroundPatch(null),
      shareVideoStatusText: "",
      shareVideoStatusClass: "",
      shareVideoJob: null,
      shareVideoPreviewPath: "",
    });
    try {
      const context = await getMemoirTranscriptShareVideoContext(item.id);
      this.setData({
        shareVideoContext: context,
        ...shareVideoSelectionPatch(context, -1, -1),
        ...shareVideoBackgroundPatch(context),
      });
      trackAuditAction("transcript.detail_share_video_open", {}, { projectId: item.project_id });
    } catch (error) {
      this.setData({
        shareVideoError: error instanceof Error ? error.message : "分享视频素材加载失败",
        shareVideoStatusClass: "failed",
      });
    } finally {
      this.setData({ shareVideoLoadingContext: false });
    }
  },

  selectShareVideoSegment(event: ShareVideoSegmentEvent) {
    const context = this.data.shareVideoContext;
    const item = this.data.item;
    if (
      !context ||
      !item ||
      this.data.shareVideoCreating ||
      this.data.shareVideoPolling ||
      this.data.shareVideoDownloadingPreview ||
      this.data.shareVideoSharing
    ) {
      return;
    }
    const index = datasetNumber(event, "index", -1);
    const segment = context.segments[index];
    if (!segment || !segment.selectable) {
      return;
    }
    const currentStart = this.data.shareVideoSelectedStartIndex;
    const currentEnd = this.data.shareVideoSelectedEndIndex;
    let nextStart = index;
    let nextEnd = index;
    if (currentStart >= 0 && currentEnd >= 0) {
      const normalizedStart = Math.min(currentStart, currentEnd);
      const normalizedEnd = Math.max(currentStart, currentEnd);
      if (index < normalizedStart) {
        nextStart = index;
        nextEnd = normalizedEnd;
      } else if (index > normalizedEnd) {
        nextStart = normalizedStart;
        nextEnd = index;
      }
    }
    this.cancelShareVideoPolling();
    this.shareVideoFileCache = {};
    this.setData({
      ...shareVideoSelectionPatch(context, nextStart, nextEnd),
      shareVideoError: "",
      shareVideoJob: null,
      shareVideoPreviewPath: "",
      shareVideoSharing: false,
    });
  },

  clearShareVideoSelection() {
    const context = this.data.shareVideoContext;
    if (
      !context ||
      this.data.shareVideoCreating ||
      this.data.shareVideoPolling ||
      this.data.shareVideoDownloadingPreview ||
      this.data.shareVideoSharing
    ) {
      return;
    }
    this.cancelShareVideoPolling();
    this.shareVideoFileCache = {};
    this.setData({
      ...shareVideoSelectionPatch(context, -1, -1),
      shareVideoError: "",
      shareVideoJob: null,
      shareVideoPreviewPath: "",
      shareVideoSharing: false,
    });
  },

  selectShareVideoBackgroundStyle(event: ShareVideoBackgroundEvent) {
    if (
      this.data.shareVideoCreating ||
      this.data.shareVideoPolling ||
      this.data.shareVideoDownloadingPreview ||
      this.data.shareVideoSharing
    ) {
      return;
    }
    const backgroundStyle = saveSelectedShareVideoBackgroundStyle(datasetString(event, "backgroundStyle"));
    this.shareVideoFileCache = {};
    this.setData({
      ...shareVideoBackgroundPatch(
        this.data.shareVideoContext,
        backgroundStyle,
        this.data.shareVideoBackgroundMode,
      ),
      shareVideoJob: null,
      shareVideoPreviewPath: "",
      shareVideoSharing: false,
    });
  },

  selectShareVideoBackgroundMode(event: ShareVideoBackgroundEvent) {
    if (
      this.data.shareVideoCreating ||
      this.data.shareVideoPolling ||
      this.data.shareVideoDownloadingPreview ||
      this.data.shareVideoSharing
    ) {
      return;
    }
    const backgroundMode = saveSelectedShareVideoBackgroundMode(datasetString(event, "backgroundMode"));
    this.shareVideoFileCache = {};
    this.setData({
      ...shareVideoBackgroundPatch(
        this.data.shareVideoContext,
        this.data.shareVideoBackgroundStyle,
        backgroundMode,
      ),
      shareVideoJob: null,
      shareVideoPreviewPath: "",
      shareVideoSharing: false,
    });
  },

  async createShareVideo() {
    const item = this.data.item;
    if (
      !item ||
      !this.data.shareVideoContext ||
      !this.data.shareVideoSelectedCanGenerate ||
      this.data.shareVideoCreating ||
      this.data.shareVideoPolling ||
      this.data.shareVideoDownloadingPreview ||
      this.data.shareVideoSharing
    ) {
      return;
    }
    this.stopPlayback();
    this.cancelShareVideoPolling();
    this.shareVideoFileCache = {};
    const token = this.shareVideoPollToken + 1;
    this.shareVideoPollToken = token;
    this.shareVideoPollFailureCount = 0;
    this.setData({
      shareVideoCreating: true,
      shareVideoPolling: false,
      shareVideoDownloadingPreview: false,
      shareVideoSharing: false,
      shareVideoError: "",
      shareVideoStatusText: "正在创建视频任务",
      shareVideoStatusClass: "running",
      shareVideoJob: null,
      shareVideoPreviewPath: "",
    });
    try {
      const job = await createMemoirTranscriptShareVideoJob(
        item.id,
        this.data.shareVideoSelectedStartChar,
        this.data.shareVideoSelectedEndChar,
        {
          backgroundStyle: this.data.shareVideoBackgroundStyle,
          backgroundMode: this.data.shareVideoBackgroundMode,
        },
      );
      if (token !== this.shareVideoPollToken) {
        return;
      }
      trackAuditAction(
        "transcript.detail_share_video_create",
        { duration_ms: job.duration_ms },
        { projectId: item.project_id },
      );
      await this.applyShareVideoJob(job, token);
    } catch (error) {
      if (token !== this.shareVideoPollToken) {
        return;
      }
      this.setData({
        shareVideoError: error instanceof Error ? error.message : "视频任务创建失败",
        shareVideoStatusText: "视频任务创建失败",
        shareVideoStatusClass: "failed",
      });
    } finally {
      if (token === this.shareVideoPollToken) {
        this.setData({ shareVideoCreating: false });
      }
    }
  },

  startShareVideoPolling(jobId: string) {
    this.clearShareVideoPollTimer();
    const token = this.shareVideoPollToken + 1;
    this.shareVideoPollToken = token;
    this.shareVideoPollFailureCount = 0;
    this.scheduleShareVideoPoll(jobId, token);
  },

  scheduleShareVideoPoll(jobId: string, token: number) {
    this.clearShareVideoPollTimer();
    this.shareVideoPollTimer = setTimeout(() => {
      void this.refreshShareVideoJob(jobId, token);
    }, SHARE_VIDEO_POLL_INTERVAL_MS);
  },

  async refreshShareVideoJob(jobId: string, token?: number) {
    const requestToken = token ?? this.shareVideoPollToken;
    if (requestToken !== this.shareVideoPollToken) {
      return;
    }
    this.setData({
      shareVideoPolling: true,
      shareVideoStatusText: "正在生成视频",
      shareVideoStatusClass: "running",
    });
    try {
      const job = await getMemoirShareVideoJob(jobId);
      if (requestToken !== this.shareVideoPollToken) {
        return;
      }
      this.shareVideoPollFailureCount = 0;
      await this.applyShareVideoJob(job, requestToken);
    } catch (error) {
      if (requestToken !== this.shareVideoPollToken) {
        return;
      }
      const failureCount = this.shareVideoPollFailureCount + 1;
      this.shareVideoPollFailureCount = failureCount;
      if (isTransientShareVideoPollError(error) && failureCount < SHARE_VIDEO_MAX_POLL_FAILURES) {
        this.setData({
          shareVideoPolling: true,
          shareVideoError: "",
          shareVideoStatusText: "视频状态刷新中断，正在重试",
          shareVideoStatusClass: "running",
        });
        this.scheduleShareVideoPoll(jobId, requestToken);
        return;
      }
      this.clearShareVideoPollTimer();
      this.shareVideoPollFailureCount = 0;
      this.setData({
        shareVideoPolling: false,
        shareVideoError: error instanceof Error ? error.message : "视频生成状态刷新失败",
        shareVideoStatusText: "视频生成状态刷新失败",
        shareVideoStatusClass: "failed",
      });
    }
  },

  async applyShareVideoJob(job: MemoirShareVideoJob, token: number) {
    const running = job.status === "pending" || job.status === "running";
    this.setData({
      shareVideoJob: job,
      shareVideoPolling: running,
      shareVideoStatusText: shareVideoJobStatusText(job),
      shareVideoStatusClass: job.status,
      shareVideoError: job.status === "failed" ? (job.error_message || "视频生成失败") : "",
    });
    if (running) {
      this.scheduleShareVideoPoll(job.id, token);
      return;
    }
    this.clearShareVideoPollTimer();
    if (job.status === "succeeded") {
      await this.loadShareVideoPreview(job, token);
    }
  },

  async ensureShareVideoPreviewPath(
    job: MemoirShareVideoJob,
    token: number,
    statusText = "正在准备预览",
  ): Promise<string> {
    if (!job.download_url) {
      throw new Error("视频还没有生成完成");
    }
    if (this.data.shareVideoJob?.id === job.id && this.data.shareVideoPreviewPath) {
      return this.data.shareVideoPreviewPath;
    }
    const cachedPath = this.shareVideoFileCache[job.id];
    const readyStatusText = shareVideoJobStatusText(job) === "视频已生成"
      ? "视频已生成，可以预览"
      : shareVideoJobStatusText(job);
    if (cachedPath) {
      this.setData({
        shareVideoPreviewPath: cachedPath,
        shareVideoStatusText: readyStatusText,
        shareVideoStatusClass: "succeeded",
      });
      return cachedPath;
    }
    this.setData({
      shareVideoDownloadingPreview: true,
      shareVideoError: "",
      shareVideoStatusText: statusText,
      shareVideoStatusClass: "running",
    });
    try {
      const previewPath = await downloadMemoirShareVideoJob(job);
      if (token !== this.shareVideoPollToken) {
        return "";
      }
      this.shareVideoFileCache[job.id] = previewPath;
      this.setData({
        shareVideoPreviewPath: previewPath,
        shareVideoStatusText: readyStatusText,
        shareVideoStatusClass: "succeeded",
      });
      return previewPath;
    } finally {
      if (token === this.shareVideoPollToken) {
        this.setData({ shareVideoDownloadingPreview: false });
      }
    }
  },

  async loadShareVideoPreview(job: MemoirShareVideoJob, token: number) {
    try {
      await this.ensureShareVideoPreviewPath(job, token);
    } catch (error) {
      if (token !== this.shareVideoPollToken) {
        return;
      }
      this.setData({
        shareVideoError: error instanceof Error ? error.message : "视频预览下载失败",
        shareVideoStatusText: "视频已生成，预览下载失败",
        shareVideoStatusClass: "failed",
      });
    }
  },

  async shareGeneratedVideo() {
    const item = this.data.item;
    const job = this.data.shareVideoJob;
    if (!item || !job || job.status !== "succeeded") {
      this.setData({
        shareVideoError: "请先生成视频",
        shareVideoStatusText: "请先生成视频",
        shareVideoStatusClass: "failed",
      });
      return;
    }
    if (
      this.data.shareVideoCreating ||
      this.data.shareVideoPolling ||
      this.data.shareVideoDownloadingPreview ||
      this.data.shareVideoSharing
    ) {
      return;
    }
    const token = this.shareVideoPollToken;
    this.setData({
      shareVideoSharing: true,
      shareVideoError: "",
      shareVideoStatusText: "正在准备分享",
      shareVideoStatusClass: "running",
    });
    try {
      const filePath = await this.ensureShareVideoPreviewPath(job, token, "正在准备分享");
      if (!filePath || token !== this.shareVideoPollToken) {
        return;
      }
      await shareLocalFileMessage({
        filePath,
        fileName: shareVideoFileName(item),
      });
      if (token !== this.shareVideoPollToken) {
        return;
      }
      this.setData({
        shareVideoStatusText: "已打开微信分享",
        shareVideoStatusClass: "succeeded",
      });
      trackAuditAction("transcript.detail_share_video_share", { job_id: job.id }, { projectId: item.project_id });
    } catch (error) {
      if (token !== this.shareVideoPollToken) {
        return;
      }
      if (isShareFileMessageCancelError(error)) {
        this.setData({
          shareVideoStatusText: "视频已生成，可以预览",
          shareVideoStatusClass: "succeeded",
        });
        return;
      }
      this.setData({
        shareVideoError: error instanceof Error ? error.message : "视频分享失败",
        shareVideoStatusText: "视频分享失败",
        shareVideoStatusClass: "failed",
      });
    } finally {
      if (token === this.shareVideoPollToken) {
        this.setData({ shareVideoSharing: false });
      }
    }
  },

  changeText(event: TextAreaEvent) {
    const item = this.data.item;
    if (!item || item.canRecoverTranscription) {
      return;
    }
    this.setData({ item: changedItem(item, event.detail.value) });
  },

  cancelEdit() {
    const item = this.data.item;
    if (!item || this.data.saving || this.data.deleting) {
      return;
    }
    this.setData({ item: toViewItem(item, this.data.bookPlan), error: "" });
  },

  async saveTranscript() {
    const item = this.data.item;
    if (!item || item.canRecoverTranscription || this.data.saving || this.data.deleting) {
      return;
    }
    const content = item.editingText.trim();
    if (!content) {
      this.setData({ error: "成书文字不能为空" });
      return;
    }
    this.setData({ saving: true, error: "" });
    try {
      const updated = await updateMemoirVoiceTranscript(item.id, content);
      this.setData({ item: toViewItem(updated, this.data.bookPlan) });
      this.resetShareVideoState();
      trackAuditAction("transcript.detail_edit_save", {}, { projectId: updated.project_id });
      wx.showToast({ title: "已保存", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "保存失败" });
    } finally {
      this.setData({ saving: false });
    }
  },

  async recoverTranscript() {
    const item = this.data.item;
    if (
      !item ||
      !item.canRecoverTranscription ||
      this.data.recovering ||
      this.data.saving ||
      this.data.deleting
    ) {
      return;
    }
    this.setData({ recovering: true, error: "" });
    try {
      const recovered = await recoverMemoirVoiceTranscript(item.id);
      this.setData({ item: toViewItem(recovered, this.data.bookPlan) });
      this.resetShareVideoState();
      trackAuditAction("transcript.detail_recover_transcription", {}, { projectId: recovered.project_id });
      wx.showToast({ title: "已恢复转写", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "恢复转写失败" });
    } finally {
      this.setData({ recovering: false });
    }
  },

  resetToRaw() {
    const item = this.data.item;
    if (!item || item.canRecoverTranscription) {
      return;
    }
    this.setData({ item: changedItem(item, item.transcript_raw) });
  },

  async startVoiceEdit() {
    const item = this.data.item;
    if (
      !item
      || item.canRecoverTranscription
      || this.data.saving
      || this.data.deleting
      || this.data.voiceEditLoading
      || this.data.voiceEditSaving
    ) {
      return;
    }
    if (this.data.voiceEditRecording) {
      stopMemoirVoiceSearchRecording();
      return;
    }
    this.stopPlayback();
    this.voiceEditToken += 1;
    const requestToken = this.voiceEditToken;
    this.setData({
      voiceEditRecording: true,
      voiceEditLoading: false,
      voiceEditSaving: false,
      voiceEditInstructionText: "",
      voiceEditStatusText: "正在听你说修改意见",
      voiceEditPreview: null,
      error: "",
    });
    try {
      const instructionText = await recordMemoirVoiceSearchQuery({
        onRecordingEnd: () => {
          if (requestToken !== this.voiceEditToken) {
            return;
          }
          this.setData({
            voiceEditRecording: false,
            voiceEditLoading: true,
            voiceEditStatusText: "正在识别修改意见",
          });
        },
        emptyTranscriptMessage: "没有识别到修改意见，请再说一次",
        noVoiceTranscriptMessage: "没有听到修改意见，请再说一次",
      });
      if (requestToken !== this.voiceEditToken) {
        return;
      }
      this.setData({
        voiceEditInstructionText: instructionText,
        voiceEditStatusText: "正在生成修改预览",
      });
      const preview = await previewMemoirVoiceTranscriptVoiceEdit(item.id, instructionText);
      if (requestToken !== this.voiceEditToken) {
        return;
      }
      this.setData({
        voiceEditPreview: transcriptVoiceEditPreviewView(preview),
        voiceEditStatusText: preview.action === "revise_transcript" ? "请确认修改内容" : preview.reply_text,
      });
      trackAuditAction("transcript.detail_voice_edit_preview", { action: preview.action }, { projectId: item.project_id });
    } catch (error) {
      if (requestToken !== this.voiceEditToken) {
        return;
      }
      this.setData({
        error: error instanceof Error ? error.message : "语音修改失败",
        voiceEditStatusText: "",
      });
    } finally {
      if (requestToken === this.voiceEditToken) {
        this.setData({
          voiceEditRecording: false,
          voiceEditLoading: false,
        });
      }
    }
  },

  cancelVoiceEdit() {
    this.voiceEditToken += 1;
    if (this.data.voiceEditRecording) {
      stopMemoirVoiceSearchRecording();
    }
    this.setData({
      voiceEditRecording: false,
      voiceEditLoading: false,
      voiceEditSaving: false,
      voiceEditInstructionText: "",
      voiceEditStatusText: "",
      voiceEditPreview: null,
      error: "",
    });
  },

  async confirmVoiceEdit() {
    const item = this.data.item;
    const preview = this.data.voiceEditPreview;
    if (
      !item
      || !preview
      || !preview.canConfirm
      || this.data.voiceEditSaving
      || this.data.saving
      || this.data.deleting
    ) {
      return;
    }
    const content = preview.editedText.trim();
    if (!content) {
      this.setData({ error: "修改后的成书文字不能为空" });
      return;
    }
    this.setData({ voiceEditSaving: true, saving: true, error: "" });
    try {
      const updated = await updateMemoirVoiceTranscript(item.id, content);
      this.setData({
        item: toViewItem(updated, this.data.bookPlan),
        voiceEditInstructionText: "",
        voiceEditStatusText: "",
        voiceEditPreview: null,
      });
      this.resetShareVideoState();
      trackAuditAction("transcript.detail_voice_edit_save", {}, { projectId: updated.project_id });
      wx.showToast({ title: "已保存修改", icon: "success" });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "保存修改失败" });
    } finally {
      this.setData({ voiceEditSaving: false, saving: false });
    }
  },

  async assignTranscriptChapter(event: PickerEvent) {
    const item = this.data.item;
    const optionIndex = pickerIndex(event);
    const option = item?.chapterAssignOptions[optionIndex];
    if (!item || !option || this.data.assigning || this.data.moving) {
      return;
    }
    this.stopPlayback();
    this.setData({ assigning: true, error: "" });
    try {
      const updatedPlan = await assignMemoirBookSources(
        item.project_id,
        sourceAssignmentRequest(option.value, item.id),
      );
      this.setData({
        bookPlan: updatedPlan,
        item: toViewItem(await getMemoirVoiceTranscript(item.id), updatedPlan),
      });
      wx.showToast({ title: "已归入章节", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "归入章节失败" });
    } finally {
      this.setData({ assigning: false });
    }
  },

  async moveTranscriptChapter(event: PickerEvent) {
    const item = this.data.item;
    const optionIndex = pickerIndex(event);
    const option = item?.chapterMoveOptions[optionIndex];
    if (!item || !option || this.data.assigning || this.data.moving) {
      return;
    }
    this.stopPlayback();
    this.setData({ moving: true, error: "" });
    try {
      const plan = this.data.bookPlan || await loadTranscriptBookPlan(item.project_id);
      const moveRequest = transcriptSourceMoveRequest(item, plan, option.value);
      if (!moveRequest.request) {
        this.setData({ error: moveRequest.error });
        return;
      }
      const updatedPlan = await moveMemoirBookSource(item.project_id, moveRequest.request);
      this.setData({
        bookPlan: updatedPlan,
        item: toViewItem(await getMemoirVoiceTranscript(item.id), updatedPlan),
      });
      wx.showToast({ title: "素材已移动", icon: "success" });
    } catch (error) {
      if (routeAuthError(error, "me")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "素材移动失败" });
    } finally {
      this.setData({ moving: false });
    }
  },

  async deleteTranscript() {
    const item = this.data.item;
    if (!item || this.data.saving || this.data.deleting) {
      return;
    }
    const confirmed = await confirmDelete(item);
    if (!confirmed) {
      return;
    }
    this.stopPlayback();
    this.setData({ deleting: true, error: "" });
    try {
      await deleteMemoirVoiceTranscript(item.id);
      wx.showToast({ title: "已删除", icon: "success" });
      wx.navigateBack({ delta: 1 });
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "删除失败" });
    } finally {
      this.setData({ deleting: false });
    }
  },

  async playAnswerAudio() {
    await this.playAudio("answer");
  },

  async playQuestionAudio() {
    await this.playAudio("question");
  },

  async playAudio(kind: TranscriptDetailAudioKind) {
    const item = this.data.item;
    const audioFileId = kind === "question" ? item?.question_audio_file_id : item?.audio_file_id;
    if (!audioFileId) {
      this.setData({
        error: kind === "question" ? "小余提问没有可播放的语音" : "这段讲述没有可播放的原音",
      });
      return;
    }
    if (this.data.playing && this.data.playingAudioKind === kind) {
      this.stopPlayback();
      return;
    }
    if (this.data.saving || this.data.deleting) {
      return;
    }
    this.stopPlayback();
    const token = this.audioPlayback.nextToken();
    const fallbackDuration = kind === "answer" ? answerAudioFallbackDuration(item) : 0;
    this.setData({
      playing: true,
      playingAudioKind: kind,
      ...(kind === "answer"
        ? {
          answerPlaybackVisible: true,
          answerPlaybackPaused: false,
          answerPlaybackCanTogglePause: false,
          answerPlaybackCanStop: true,
          answerPlaybackStopText: "停止",
          ...answerPlaybackPatch(0, fallbackDuration, "正在准备原音"),
        }
        : {}),
      error: "",
    });
    try {
      await this.audioPlayback.playFile(audioFileId, token, kind === "answer"
        ? {
          onPlay: (audio) => {
            if (!this.audioPlayback.isCurrentToken(token) || this.audioPlayback.currentAudio() !== audio) {
              return;
            }
            this.setData({
              answerPlaybackPaused: false,
              answerPlaybackCanTogglePause: true,
              answerPlaybackCanStop: true,
              answerPlaybackStopText: "停止",
            });
            this.updateAnswerPlaybackProgress(audio.currentTime, audio.duration || fallbackDuration, "正在播放");
          },
          onTimeUpdate: (audio) => {
            if (!this.audioPlayback.isCurrentToken(token) || this.audioPlayback.currentAudio() !== audio) {
              return;
            }
            this.updateAnswerPlaybackProgress(audio.currentTime, audio.duration || fallbackDuration, "正在播放");
          },
          onEnded: (audio) => {
            if (!this.audioPlayback.isCurrentToken(token)) {
              return;
            }
            const duration = positiveSeconds(audio.duration) || fallbackDuration;
            this.setData({
              answerPlaybackPaused: false,
              answerPlaybackCanTogglePause: false,
              answerPlaybackCanStop: true,
              answerPlaybackStopText: "收起",
              ...answerPlaybackPatch(duration, duration, "播放完成"),
            });
          },
        }
        : undefined);
    } catch (error) {
      if (this.audioPlayback.isCurrentToken(token)) {
        this.setData({
          error: error instanceof Error ? error.message : "播放失败",
          ...(kind === "answer"
            ? {
              answerPlaybackPaused: false,
              answerPlaybackCanTogglePause: false,
              answerPlaybackCanStop: true,
              answerPlaybackStopText: "收起",
              answerPlaybackStatusText: "播放失败",
            }
            : {}),
        });
      }
    } finally {
      if (this.audioPlayback.isCurrentToken(token)) {
        this.setData({
          playing: false,
          playingAudioKind: "",
          ...(kind === "answer"
            ? {
              answerPlaybackPaused: false,
              answerPlaybackCanTogglePause: false,
            }
            : {}),
        });
      }
    }
  },

  toggleAnswerPlaybackPause() {
    if (!this.data.answerPlaybackCanTogglePause || this.data.playingAudioKind !== "answer") {
      return;
    }
    const audio = this.audioPlayback.currentAudio();
    if (!audio) {
      return;
    }
    if (this.data.answerPlaybackPaused) {
      audio.play();
      this.setData({
        answerPlaybackPaused: false,
        answerPlaybackStatusText: "正在播放",
      });
      return;
    }
    audio.pause();
    this.setData({
      answerPlaybackPaused: true,
      answerPlaybackStatusText: "已暂停",
    });
  },

  updateAnswerPlaybackProgress(currentSeconds: number, durationSeconds: number, statusText: string) {
    this.setData(answerPlaybackPatch(currentSeconds, durationSeconds, statusText));
  },

  stopPlayback() {
    this.audioPlayback.stop();
    if (this.data.playing || this.data.answerPlaybackVisible) {
      this.setData({
        playing: false,
        playingAudioKind: "",
        answerPlaybackVisible: false,
        answerPlaybackPaused: false,
        answerPlaybackStatusText: "",
        answerPlaybackCanTogglePause: false,
        answerPlaybackCanStop: false,
        answerPlaybackStopText: "停止",
      });
    }
  },

  goBack() {
    wx.navigateBack({ delta: 1 });
  },

  openLogin() {
    switchToMe();
  },

  onUnload() {
    this.stopPlayback();
    this.cancelVoiceEdit();
    this.cancelShareVideoPolling();
  },
});
