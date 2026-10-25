import { accessibilityPageClass } from "../../services/accessibility-settings";
import { trackAuditAction } from "../../services/audit-tracker";
import { definePage, pageRuntime } from "../../services/page-definition";
import {
  createMemoirMaterialImageGenerationJob,
  createMemoirShareVideoCandidateJob,
  createMemoirTranscriptShareVideoJob,
  downloadMemoirShareVideoJob,
  deleteMemoirShareVideoJob,
  dismissMemoirShareVideoCandidate,
  getMemoirMaterialImageGenerationJob,
  getMemoirShareVideoJob,
  getMemoirVoiceTranscript,
  memoirSharedVideoUrl,
  setMemoirVideoShare,
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
  enableMiniProgramShareMenu,
  hideMiniProgramShareMenu,
  saveVideoToAlbum,
  shareLocalFileMessage,
  sharedVideoShareMessage,
} from "../../services/share";
import { setPageNavigationTitle } from "../../services/page-navigation";
import { readMemoirShareLikes } from "../../services/memoir-share-like-api";
import { initialVideoPlaybackData, videoPlaybackMethods } from "../../services/video-playback";
import { shareLikerViews } from "../../services/share-likes";
import { shareVideoDeleteConfirmation } from "../../services/share-video-delete";
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

