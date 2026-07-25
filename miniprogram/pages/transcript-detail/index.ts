import type {
  MemoirMaterialOfflineASRState,
  MemoirShareVideoCustomBackgroundAsset,
  MemoirVoiceTranscriptItem,
} from "../../domain/voice-chat";
import { definePage } from "../../services/page-definition";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  convertMemoirInterviewQuestionToAnswer,
  createMemoirTranscriptShareVideoJob,
  deleteMemoirVoiceTranscript,
  downloadMemoirShareVideoCustomBackgroundPreview,
  downloadMemoirShareVideoJob,
  getMemoirShareVideoJob,
  getMemoirTranscriptShareVideoContext,
  getMemoirVoiceTranscript,
  previewMemoirVoiceTranscriptVoiceEdit,
  recoverMemoirVoiceTranscript,
  startMemoirMaterialOfflineASR,
  updateMemoirVoiceTranscript,
  uploadMemoirShareVideoCustomBackground,
} from "../../services/memoir-voice-api";
import { AudioPlaybackController } from "../../services/audio-playback";
import {
  shareLocalFileMessage,
} from "../../services/share";
import {
  isAuthError,
  navigateToLogin,
  routeAuthError,
} from "../../services/navigation";
import {
  recordMemoirVoiceSearchQuery,
  stopMemoirVoiceSearchRecording,
} from "../../services/memoir-voice-search";
import {
  plainTranscriptSegments,
  transcriptAudioText,
  transcriptBookInclusionText,
  transcriptCreatedAtText,
  transcriptTitleText,
  transcriptVoiceEditPreviewView,
  transcriptVoiceEditStateView,
  type TranscriptTextSegment,
  type TranscriptVoiceEditPhase,
  type TranscriptVoiceEditPreviewView,
} from "../../services/transcript-view-model";
import {
  datasetNumber,
  datasetString,
  type PageDatasetEvent,
  queryParam,
  type PageQuery,
  type PageTextInputEvent,
} from "../../services/page-input";
import {
  chooseShareVideoBackgroundImage,
  initialTranscriptShareVideoData,
  saveShareVideoBackgroundSelection,
  shareVideoBackgroundPatch,
  shareVideoSelectionPatch,
  TranscriptShareVideoController,
  type TranscriptShareVideoData,
} from "../../services/transcript-share-video";
import { materialOfflineASRView } from "../../services/material-offline-asr-view";

type TextAreaEvent = PageTextInputEvent;
type ShareVideoSegmentEvent = PageDatasetEvent<{ index?: string | number }>;
type ShareVideoBackgroundEvent = PageDatasetEvent<{
  backgroundMode?: string;
  backgroundSource?: string;
  backgroundStyle?: string;
}>;

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

