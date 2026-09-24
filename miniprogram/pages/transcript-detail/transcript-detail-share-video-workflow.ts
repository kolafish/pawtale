import type { MemoirShareVideoCustomBackgroundAsset } from "../../domain/memoir-transcript";
import { trackAuditAction } from "../../services/audit-tracker";
import { setPageNavigationTitle } from "../../services/page-navigation";
import {
  downloadMemoirShareVideoCustomBackgroundPreview,
  getMemoirShareVideoJob,
  getMemoirTranscriptShareVideoContext,
  uploadMemoirShareVideoCustomBackground,
} from "../../services/memoir-transcript-api";
import {
  datasetNumber,
  datasetString,
  type PageDatasetEvent,
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
import {
  shareVideoFileName,
  type TranscriptDetailItemView,
} from "./transcript-detail-model";
import {
  TranscriptMaterialImageController,
  type TranscriptMaterialImageTaskState,
} from "../../services/transcript-material-image";

type ShareVideoSegmentEvent = PageDatasetEvent<{ index?: string | number }>;
type ShareVideoBackgroundEvent = PageDatasetEvent<{
  backgroundMode?: string;
  backgroundSource?: string;
  backgroundStyle?: string;
}>;

export interface TranscriptMediaData
  extends TranscriptShareVideoData, TranscriptMaterialImageTaskState {
  viewMode: "create" | "video";
  pageClass: string;
  loading: boolean;
  needsLogin: boolean;
  error: string;
  utteranceId: string;
  item: TranscriptDetailItemView | null;
}

interface TranscriptMediaWorkflowHost {
  data: TranscriptMediaData;
  shareVideoController: TranscriptShareVideoController;
  materialImageController: TranscriptMaterialImageController;
  shareVideoBackgroundUploadToken: number;
  setData(patch: Partial<TranscriptMediaData>): void;
}

export interface TranscriptMediaWorkflowMethods {
  resetShareVideoState(): void;
  openShareVideoPanel(resumeRunningJob?: boolean): Promise<void>;
  remakeShareVideo(): Promise<void>;
  selectShareVideoSegment(event: ShareVideoSegmentEvent): void;
  clearShareVideoSelection(): void;
  selectShareVideoBackgroundSource(event: ShareVideoBackgroundEvent): void;
  selectShareVideoBackgroundStyle(event: ShareVideoBackgroundEvent): void;
  selectShareVideoBackgroundMode(event: ShareVideoBackgroundEvent): void;
  chooseShareVideoCustomBackground(): Promise<void>;
  createMaterialImage(): Promise<void>;
  openImageAssets(): void;
  createShareVideo(): Promise<void>;
  shareGeneratedVideo(): Promise<void>;
}

type TranscriptMediaWorkflow = TranscriptMediaWorkflowHost
  & TranscriptMediaWorkflowMethods;

export const transcriptMediaWorkflowMethods:
TranscriptMediaWorkflowMethods & ThisType<TranscriptMediaWorkflow> = {
  resetShareVideoState() {
    this.shareVideoController.reset();
    this.materialImageController.reset();
    this.shareVideoBackgroundUploadToken += 1;
    this.setData(initialTranscriptShareVideoData());
  },

  async openShareVideoPanel(resumeRunningJob = true) {
    const item = this.data.item;
    if (!item || item.canRecoverTranscription) {
      return;
    }
    this.shareVideoController.reset();
    this.materialImageController.reset();
    this.setData({
      ...initialTranscriptShareVideoData(),
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
      if (resumeRunningJob && context.latest_job_id) {
        try {
          const job = await getMemoirShareVideoJob(context.latest_job_id);
          if (
            job.utterance_id === item.id &&
            job.project_id === item.project_id &&
            (job.status === "pending" || job.status === "running")
          ) {
            await this.shareVideoController.showJob(job);
            this.setData({ viewMode: "video" });
            setPageNavigationTitle("分享视频");
            return;
          }
        } catch (error) {
          this.setData({
            shareVideoError: error instanceof Error ? error.message : "上次视频状态读取失败",
          });
        }
      }
      if (context.latest_image_job_id) {
        await this.materialImageController.refresh(context.latest_image_job_id);
        const imageJob = this.data.materialImageJob;
        if (imageJob) {
          const firstSegmentIndex = context.segments.findIndex(
            (segment) => segment.end_char > imageJob.start_char,
          );
          let lastSegmentIndex = -1;
          for (let index = context.segments.length - 1; index >= 0; index -= 1) {
            if (context.segments[index].start_char < imageJob.end_char) {
              lastSegmentIndex = index;
              break;
            }
          }
          const statusText = this.data.materialImageStatusText;
          const statusClass = this.data.materialImageStatusClass;
          this.setData({
            ...shareVideoSelectionPatch(context, firstSegmentIndex, lastSegmentIndex),
            materialImageStatusText: statusText,
            materialImageStatusClass: statusClass,
          });
        }
      }
    } catch (error) {
      this.setData({
        shareVideoError: error instanceof Error ? error.message : "分享视频素材加载失败",
        shareVideoStatusClass: "failed",
      });
    } finally {
      this.setData({ shareVideoLoadingContext: false });
    }
  },

  async remakeShareVideo() {
    if (this.data.shareVideoCreating || this.data.shareVideoSharing) {
      return;
    }
    this.setData({ viewMode: "create" });
    setPageNavigationTitle("做成图片或视频");
    await this.openShareVideoPanel(false);
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
      this.data.shareVideoCustomBackgroundUploading ||
      this.data.materialImageCreating ||
      this.data.materialImagePolling
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
    this.materialImageController.clearResult();
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
      this.data.shareVideoSharing ||
      this.data.materialImageCreating ||
      this.data.materialImagePolling
    ) {
      return;
    }
    this.shareVideoController.clearResult();
    this.materialImageController.clearResult();
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
      this.data.shareVideoCustomBackgroundUploading ||
      this.data.materialImageCreating ||
      this.data.materialImagePolling
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
      this.data.shareVideoCustomBackgroundUploading ||
      this.data.materialImageCreating ||
      this.data.materialImagePolling
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
    this.materialImageController.clearResult();
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
      this.data.shareVideoCustomBackgroundUploading ||
      this.data.materialImageCreating ||
      this.data.materialImagePolling
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
      this.data.shareVideoCustomBackgroundUploading ||
      this.data.materialImageCreating ||
      this.data.materialImagePolling
    ) {
      return;
    }
    let uploadToken = 0;
    try {
      const filePath = await chooseShareVideoBackgroundImage();
      if (!filePath) {
        return;
      }
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

  async createMaterialImage() {
    const item = this.data.item;
    if (
      !item ||
      !this.data.shareVideoContext ||
      !this.data.shareVideoSelectedText ||
      this.data.materialImageCreating ||
      this.data.materialImagePolling ||
      this.data.shareVideoCreating ||
      this.data.shareVideoPolling ||
      this.data.shareVideoDownloadingPreview ||
      this.data.shareVideoSharing ||
      this.data.shareVideoCustomBackgroundUploading
    ) {
      return;
    }
    const job = await this.materialImageController.create({
      utteranceId: item.id,
      startChar: this.data.shareVideoSelectedStartChar,
      endChar: this.data.shareVideoSelectedEndChar,
      styleId: this.data.shareVideoBackgroundStyle,
    });
    if (job) {
      trackAuditAction(
        "transcript.detail_material_image_create",
        {
          asset_id: job.asset_id,
          style_id: job.style_id,
          selected_char_count: Array.from(job.selected_text).length,
        },
        { projectId: item.project_id },
      );
    }
  },

  openImageAssets() {
    const projectId = this.data.item?.project_id || "";
    if (!projectId) {
      return;
    }
    wx.navigateTo({
      url: `/pages/image-assets/index?projectId=${encodeURIComponent(projectId)}`,
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
      this.data.shareVideoSharing ||
      this.data.shareVideoCustomBackgroundUploading ||
      this.data.materialImageCreating ||
      this.data.materialImagePolling
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
      this.setData({ viewMode: "video" });
      setPageNavigationTitle("分享视频");
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
    const shared = await this.shareVideoController.share(shareVideoFileName(job?.title || job?.selected_text));
    if (shared && item && job) {
      trackAuditAction("transcript.detail_share_video_share", { job_id: job.id }, { projectId: item.project_id });
    }
  },
};
