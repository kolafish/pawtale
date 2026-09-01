import { accessibilityPageClass } from "../../services/accessibility-settings";
import { definePage, pageRuntime } from "../../services/page-definition";
import {
  createMemoirMaterialImageGenerationJob,
  createMemoirTranscriptShareVideoJob,
  downloadMemoirShareVideoJob,
  getMemoirMaterialImageGenerationJob,
  getMemoirShareVideoJob,
  getMemoirVoiceTranscript,
} from "../../services/memoir-transcript-api";
import {
  isAuthError,
  navigateToLogin,
} from "../../services/navigation";
import {
  queryParam,
  type PageQuery,
} from "../../services/page-input";
import { shareLocalFileMessage } from "../../services/share";
import {
  initialTranscriptMaterialImageTaskState,
  TranscriptMaterialImageController,
} from "../../services/transcript-material-image";
import {
  initialTranscriptShareVideoData,
  TranscriptShareVideoController,
} from "../../services/transcript-share-video";
import {
  toViewItem,
} from "../transcript-detail/transcript-detail-model";
import {
  transcriptMediaWorkflowMethods,
  type TranscriptMediaData,
} from "../transcript-detail/transcript-detail-share-video-workflow";

definePage<TranscriptMediaData>()({
  shareVideoController: pageRuntime(() => new TranscriptShareVideoController({
    createJob: createMemoirTranscriptShareVideoJob,
    getJob: getMemoirShareVideoJob,
    downloadJob: downloadMemoirShareVideoJob,
    shareFile: shareLocalFileMessage,
  })),
  materialImageController: pageRuntime(() => new TranscriptMaterialImageController({
    createJob: createMemoirMaterialImageGenerationJob,
    getJob: getMemoirMaterialImageGenerationJob,
  })),
  loadToken: 0,
  shareVideoBackgroundUploadToken: 0,

  data: {
    pageClass: "detail-page",
    loading: false,
    needsLogin: false,
    error: "",
    utteranceId: "",
    item: null,
    ...initialTranscriptShareVideoData(),
    ...initialTranscriptMaterialImageTaskState(),
  },

  async onLoad(options?: PageQuery) {
    this.shareVideoController.setStateListener((patch) => this.setData(patch));
    this.materialImageController.setStateListener((patch) => this.setData(patch));
    this.resetShareVideoState();
    this.setData({ utteranceId: queryParam(options, "id") });
    await this.loadTranscript();
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
      if (requestToken !== this.loadToken) {
        return;
      }
      this.setData({
        needsLogin: false,
        item: toViewItem(transcript),
      });
      await this.openShareVideoPanel();
    } catch (error) {
      if (requestToken !== this.loadToken) {
        return;
      }
      if (isAuthError(error)) {
        this.setData({ needsLogin: true, item: null, error: "" });
        return;
      }
      this.setData({ error: error instanceof Error ? error.message : "图片与视频工具加载失败" });
    } finally {
      if (requestToken === this.loadToken) {
        this.setData({ loading: false });
      }
    }
  },

  ...transcriptMediaWorkflowMethods,

  openLogin() {
    navigateToLogin();
  },

  onShow() {
    this.setData({ pageClass: accessibilityPageClass("detail-page") });
  },

  onUnload() {
    this.shareVideoBackgroundUploadToken += 1;
    this.shareVideoController.dispose();
    this.materialImageController.dispose();
  },
});
