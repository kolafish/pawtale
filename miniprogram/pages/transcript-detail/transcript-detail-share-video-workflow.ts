import { chooseImageFile } from "../../services/authenticated-media";
import type { MemoirShareVideoCustomBackgroundAsset } from "../../domain/memoir-transcript";
import {
  MEMOIR_SHARE_VIDEO_STYLES,
  normalizeMemoirShareVideoStyle,
} from "../../domain/memoir-transcript";
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
  datasetChoice,
  datasetString,
  type PageDatasetEvent,
  type PageTextInputEvent,
} from "../../services/page-input";
import {
  initialTranscriptShareVideoData,
  saveShareVideoBackgroundSelection,
  shareVideoBackgroundPatch,
  shareVideoSelectionPatch,
  shareVideoStylePatch,
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
type ShareVideoStyleEvent = PageDatasetEvent<{ videoStyle?: string }>;
type ShareVideoBackgroundEvent = PageDatasetEvent<{
  backgroundMode?: string;
  backgroundStyle?: string;
}>;

export interface TranscriptMediaData
  extends TranscriptShareVideoData, TranscriptMaterialImageTaskState {
  viewMode: "create" | "video";
  shareVideoDeleting: boolean;
  // Share videos are portrait since the vertical renderer; older ones are landscape.
  shareVideoPreviewPortrait: boolean;
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
  openShareVideoPanel(resumeRunningJob?: boolean, restoreImageJob?: boolean): Promise<void>;
  remakeShareVideo(): Promise<void>;
  selectShareVideoSegment(event: ShareVideoSegmentEvent): void;
  clearShareVideoSelection(): void;
  selectShareVideoStyle(event: ShareVideoStyleEvent): void;
  selectShareVideoBackgroundStyle(event: ShareVideoBackgroundEvent): void;
  selectShareVideoBackgroundMode(event: ShareVideoBackgroundEvent): void;
  toggleShareVideoMoreSettings(): void;
  changeShareVideoBackgroundNote(event: PageTextInputEvent): void;
  chooseShareVideoCustomBackground(): Promise<void>;
  createMaterialImage(): Promise<void>;
  openImageAssets(): void;
  createShareVideo(): Promise<void>;
  chooseShareVideoHook(): void;
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

  async openShareVideoPanel(resumeRunningJob = true, restoreImageJob = true) {
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
        ...shareVideoStylePatch(context),
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
      if (restoreImageJob && context.latest_image_job_id) {
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
    if (this.data.shareVideoCreating || this.data.shareVideoSharing || this.data.shareVideoDeleting) {
      return;
    }
    const previousJob = this.data.shareVideoJob;
    this.setData({ viewMode: "create" });
    setPageNavigationTitle("做成图片或视频");
    await this.openShareVideoPanel(false, false);
    const context = this.data.shareVideoContext;
    if (context && previousJob) {
      const firstIndex = context.segments.findIndex((segment) => segment.end_char > previousJob.start_char);
      let lastIndex = -1;
      for (let index = context.segments.length - 1; index >= 0; index -= 1) {
        if (context.segments[index].start_char < previousJob.end_char) {
          lastIndex = index;
          break;
        }
      }
      const selection = shareVideoSelectionPatch(context, firstIndex, lastIndex);
      const sameText = selection.shareVideoSelectedText === previousJob.selected_text;
      this.setData({
        ...shareVideoBackgroundPatch(context, {
          backgroundStyle: previousJob.background_style || "",
          backgroundMode: previousJob.background_mode || "",
        }),
        ...shareVideoStylePatch(context,
          normalizeMemoirShareVideoStyle(previousJob.video_style, previousJob.background_source)),
        ...(sameText ? selection : {}),
        shareVideoCustomBackgroundAssetId: previousJob.custom_background_asset_id || "",
        shareVideoBackgroundNote: previousJob.background_note || "",
        shareVideoCustomBackgroundStatusText: previousJob.custom_background_asset_id
          ? "沿用原视频的图片，也可以重新选择" : "",
        shareVideoError: sameText ? "" : "讲述文字已更新，请重新选择片段。已有视频会继续保留。",
      });
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

  selectShareVideoStyle(event: ShareVideoStyleEvent) {
    if (
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
    const style = datasetChoice(
      event,
      "videoStyle",
      MEMOIR_SHARE_VIDEO_STYLES.map((option) => option.value),
      this.data.shareVideoStyle,
    );
    this.shareVideoController.clearResult();
    this.setData({
      ...shareVideoStylePatch(this.data.shareVideoContext, style),
      shareVideoError: "",
      ...shareVideoSelectionPatch(
        this.data.shareVideoContext,
        this.data.shareVideoSelectedStartIndex,
        this.data.shareVideoSelectedEndIndex,
      ),
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

  toggleShareVideoMoreSettings() {
    this.setData({ shareVideoMoreSettingsOpen: !this.data.shareVideoMoreSettingsOpen });
  },

  changeShareVideoBackgroundNote(event: PageTextInputEvent) {
    this.setData({ shareVideoBackgroundNote: event.detail.value });
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
      this.data.shareVideoStyle !== "uploaded" ||
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
      const filePath = await chooseImageFile();
      if (!filePath) {
        return;
      }
      this.shareVideoController.clearResult();
      this.shareVideoBackgroundUploadToken += 1;
      const token = this.shareVideoBackgroundUploadToken;
      uploadToken = token;
      this.setData({
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
    if (this.data.shareVideoStyleUnavailable) {
      this.setData({ shareVideoError: "插图生成暂不可用，请选择自选画面或往事小剧场" });
      return;
    }
    if (this.data.shareVideoStyle === "uploaded"
      && !this.data.shareVideoCustomBackgroundAssetId) {
      this.setData({
        shareVideoError: "请先选择一张自定义背景图片",
        shareVideoStatusText: "请先选择背景图片",
        shareVideoStatusClass: "failed",
      });
      return;
    }
    const candidate = this.data.shareVideoCandidate?.status === "ready"
      && this.data.shareVideoCandidate.start_char === this.data.shareVideoSelectedStartChar
      && this.data.shareVideoCandidate.end_char === this.data.shareVideoSelectedEndChar
      ? this.data.shareVideoCandidate
      : null;
    // Send the opening line only when the author changed it from the default.
    const hookText = this.data.shareVideoHookText;
    const changedHook = hookText !== (candidate?.hook_text || "");
    this.setData({ viewMode: "video" });
    setPageNavigationTitle("分享视频");
    const job = await this.shareVideoController.create({
      utteranceId: item.id,
      startChar: this.data.shareVideoSelectedStartChar,
      endChar: this.data.shareVideoSelectedEndChar,
      options: {
        videoStyle: this.data.shareVideoStyle,
        ...(changedHook ? { hookText } : {}),
        ...(this.data.shareVideoStyle !== "story_animation" ? {
          backgroundSource: this.data.shareVideoBackgroundSource,
          ...(this.data.shareVideoBackgroundSource === "ai" ? {
            backgroundStyle: this.data.shareVideoBackgroundStyle,
            backgroundMode: this.data.shareVideoBackgroundMode,
            ...(this.data.shareVideoBackgroundNote.trim()
              ? { backgroundNote: this.data.shareVideoBackgroundNote.trim() } : {}),
          } : {}),
          ...(this.data.shareVideoBackgroundSource === "custom" ? {
            customBackgroundAssetId: this.data.shareVideoCustomBackgroundAssetId,
          } : {}),
        } : {}),
      },
      ...(candidate ? { candidate: { projectId: item.project_id, id: candidate.id } } : {}),
    });
    if (job) {
      trackAuditAction(
        "transcript.detail_share_video_create",
        { video_style: this.data.shareVideoStyle, duration_ms: job.duration_ms },
        { projectId: item.project_id },
      );
    } else {
      this.setData({ viewMode: "create" });
      setPageNavigationTitle("做成图片或视频");
    }
  },

  chooseShareVideoHook() {
    // An action sheet holds six rows: automatic plus up to five sentences.
    const choices = this.data.shareVideoHookChoices.slice(0, 5);
    if (!choices.length || this.data.shareVideoCreating) {
      return;
    }
    wx.showActionSheet({
      itemList: ["自动挑选", ...choices],
      success: ({ tapIndex }) => {
        this.setData({ shareVideoHookText: tapIndex > 0 ? choices[tapIndex - 1] : "" });
      },
    });
  },

  async shareGeneratedVideo() {
    if (this.data.shareVideoDeleting) {
      return;
    }
    const item = this.data.item;
    const job = this.data.shareVideoJob;
    const shared = await this.shareVideoController.share(shareVideoFileName(job?.title || job?.selected_text));
    if (shared && item && job) {
      trackAuditAction("transcript.detail_share_video_share", { job_id: job.id }, { projectId: item.project_id });
    }
  },
};
