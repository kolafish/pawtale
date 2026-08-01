import { definePage } from "../../services/page-definition";
import { accessibilityPageClass } from "../../services/accessibility-settings";
import { trackAuditAction } from "../../services/audit-tracker";
import {
  getMemoirVoiceTranscript,
} from "../../services/memoir-transcript-api";
import { AudioPlaybackController } from "../../services/audio-playback";
import {
  isAuthError,
  navigateToLogin,
} from "../../services/navigation";
import {
  pageUrl,
  queryParam,
  type PageQuery,
} from "../../services/page-input";
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

definePage<TranscriptDetailData>()({
  audioPlayback: new AudioPlaybackController(),
  loadToken: 0,
  voiceEditRequestToken: 0,
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
    editPreviewVersion: "after",
    voiceEditGatewayUrl: "",
    voiceEditLoading: false,
    voiceEditInstructionText: "",
    voiceEditMessage: "",
    assignmentEditorOpen: false,
    assignmentLoading: false,
    assignmentSaving: false,
    assignmentError: "",
    assignmentOptions: [],
    assignmentIndex: 0,
    assignmentText: "",
    assignmentCanSave: false,
    assignmentPlacement: null,
  },

  async onLoad(options?: PageQuery) {
    const utteranceId = queryParam(options, "id");
    this.setData({
      utteranceId,
      questionExpanded: false,
      moreActionsExpanded: false,
      editReferenceExpanded: false,
      editPreviewVersion: "after",
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

  openLogin() {
    navigateToLogin();
  },

  openMediaTools() {
    if (!this.data.item || this.data.item.canRecoverTranscription) {
      return;
    }
    wx.navigateTo({
      url: pageUrl("/pages/transcript-media/index", { id: this.data.item.id }),
    });
  },

  onShow() {
    this.setData({ pageClass: accessibilityPageClass("detail-page") });
    this.startOfflineAsrPollingIfNeeded();
  },

  onHide() {
    this.invalidateVoiceEditRequest();
    this.stopOfflineAsrPolling();
  },

  onUnload() {
    this.voiceEditRequestToken += 1;
    syncEditUnloadAlert(null);
    this.stopOfflineAsrPolling();
    this.stopPlayback();
  },
});