interface TranscriptDetailData extends TranscriptShareVideoData {
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

function voiceEditStateData(
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
const VOICE_EDIT_MAX_RECORDING_MS = 30000;
const OFFLINE_ASR_POLL_INTERVAL_MS = 2000;
const OFFLINE_ASR_MAX_POLL_ATTEMPTS = 150;
type TranscriptDetailAudioKind = "question" | "answer";

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

function shareVideoFileName(item: TranscriptDetailItemView | null): string {
  const title = (item?.titleText || item?.summary_title || "回忆录片段")
    .replace(/[\\/:*?"<>|]/g, "")
    .trim();
  const shortTitle = Array.from(title).slice(0, 24).join("");
  return `${shortTitle || "回忆录片段"}.mp4`;
}

function toViewItem(
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

function changedItem(item: TranscriptDetailItemView, editingText: string): TranscriptDetailItemView {
  return {
    ...item,
    editingText,
    editingCharCount: editingText.trim().length,
    changed: editingText.trim() !== (item.transcript_edited || item.transcript_raw).trim(),
    isEditing: true,
  };
}

function syncEditUnloadAlert(item: TranscriptDetailItemView | null): void {
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

function confirmDelete(item: TranscriptDetailItemView): Promise<boolean> {
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

function confirmConvertQuestionToAnswer(item: TranscriptDetailItemView): Promise<boolean> {
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

function confirmDiscardEdit(): Promise<boolean> {
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

function confirmOfflineAsr(item: TranscriptDetailItemView): Promise<boolean> {
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

definePage<TranscriptDetailData>()({
  audioPlayback: new AudioPlaybackController(),
  shareVideoController: new TranscriptShareVideoController({
    createJob: createMemoirTranscriptShareVideoJob,
    getJob: getMemoirShareVideoJob,
    downloadJob: downloadMemoirShareVideoJob,
    shareFile: shareLocalFileMessage,
  }),
  loadToken: 0,
  voiceEditToken: 0,
  shareVideoBackgroundUploadToken: 0,
  offlineAsrPollTimer: null as ReturnType<typeof setTimeout> | null,
  offlineAsrPollToken: 0,
  offlineAsrPollAttempts: 0,

  data: {
    pageClass: "detail-page",
    loading: false,
    needsLogin: false,
    saving: false,
    deleting: false,
    convertingQuestionToAnswer: false,
    recovering: false,
    offlineAsrStarting: false,
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
    questionExpanded: false,
    moreActionsExpanded: false,
    editReferenceExpanded: false,
    voiceEditRecording: false,
    voiceEditLoading: false,
    voiceEditSaving: false,
    voiceEditInstructionText: "",
    voiceEditPreview: null,
    ...voiceEditStateData("idle"),
    ...initialTranscriptShareVideoData(),
  },

  async onLoad(options?: PageQuery) {
    this.shareVideoController.setStateListener((patch) => this.setData(patch));
    this.resetShareVideoState();
    const utteranceId = queryParam(options, "id");
    this.setData({
      utteranceId,
      questionExpanded: false,
      moreActionsExpanded: false,
      editReferenceExpanded: false,
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
    this.stopOfflineAsrPolling();
    this.loadToken += 1;
    const requestToken = this.loadToken;
    this.setData({ loading: true, error: "" });
    try {
      const transcript = await getMemoirVoiceTranscript(this.data.utteranceId);
      if (requestToken !== this.loadToken) {
        return;
      }
      this.setData({
        needsLogin: false,
        item: toViewItem(transcript, this.data.item),
      });
      this.startOfflineAsrPollingIfNeeded();
      trackAuditAction("transcript.detail_view", {}, { projectId: transcript.project_id });
    } catch (error) {
      if (requestToken !== this.loadToken) {
        return;
      }
      if (isAuthError(error)) {
        this.setData({ needsLogin: true, item: null, error: "" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "讲述记录加载失败" });
    } finally {
      if (requestToken === this.loadToken) {
        this.setData({ loading: false });
      }
    }
  },

  stopOfflineAsrPolling() {
    this.offlineAsrPollToken += 1;
    this.offlineAsrPollAttempts = 0;
    if (this.offlineAsrPollTimer !== null) {
      clearTimeout(this.offlineAsrPollTimer);
      this.offlineAsrPollTimer = null;
    }
  },

  startOfflineAsrPollingIfNeeded() {
    if (!this.data.item?.offlineAsrActive) {
      return;
    }
    this.stopOfflineAsrPolling();
    const token = this.offlineAsrPollToken;
    this.scheduleOfflineAsrPoll(token);
  },

  scheduleOfflineAsrPoll(token: number) {
    if (token !== this.offlineAsrPollToken || !this.data.item?.offlineAsrActive) {
      return;
    }
    if (this.offlineAsrPollAttempts >= OFFLINE_ASR_MAX_POLL_ATTEMPTS) {
      return;
    }
    this.offlineAsrPollTimer = setTimeout(() => {
      this.offlineAsrPollTimer = null;
      void this.pollOfflineAsr(token);
    }, OFFLINE_ASR_POLL_INTERVAL_MS);
  },

  async pollOfflineAsr(token: number) {
    const current = this.data.item;
    if (!current || token !== this.offlineAsrPollToken) {
      return;
    }
    this.offlineAsrPollAttempts += 1;
    try {
      const transcript = await getMemoirVoiceTranscript(current.id);
      if (token !== this.offlineAsrPollToken) {
        return;
      }
      const next = toViewItem(transcript, current);
      this.setData({ item: next });
      if (next.offline_asr.status === "succeeded") {
        this.stopOfflineAsrPolling();
        if (!this.data.shareVideoPanelOpen) {
          this.resetShareVideoState();
        }
        wx.showToast({ title: "识别已更新", icon: "success" });
        return;
      }
      if (next.offline_asr.status === "failed") {
        this.stopOfflineAsrPolling();
        return;
      }
      this.scheduleOfflineAsrPoll(token);
    } catch {
      if (token !== this.offlineAsrPollToken) {
        return;
      }
      this.scheduleOfflineAsrPoll(token);
    }
  },

  async startOfflineAsr() {
    const item = this.data.item;
    if (
      !item
      || !item.showOfflineAsr
      || !item.canStartOfflineAsr
      || item.isEditing
      || this.data.offlineAsrStarting
      || this.data.saving
      || this.data.deleting
      || this.data.convertingQuestionToAnswer
    ) {
      return;
    }
    if (!await confirmOfflineAsr(item)) {
      return;
    }
    this.stopPlayback();
    this.resetShareVideoState();
    this.setData({ offlineAsrStarting: true, error: "" });
    try {
      const state = await startMemoirMaterialOfflineASR(item.id);
      const next = toViewItem({ ...item, offline_asr: state }, item);
      this.setData({ item: next });
      trackAuditAction(
        "transcript.detail_offline_asr_start",
        {
          dialect: state.dialect_label,
          dialect_enhancement_enabled: state.dialect_enhancement_enabled,
        },
        { projectId: item.project_id },
      );
      wx.showToast({ title: "已开始识别", icon: "success" });
      this.startOfflineAsrPollingIfNeeded();
    } catch (error) {
      this.setData({ error: error instanceof Error ? error.message : "离线识别启动失败" });
    } finally {
      this.setData({ offlineAsrStarting: false });
    }
  },

  beginEdit() {
    const item = this.data.item;
    if (
      !item
      || item.canRecoverTranscription
      || this.data.saving
      || this.data.deleting
      || this.data.convertingQuestionToAnswer
      || this.data.voiceEditRecording
      || this.data.voiceEditLoading
      || this.data.voiceEditSaving
    ) {
      return;
    }
    this.stopPlayback();
    this.resetShareVideoState();
    const current = item.transcript_edited || item.transcript_raw;
    const nextItem: TranscriptDetailItemView = {
      ...item,
      editingText: current,
      editingCharCount: current.trim().length,
      changed: false,
      isEditing: true,
    };
    this.setData({
      item: nextItem,
      editReferenceExpanded: false,
      error: "",
    });
    syncEditUnloadAlert(nextItem);
  },

  toggleEditReference() {
    if (!this.data.item?.isEditing) {
      return;
    }
    this.setData({ editReferenceExpanded: !this.data.editReferenceExpanded });
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

  resetShareVideoState() {
    this.shareVideoController.reset();
    this.shareVideoBackgroundUploadToken += 1;
    this.setData(initialTranscriptShareVideoData());
  },

  async toggleShareVideoPanel() {
    const item = this.data.item;
    if (
      !item ||
      item.canRecoverTranscription ||
      item.isEditing ||
      this.data.saving ||
      this.data.deleting ||
      this.data.shareVideoSharing ||
      this.data.shareVideoCustomBackgroundUploading
    ) {
      return;
    }
    if (this.data.shareVideoPanelOpen) {
      this.shareVideoController.cancel();
      this.resetShareVideoState();
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
        this.shareVideoController.resume(job.id);
      }
      return;
    }
    this.shareVideoController.reset();
    this.setData({
      ...initialTranscriptShareVideoData(),
      shareVideoPanelOpen: true,
      shareVideoLoadingContext: true,
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
      this.data.shareVideoSharing ||
      this.data.shareVideoCustomBackgroundUploading
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
    this.shareVideoController.clearResult();
    this.setData({
      ...shareVideoSelectionPatch(context, nextStart, nextEnd),
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
    this.shareVideoController.clearResult();
    this.setData({
      ...shareVideoSelectionPatch(context, -1, -1),
    });
  },

  selectShareVideoBackgroundSource(event: ShareVideoBackgroundEvent) {
    if (
      this.data.shareVideoCreating ||
      this.data.shareVideoPolling ||
      this.data.shareVideoDownloadingPreview ||
      this.data.shareVideoSharing ||
      this.data.shareVideoCustomBackgroundUploading
    ) {
      return;
    }
    const backgroundPatch = shareVideoBackgroundPatch(this.data.shareVideoContext, {
      backgroundSource: datasetString(event, "backgroundSource"),
      backgroundStyle: this.data.shareVideoBackgroundStyle,
      backgroundMode: this.data.shareVideoBackgroundMode,
    });
    saveShareVideoBackgroundSelection(backgroundPatch);
    this.shareVideoController.clearResult();
    this.setData({
      ...backgroundPatch,
    });
  },

  selectShareVideoBackgroundStyle(event: ShareVideoBackgroundEvent) {
    if (
      this.data.shareVideoCreating ||
      this.data.shareVideoPolling ||
      this.data.shareVideoDownloadingPreview ||
      this.data.shareVideoSharing ||
      this.data.shareVideoCustomBackgroundUploading
    ) {
      return;
    }
    const backgroundPatch = shareVideoBackgroundPatch(this.data.shareVideoContext, {
      backgroundSource: this.data.shareVideoBackgroundSource,
      backgroundStyle: datasetString(event, "backgroundStyle"),
      backgroundMode: this.data.shareVideoBackgroundMode,
    });
    saveShareVideoBackgroundSelection(backgroundPatch);
    this.shareVideoController.clearResult();
    this.setData({
      ...backgroundPatch,
    });
  },

  selectShareVideoBackgroundMode(event: ShareVideoBackgroundEvent) {
    if (
      this.data.shareVideoCreating ||
      this.data.shareVideoPolling ||
      this.data.shareVideoDownloadingPreview ||
      this.data.shareVideoSharing ||
      this.data.shareVideoCustomBackgroundUploading
    ) {
      return;
    }
    const backgroundPatch = shareVideoBackgroundPatch(this.data.shareVideoContext, {
      backgroundSource: this.data.shareVideoBackgroundSource,
      backgroundStyle: this.data.shareVideoBackgroundStyle,
      backgroundMode: datasetString(event, "backgroundMode"),
    });
    saveShareVideoBackgroundSelection(backgroundPatch);
    this.shareVideoController.clearResult();
    this.setData({
      ...backgroundPatch,
    });
  },

  async chooseShareVideoCustomBackground() {
    const item = this.data.item;
    if (
      !item ||
      !this.data.shareVideoContext ||
      this.data.shareVideoCreating ||
      this.data.shareVideoPolling ||
      this.data.shareVideoDownloadingPreview ||
      this.data.shareVideoSharing ||
      this.data.shareVideoCustomBackgroundUploading
    ) {
      return;
    }
    let uploadToken = 0;
    try {
      const filePath = await chooseShareVideoBackgroundImage();
      if (!filePath) {
        return;
      }
      this.stopPlayback();
      this.shareVideoController.clearResult();
      this.shareVideoBackgroundUploadToken += 1;
      const token = this.shareVideoBackgroundUploadToken;
      uploadToken = token;
      const backgroundPatch = shareVideoBackgroundPatch(this.data.shareVideoContext, {
        backgroundSource: "custom",
        backgroundStyle: this.data.shareVideoBackgroundStyle,
        backgroundMode: this.data.shareVideoBackgroundMode,
      });
      saveShareVideoBackgroundSelection(backgroundPatch);
      this.setData({
        ...backgroundPatch,
        shareVideoCustomBackgroundUploading: true,
        shareVideoCustomBackgroundStatusText: "正在处理背景图片",
      });
      const asset: MemoirShareVideoCustomBackgroundAsset = await uploadMemoirShareVideoCustomBackground(
        item.project_id,
        filePath,
      );
      if (token !== this.shareVideoBackgroundUploadToken) {
        return;
      }
      if (asset.status !== "ready") {
        throw new Error("背景图片还没有处理完成，请稍后重试");
      }
      let previewPath = "";
      try {
        previewPath = await downloadMemoirShareVideoCustomBackgroundPreview(asset);
      } catch (previewError) {
        if (token !== this.shareVideoBackgroundUploadToken) {
          return;
        }
        this.setData({
          shareVideoCustomBackgroundUploading: false,
          shareVideoCustomBackgroundAssetId: asset.id,
          shareVideoCustomBackgroundPreviewPath: "",
          shareVideoCustomBackgroundStatusText: previewError instanceof Error
            ? `背景图片已处理，预览暂不可用：${previewError.message}`
            : "背景图片已处理，预览暂不可用",
          shareVideoError: "",
        });
        return;
      }
      if (token !== this.shareVideoBackgroundUploadToken) {
        return;
      }
      this.setData({
        shareVideoCustomBackgroundUploading: false,
        shareVideoCustomBackgroundAssetId: asset.id,
        shareVideoCustomBackgroundPreviewPath: previewPath,
        shareVideoCustomBackgroundStatusText: "背景图片已处理，会用于本次视频",
        shareVideoError: "",
      });
      trackAuditAction(
        "transcript.detail_share_video_custom_background_upload",
        { asset_id: asset.id },
        { projectId: item.project_id },
      );
    } catch (error) {
      if (uploadToken && uploadToken !== this.shareVideoBackgroundUploadToken) {
        return;
      }
      this.setData({
        shareVideoCustomBackgroundUploading: false,
        shareVideoCustomBackgroundStatusText: "",
        shareVideoError: error instanceof Error ? error.message : "背景图片处理失败",
        shareVideoStatusText: "背景图片处理失败",
        shareVideoStatusClass: "failed",
      });
    }
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
      this.data.shareVideoSharing ||
      this.data.shareVideoCustomBackgroundUploading
    ) {
      return;
    }
    if (this.data.shareVideoBackgroundSource === "custom" && !this.data.shareVideoCustomBackgroundAssetId) {
      this.setData({
        shareVideoError: "请先选择一张自定义背景图片",
        shareVideoStatusText: "请先选择背景图片",
        shareVideoStatusClass: "failed",
      });
      return;
    }
    this.stopPlayback();
    const job = await this.shareVideoController.create({
      utteranceId: item.id,
      startChar: this.data.shareVideoSelectedStartChar,
      endChar: this.data.shareVideoSelectedEndChar,
      options: {
          backgroundSource: this.data.shareVideoBackgroundSource,
          ...(this.data.shareVideoBackgroundSource === "ai" ? {
            backgroundStyle: this.data.shareVideoBackgroundStyle,
            backgroundMode: this.data.shareVideoBackgroundMode,
          } : {}),
          ...(this.data.shareVideoBackgroundSource === "custom" ? {
            customBackgroundAssetId: this.data.shareVideoCustomBackgroundAssetId,
          } : {}),
      },
    });
    if (job) {
      trackAuditAction(
        "transcript.detail_share_video_create",
        { background_source: this.data.shareVideoBackgroundSource, duration_ms: job.duration_ms },
        { projectId: item.project_id },
      );
    }
  },

  async shareGeneratedVideo() {
    const item = this.data.item;
    const job = this.data.shareVideoJob;
    const shared = await this.shareVideoController.share(shareVideoFileName(item));
    if (shared && item && job) {
      trackAuditAction("transcript.detail_share_video_share", { job_id: job.id }, { projectId: item.project_id });
    }
  },

  changeText(event: TextAreaEvent) {
    const item = this.data.item;
    if (!item || item.canRecoverTranscription) {
      return;
    }
    const nextItem = changedItem(item, event.detail.value);
    this.setData({ item: nextItem });
    syncEditUnloadAlert(nextItem);
  },

  async cancelEdit() {
    const item = this.data.item;
    if (!item || this.data.saving || this.data.deleting) {
      return;
    }
    if (item.changed && !await confirmDiscardEdit()) {
      return;
    }
    const nextItem = toViewItem(item);
    this.setData({
      item: nextItem,
      editReferenceExpanded: false,
      error: "",
    });
    syncEditUnloadAlert(nextItem);
  },

  async saveTranscript() {
    const item = this.data.item;
    if (!item || item.canRecoverTranscription || this.data.saving || this.data.deleting || this.data.convertingQuestionToAnswer) {
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
      const nextItem = toViewItem(updated);
      this.setData({
        item: nextItem,
        editReferenceExpanded: false,
      });
      syncEditUnloadAlert(nextItem);
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
      this.data.deleting ||
      this.data.convertingQuestionToAnswer
    ) {
      return;
    }
    this.setData({ recovering: true, error: "" });
    try {
      const recovered = await recoverMemoirVoiceTranscript(item.id);
      this.setData({ item: toViewItem(recovered) });
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
    const nextItem = changedItem(item, item.transcript_raw);
    this.setData({ item: nextItem });
    syncEditUnloadAlert(nextItem);
  },

  async startVoiceEdit() {
    const item = this.data.item;
    if (
      !item
      || item.canRecoverTranscription
      || this.data.saving
      || this.data.deleting
      || this.data.convertingQuestionToAnswer
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
      voiceEditPreview: null,
      error: "",
      ...voiceEditStateData("listening"),
    });
    try {
      const instructionText = await recordMemoirVoiceSearchQuery({
        stopMode: "manual",
        maxDurationMs: VOICE_EDIT_MAX_RECORDING_MS,
        onRecordingEnd: () => {
          if (requestToken !== this.voiceEditToken) {
            return;
          }
          this.setData({
            voiceEditRecording: false,
            voiceEditLoading: true,
            ...voiceEditStateData("transcribing"),
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
        ...voiceEditStateData("generating"),
      });
      const preview = await previewMemoirVoiceTranscriptVoiceEdit(item.id, instructionText);
      if (requestToken !== this.voiceEditToken) {
        return;
      }
      this.setData({
        voiceEditPreview: transcriptVoiceEditPreviewView(preview),
        ...voiceEditStateData(
          preview.action === "revise_transcript" ? "review" : "clarify",
          preview.action === "revise_transcript" ? "" : preview.reply_text,
        ),
      });
      trackAuditAction("transcript.detail_voice_edit_preview", { action: preview.action }, { projectId: item.project_id });
    } catch (error) {
      if (requestToken !== this.voiceEditToken) {
        return;
      }
      const message = error instanceof Error ? error.message : "语音修改失败";
      this.setData({ error: message, ...voiceEditStateData("error", message) });
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
      voiceEditPreview: null,
      error: "",
      ...voiceEditStateData("idle"),
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
      || this.data.convertingQuestionToAnswer
    ) {
      return;
    }
    const content = preview.editedText.trim();
    if (!content) {
      const message = "修改后的成书文字不能为空";
      this.setData({ error: message, ...voiceEditStateData("error", message) });
      return;
    }
    this.setData({
      voiceEditSaving: true,
      saving: true,
      error: "",
      ...voiceEditStateData("saving"),
    });
    try {
      const updated = await updateMemoirVoiceTranscript(item.id, content);
      this.setData({
        item: toViewItem(updated),
        voiceEditInstructionText: "",
        voiceEditPreview: null,
        ...voiceEditStateData("idle"),
      });
      this.resetShareVideoState();
      trackAuditAction("transcript.detail_voice_edit_save", {}, { projectId: updated.project_id });
      wx.showToast({ title: "已保存修改", icon: "success" });
    } catch (error) {
      const message = error instanceof Error ? error.message : "保存修改失败";
      this.setData({ error: message, ...voiceEditStateData("error", message) });
    } finally {
      this.setData({ voiceEditSaving: false, saving: false });
    }
  },

  async deleteTranscript() {
    const item = this.data.item;
    if (!item || this.data.saving || this.data.deleting || this.data.convertingQuestionToAnswer) {
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

  async convertCurrentQuestionToAnswer() {
    const item = this.data.item;
    if (
      !item ||
      !item.canConvertQuestionToAnswer ||
      !item.chat_message_id ||
      this.data.saving ||
      this.data.deleting ||
      this.data.convertingQuestionToAnswer
    ) {
      return;
    }
    const confirmed = await confirmConvertQuestionToAnswer(item);
    if (!confirmed) {
      return;
    }
    this.stopPlayback();
    this.resetShareVideoState();
    this.setData({ convertingQuestionToAnswer: true, error: "" });
    try {
      await convertMemoirInterviewQuestionToAnswer(item.project_id, item.chat_message_id);
      wx.showToast({ title: "已改成回答", icon: "success" });
      await this.loadTranscript();
      this.setData({ moreActionsExpanded: false });
    } catch (error) {
      if (routeAuthError(error, "login")) {
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "转换失败，请稍后重试" });
    } finally {
      this.setData({ convertingQuestionToAnswer: false });
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
    if (this.data.saving || this.data.deleting || this.data.convertingQuestionToAnswer) {
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

  openLogin() {
    navigateToLogin();
  },

  onShow() {
    this.setData({ pageClass: accessibilityPageClass("detail-page") });
    this.startOfflineAsrPollingIfNeeded();
  },

  onHide() {
    this.stopOfflineAsrPolling();
  },

  onUnload() {
    syncEditUnloadAlert(null);
    this.stopOfflineAsrPolling();
    this.stopPlayback();
    this.cancelVoiceEdit();
    this.shareVideoController.dispose();
  },
});
