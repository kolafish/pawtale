import { accessibilityPageClass } from "../../services/accessibility-settings";
import { trackAuditAction } from "../../services/audit-tracker";
import { definePage, pageRuntime } from "../../services/page-definition";
import {
  createMemoirMaterialImageGenerationJob,
  createMemoirShareVideoCandidateJob,
  createMemoirTranscriptShareVideoJob,
  downloadMemoirShareVideoJob,
  deleteMemoirShareVideoJob,
  getMemoirMaterialImageGenerationJob,
  getMemoirShareVideoJob,
  getMemoirShareVideoCandidate,
  getMemoirVoiceTranscript,
  memoirSharedVideoUrl,
} from "../../services/memoir-transcript-api";
import {
  isAuthError,
  navigateToLogin,
} from "../../services/navigation";
import {
  queryParam,
  type PageQuery,
} from "../../services/page-input";
import {
  homeShareAppMessage,
  saveVideoToAlbum,
  shareLocalFileMessage,
  sharedVideoShareMessage,
} from "../../services/share";
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
    streamUrl: (job) => memoirSharedVideoUrl(job.output?.share_code || ""),
    shareFile: shareLocalFileMessage,
    saveVideo: saveVideoToAlbum,
  })),
  materialImageController: pageRuntime(() => new TranscriptMaterialImageController({
    createJob: createMemoirMaterialImageGenerationJob,
    getJob: getMemoirMaterialImageGenerationJob,
  })),
  loadToken: 0,
  requestedShareVideoJobId: "",
  requestedShareVideoCandidateId: "",
  // Set by the home card's primary action: start the recommended clip with default settings.
  createRequestedCandidate: false,
  shareVideoBackgroundUploadToken: 0,

  data: {
    pageClass: "detail-page",
    loading: false,
    needsLogin: false,
    error: "",
    viewMode: "create",
    shareVideoDeleting: false,
    shareVideoPreviewPortrait: true,
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
    this.createRequestedCandidate = Boolean(this.requestedShareVideoCandidateId) && queryParam(options, "create") === "1";
    this.setData({
      utteranceId: queryParam(options, "id"),
      viewMode: this.requestedShareVideoJobId ? "video" : "create",
    });
    setPageNavigationTitle(this.requestedShareVideoJobId ? "分享视频" : "做成图片或视频");
    await this.loadTranscript();
    if (this.createRequestedCandidate && this.data.shareVideoCandidate && this.data.shareVideoSelectedCanGenerate) {
      await this.createShareVideo();
    }
  },

  onShareAppMessage() {
    const job = this.data.shareVideoJob;
    const shareCode = job?.output?.share_code;
    if (!job || !shareCode) {
      return homeShareAppMessage();
    }
    trackAuditAction("transcript.detail_share_video_card", { job_id: job.id }, { projectId: job.project_id });
    return sharedVideoShareMessage(job.title, shareCode, memoirSharedVideoUrl(shareCode, "thumbnail"));
  },

  markShareVideoPreviewShape(event: { detail: { width: number; height: number } }) {
    this.setData({ shareVideoPreviewPortrait: event.detail.height >= event.detail.width });
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
          const selection = shareVideoSelectionPatch(context, startIndex, endIndex);
          const hookText = candidate.hook_text;
          this.setData({
            shareVideoCandidate: candidate,
            ...selection,
            shareVideoHookText: hookText,
            shareVideoHookChoices: hookText && !selection.shareVideoHookChoices.includes(hookText)
              ? [hookText, ...selection.shareVideoHookChoices]
              : selection.shareVideoHookChoices,
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

  async saveShareVideoToAlbum() {
    const job = this.data.shareVideoJob;
    if (!job || this.data.shareVideoDeleting) {
      return;
    }
    if (await this.shareVideoController.saveToAlbum()) {
      wx.showToast({ title: "已保存到相册", icon: "success" });
      trackAuditAction("transcript.detail_share_video_save", { job_id: job.id }, { projectId: job.project_id });
    }
  },

  // Remaking and deleting are rarer than sending, so they live behind one menu.
  openShareVideoMoreActions() {
    if (this.data.shareVideoSharing || this.data.shareVideoDeleting) {
      return;
    }
    wx.showActionSheet({
      itemList: ["重新制作", "删除这版视频"],
      success: ({ tapIndex }) => {
        if (tapIndex === 0) {
          void this.remakeShareVideo();
        } else if (tapIndex === 1) {
          void this.deleteShareVideo();
        }
      },
    });
  },

  markShareVideoStreamError() {
    this.setData({ shareVideoError: "视频没有加载出来，请检查网络后重试" });
  },

  async deleteShareVideo() {
    const job = this.data.shareVideoJob;
    if (!job || job.status !== "succeeded" || this.data.shareVideoDeleting
      || this.data.shareVideoSharing || this.data.shareVideoDownloadingPreview) {
      return;
    }
    const requestToken = this.loadToken;
    this.setData({ shareVideoDeleting: true });
    try {
      const confirmed = await new Promise<boolean>((resolve) => {
        wx.showModal({
          title: "删除这版视频？",
          content: `《${job.title}》删除后需要重新制作。其他版本、原始讲述和图片素材会保留。`,
          confirmText: "删除视频",
          confirmColor: "#b64545",
          success: (result) => resolve(Boolean(result.confirm)),
          fail: () => resolve(false),
        });
      });
      if (!confirmed || requestToken !== this.loadToken || this.data.shareVideoJob?.id !== job.id) {
        return;
      }
      await deleteMemoirShareVideoJob(job.id);
      if (requestToken !== this.loadToken) {
        return;
      }
      this.shareVideoController.reset();
      wx.showToast({ title: "视频已删除", icon: "success" });
      wx.navigateBack();
    } catch (error) {
      if (requestToken === this.loadToken) {
        this.setData({ shareVideoError: error instanceof Error ? error.message : "删除视频失败" });
      }
    } finally {
      if (requestToken === this.loadToken) {
        this.setData({ shareVideoDeleting: false });
      }
    }
  },

  async refreshShareVideoJob() {
    if (this.data.shareVideoDeleting) {
      return;
    }
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
    this.loadToken += 1;
    this.shareVideoBackgroundUploadToken += 1;
    this.shareVideoController.dispose();
    this.materialImageController.dispose();
  },
});
