import { accessibilityPageClass } from "../../services/accessibility-settings";
import { definePage, pageRuntime } from "../../services/page-definition";
import {
  createMemoirMaterialImageGenerationJob,
  createMemoirShareVideoCandidateJob,
  createMemoirTranscriptShareVideoJob,
  downloadMemoirShareVideoJob,
  getMemoirMaterialImageGenerationJob,
  getMemoirShareVideoJob,
  getMemoirShareVideoCandidate,
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
import { setPageNavigationTitle } from "../../services/page-navigation";
import {
  initialTranscriptMaterialImageTaskState,
  TranscriptMaterialImageController,
} from "../../services/transcript-material-image";
import {
  initialTranscriptShareVideoData,
  shareVideoSelectionPatch,
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
    createCandidateJob: createMemoirShareVideoCandidateJob,
    getJob: getMemoirShareVideoJob,
    downloadJob: downloadMemoirShareVideoJob,
    shareFile: shareLocalFileMessage,
  })),
  materialImageController: pageRuntime(() => new TranscriptMaterialImageController({
    createJob: createMemoirMaterialImageGenerationJob,
    getJob: getMemoirMaterialImageGenerationJob,
  })),
  loadToken: 0,
  requestedShareVideoJobId: "",
  requestedShareVideoCandidateId: "",
  shareVideoBackgroundUploadToken: 0,

  data: {
    pageClass: "detail-page",
    loading: false,
    needsLogin: false,
    error: "",
    viewMode: "create",
    utteranceId: "",
    item: null,
    ...initialTranscriptShareVideoData(),
    ...initialTranscriptMaterialImageTaskState(),
  },

  async onLoad(options?: PageQuery) {
    this.shareVideoController.setStateListener((patch) => this.setData(patch));
    this.materialImageController.setStateListener((patch) => this.setData(patch));
    this.resetShareVideoState();
    this.requestedShareVideoJobId = queryParam(options, "jobId");
    this.requestedShareVideoCandidateId = queryParam(options, "candidateId");
    this.setData({
      utteranceId: queryParam(options, "id"),
      viewMode: this.requestedShareVideoJobId ? "video" : "create",
    });
    setPageNavigationTitle(this.requestedShareVideoJobId ? "分享视频" : "做成图片或视频");
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
      const item = toViewItem(transcript);
      this.setData({ needsLogin: false, item });
      if (this.requestedShareVideoJobId) {
        const job = await getMemoirShareVideoJob(this.requestedShareVideoJobId);
        if (requestToken !== this.loadToken) {
          return;
        }
        if (job.utterance_id !== item.id || job.project_id !== item.project_id) {
          throw new Error("视频不属于这段讲述");
        }
        await this.shareVideoController.showJob(job);
      } else {
        await this.openShareVideoPanel(!this.requestedShareVideoCandidateId, !this.requestedShareVideoCandidateId);
        if (this.requestedShareVideoCandidateId) {
          const { candidate } = await getMemoirShareVideoCandidate(item.project_id);
          if (requestToken !== this.loadToken) {
            return;
          }
          const context = this.data.shareVideoContext;
          if (!context || !candidate || candidate.id !== this.requestedShareVideoCandidateId
            || candidate.project_id !== item.project_id || candidate.utterance_id !== item.id
            || candidate.status !== "ready" || candidate.text_hash !== context.text_hash) {
            throw new Error("推荐片段已更新，请从这段讲述重新选择内容");
          }
          const startIndex = context.segments.findIndex((segment) => segment.start_char === candidate.start_char);
          const endIndex = context.segments.findIndex((segment) => segment.end_char === candidate.end_char);
          if (startIndex < 0 || endIndex < startIndex) {
            throw new Error("推荐片段已更新，请从这段讲述重新选择内容");
          }
          this.setData({
            shareVideoCandidate: candidate,
            ...shareVideoSelectionPatch(context, startIndex, endIndex),
          });
        }
      }
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

  async refreshShareVideoJob() {
    const job = this.data.shareVideoJob;
    if (job && job.utterance_id === this.data.utteranceId) {
      await this.shareVideoController.refresh(job.id);
    }
  },

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
