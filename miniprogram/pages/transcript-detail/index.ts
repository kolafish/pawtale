import { definePage } from "../../services/page-definition";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  createMemoirMaterialImageGenerationJob,
  createMemoirTranscriptShareVideoJob,
  downloadMemoirShareVideoJob,
  getMemoirMaterialImageGenerationJob,
  getMemoirShareVideoJob,
  getMemoirVoiceTranscript,
} from "../../services/memoir-transcript-api";
import { AudioPlaybackController } from "../../services/audio-playback";
import {
  shareLocalFileMessage,
} from "../../services/share";
import {
  isAuthError,
  navigateToLogin,
} from "../../services/navigation";
import {
  queryParam,
  type PageQuery,
} from "../../services/page-input";
import {
  initialTranscriptShareVideoData,
  TranscriptShareVideoController,
} from "../../services/transcript-share-video";
import {
  initialTranscriptMaterialImageTaskState,
  TranscriptMaterialImageController,
} from "../../services/transcript-material-image";
import { PollingTimer } from "../../services/polling-timer";
import { memoirProjectDictationUrl } from "../../services/memoir-api-client";
import {
  OFFLINE_ASR_POLL_INTERVAL_MS,
  syncEditUnloadAlert,
  toViewItem,
  type TranscriptDetailData,
} from "./transcript-detail-model";
import {
  transcriptDetailAudioWorkflowMethods,
} from "./transcript-detail-audio-workflow";
import {
  transcriptDetailEditWorkflowMethods,
} from "./transcript-detail-edit-workflow";
import {
  transcriptDetailShareVideoWorkflowMethods,
} from "./transcript-detail-share-video-workflow";

definePage<TranscriptDetailData>()({
  audioPlayback: new AudioPlaybackController(),
  shareVideoController: new TranscriptShareVideoController({
    createJob: createMemoirTranscriptShareVideoJob,
    getJob: getMemoirShareVideoJob,
    downloadJob: downloadMemoirShareVideoJob,
    shareFile: shareLocalFileMessage,
  }),
  materialImageController: new TranscriptMaterialImageController({
    createJob: createMemoirMaterialImageGenerationJob,
    getJob: getMemoirMaterialImageGenerationJob,
  }),
  loadToken: 0,
  shareVideoBackgroundUploadToken: 0,
  offlineAsrPoller: new PollingTimer(OFFLINE_ASR_POLL_INTERVAL_MS),
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
    voiceEditGatewayUrl: "",
    voiceEditLoading: false,
    voiceEditSaving: false,
    voiceEditInstructionText: "",
    voiceEditPreview: null,
    assignmentEditorOpen: false,
    assignmentLoading: false,
    assignmentSaving: false,
    assignmentError: "",
    assignmentOptions: [],
    assignmentIndex: 0,
    assignmentText: "",
    assignmentCanSave: false,
    assignmentPlacement: null,
    ...initialTranscriptShareVideoData(),
    ...initialTranscriptMaterialImageTaskState(),
  },

  async onLoad(options?: PageQuery) {
    this.shareVideoController.setStateListener((patch) => this.setData(patch));
    this.materialImageController.setStateListener((patch) => this.setData(patch));
    this.resetShareVideoState();
    const utteranceId = queryParam(options, "id");
    this.setData({
      utteranceId,
      questionExpanded: false,
      moreActionsExpanded: false,
      editReferenceExpanded: false,
      assignmentEditorOpen: false,
      assignmentError: "",
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
        voiceEditGatewayUrl: memoirProjectDictationUrl(transcript.project_id),
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

  ...transcriptDetailAudioWorkflowMethods,

  ...transcriptDetailEditWorkflowMethods,

  ...transcriptDetailShareVideoWorkflowMethods,

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
    this.shareVideoController.dispose();
    this.materialImageController.dispose();
  },
});