definePage<TranscriptMediaData & { shareCardPreparing: boolean }>()({
  shareVideoController: pageRuntime(() => new TranscriptShareVideoController({
    createJob: createMemoirTranscriptShareVideoJob,
    createCandidateJob: createMemoirShareVideoCandidateJob,
    getJob: getMemoirShareVideoJob,
    downloadJob: downloadMemoirShareVideoJob,
    streamUrl: (job) => job.output?.share_enabled ? memoirSharedVideoUrl(job.output.share_code) : "",
    enableShare: (job) => setMemoirVideoShare(job.id, true),
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

  data: {
    pageClass: "detail-page",
    loading: false,
    needsLogin: false,
    error: "",
    viewMode: "create",
    shareVideoDeleting: false,
    shareCardPreparing: false,
    shareVideoPreviewPortrait: true,
    utteranceId: "",
    item: null,
    shareVideoLikeCount: 0,
    shareVideoLikers: [],
    ...initialTranscriptShareVideoData(),
    ...initialTranscriptMaterialImageTaskState(),
    ...initialVideoPlaybackData(),
  },

  ...videoPlaybackMethods,

  async onLoad(options?: PageQuery) {
    this.shareVideoController.setStateListener((patch) => {
      const previousJob = this.data.shareVideoJob;
      const job = patch.shareVideoJob;
      this.setData({
        ...patch,
        ...(job !== undefined && job?.id !== previousJob?.id
          ? { shareVideoLikeCount: 0, shareVideoLikers: [] } : {}),
      });
      this.syncVideoShareMenu();
      if (job?.status === "succeeded" && job.output?.share_enabled && job.output?.share_code
        && job.output.share_code !== previousJob?.output?.share_code) {
        void this.loadShareVideoLikers(job.output.share_code, this.loadToken);
      }
    });
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

  syncVideoShareMenu() {
    if (this.data.shareVideoJob?.output?.share_enabled && !this.data.shareCardPreparing) enableMiniProgramShareMenu();
    else hideMiniProgramShareMenu();
  },

  onShareAppMessage() {
    const job = this.data.shareVideoJob;
    if (!job?.output?.share_enabled) return homeShareAppMessage();
    return sharedVideoShareMessage(job.title, job.output.share_code, memoirSharedVideoUrl(job.output.share_code, "card"));
  },

  async prepareVideoShare() {
    const job = this.data.shareVideoJob;
    if (!job?.output || job.output.share_enabled || this.data.shareCardPreparing) return;
    const requestToken = this.loadToken;
    this.setData({ shareCardPreparing: true, shareVideoError: "" });
    this.syncVideoShareMenu();
    try {
      const sharedJob = await setMemoirVideoShare(job.id, true);
      if (requestToken !== this.loadToken || this.data.shareVideoJob?.id !== job.id) return;
      await this.shareVideoController.showJob(sharedJob);
      trackAuditAction("transcript.detail_share_video_card", { job_id: job.id }, { projectId: job.project_id });
      wx.showToast({ title: "已准备好，请再点分享给好友", icon: "none" });
    } catch (error) {
      if (requestToken === this.loadToken && this.data.shareVideoJob?.id === job.id) {
        this.setData({ shareVideoError: error instanceof Error ? error.message : "分享准备失败，请重试" });
      }
    } finally {
      if (requestToken === this.loadToken) {
        this.setData({ shareCardPreparing: false });
        this.syncVideoShareMenu();
      }
    }
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
          // Read the consumed suggestion by id; it no longer appears in the home feed.
          const candidate = await dismissMemoirShareVideoCandidate(item.project_id, this.requestedShareVideoCandidateId);
          if (requestToken !== this.loadToken) {
            return;
          }
          const context = this.data.shareVideoContext;
          if (!context || !candidate || candidate.id !== this.requestedShareVideoCandidateId
            || candidate.project_id !== item.project_id || candidate.utterance_id !== item.id
            || candidate.status !== "dismissed" || candidate.text_hash !== context.text_hash) {
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

  async loadShareVideoLikers(shareCode: string, requestToken: number) {
    try {
      // The author reads the same likes as family and gets every name back.
      const likes = await readMemoirShareLikes({ video_code: shareCode });
      if (requestToken !== this.loadToken || this.data.shareVideoJob?.output?.share_code !== shareCode) return;
      this.setData({ shareVideoLikeCount: likes.count, shareVideoLikers: shareLikerViews(likes.likers) });
    } catch (error) {
      console.warn("share video likes unavailable", error);
    }
  },

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
    if (this.data.shareVideoSharing || this.data.shareVideoDeleting || this.data.shareCardPreparing) {
      return;
    }
    wx.showActionSheet({
      itemList: ["重新制作", "删除这版视频", ...(this.data.shareVideoJob?.output?.share_enabled ? ["停止分享"] : [])],
      success: ({ tapIndex }) => {
        if (tapIndex === 0) {
          void this.remakeShareVideo();
        } else if (tapIndex === 1) {
          void this.deleteShareVideo();
        } else if (tapIndex === 2) {
          void this.stopVideoShare();
        }
      },
    });
  },

  async stopVideoShare() {
    const job = this.data.shareVideoJob;
    if (!job || this.data.shareCardPreparing || this.data.shareVideoSharing) return;
    const requestToken = this.loadToken;
    this.setData({ shareCardPreparing: true, shareVideoError: "" });
    this.syncVideoShareMenu();
    try {
      const updated = await setMemoirVideoShare(job.id, false);
      if (requestToken !== this.loadToken || this.data.shareVideoJob?.id !== job.id) return;
      await this.shareVideoController.showJob(updated);
      wx.showToast({ title: "已停止分享", icon: "success" });
    } catch (error) {
      if (requestToken === this.loadToken && this.data.shareVideoJob?.id === job.id) {
        this.setData({ shareVideoError: error instanceof Error ? error.message : "停止分享失败" });
      }
    } finally {
      if (requestToken === this.loadToken) {
        this.setData({ shareCardPreparing: false });
        this.syncVideoShareMenu();
      }
    }
  },

  markShareVideoStreamError() {
    this.setData({ shareVideoError: "视频没有加载出来，请检查网络后重试" });
  },

  async deleteShareVideo() {
    const job = this.data.shareVideoJob;
    if (!job || (job.status !== "succeeded" && job.status !== "failed") || this.data.shareVideoDeleting
      || this.data.shareVideoSharing || this.data.shareVideoDownloadingPreview) {
      return;
    }
    const requestToken = this.loadToken;
    this.setData({ shareVideoDeleting: true });
    try {
      const confirmed = await new Promise<boolean>((resolve) => {
        wx.showModal({
          ...shareVideoDeleteConfirmation(job),
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
      wx.showToast({ title: job.status === "failed" ? "已删除" : "视频已删除", icon: "success" });
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
    this.syncVideoShareMenu();
  },

  onUnload() {
    this.loadToken += 1;
    this.shareVideoController.dispose();
    this.materialImageController.dispose();
  },
});
