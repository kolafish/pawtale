import type {
  MemoirShareVideoBackgroundMode,
  MemoirShareVideoBackgroundSource,
  MemoirShareVideoBackgroundStyle,
  MemoirShareVideoJob,
  MemoirTranscriptShareVideoContext,
  MemoirTranscriptShareVideoSegment,
} from "../domain/memoir-transcript";
import { ApiError } from "./request";
import { isShareFileMessageCancelError } from "./share";
import {
  readShareVideoBackgroundSettings,
  saveShareVideoBackgroundSettings,
  type ShareVideoBackgroundSettings,
} from "./share-video-background-settings";
import { formatTranscriptDurationSeconds } from "./transcript-view-model";
import { formatPlaybackClock } from "./time";

export interface TranscriptShareVideoSegmentView extends MemoirTranscriptShareVideoSegment {
  arrayIndex: number;
  selected: boolean;
  durationText: string;
  timeRangeText: string;
  className: string;
}

export interface TranscriptShareVideoTaskState {
  shareVideoCreating: boolean;
  shareVideoPolling: boolean;
  shareVideoDownloadingPreview: boolean;
  shareVideoSharing: boolean;
  shareVideoError: string;
  shareVideoStatusText: string;
  shareVideoStatusClass: string;
  shareVideoJob: MemoirShareVideoJob | null;
  shareVideoPreviewPath: string;
}

export interface TranscriptShareVideoData extends TranscriptShareVideoTaskState {
  shareVideoPanelOpen: boolean;
  shareVideoLoadingContext: boolean;
  shareVideoContext: MemoirTranscriptShareVideoContext | null;
  shareVideoSegments: TranscriptShareVideoSegmentView[];
  shareVideoSelectedStartIndex: number;
  shareVideoSelectedEndIndex: number;
  shareVideoSelectedStartChar: number;
  shareVideoSelectedEndChar: number;
  shareVideoSelectedText: string;
  shareVideoSelectedMetaText: string;
  shareVideoSelectedCanGenerate: boolean;
  shareVideoBackgroundSource: string;
  shareVideoBackgroundSourceLabel: string;
  shareVideoBackgroundSourceOptions: MemoirShareVideoBackgroundSource[];
  shareVideoBackgroundUsesAi: boolean;
  shareVideoBackgroundUsesCustom: boolean;
  shareVideoBackgroundStyle: string;
  shareVideoBackgroundStyleLabel: string;
  shareVideoBackgroundStyleOptions: MemoirShareVideoBackgroundStyle[];
  shareVideoBackgroundMode: string;
  shareVideoBackgroundModeLabel: string;
  shareVideoBackgroundModeOptions: MemoirShareVideoBackgroundMode[];
  shareVideoCustomBackgroundUploading: boolean;
  shareVideoCustomBackgroundAssetId: string;
  shareVideoCustomBackgroundPreviewPath: string;
  shareVideoCustomBackgroundStatusText: string;
}

type ShareVideoSelectionPatch = Pick<
  TranscriptShareVideoData,
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
>;

export type ShareVideoBackgroundPatch = Pick<
  TranscriptShareVideoData,
  | "shareVideoBackgroundSource"
  | "shareVideoBackgroundSourceLabel"
  | "shareVideoBackgroundSourceOptions"
  | "shareVideoBackgroundUsesAi"
  | "shareVideoBackgroundUsesCustom"
  | "shareVideoBackgroundStyle"
  | "shareVideoBackgroundStyleLabel"
  | "shareVideoBackgroundStyleOptions"
  | "shareVideoBackgroundMode"
  | "shareVideoBackgroundModeLabel"
  | "shareVideoBackgroundModeOptions"
>;

export interface TranscriptShareVideoCreateOptions {
  backgroundSource?: string;
  backgroundStyle?: string;
  backgroundMode?: string;
  customBackgroundAssetId?: string;
}

export interface TranscriptShareVideoCreateInput {
  utteranceId: string;
  startChar: number;
  endChar: number;
  options: TranscriptShareVideoCreateOptions;
}

type CreateShareVideoJob = (
  utteranceId: string,
  startChar: number,
  endChar: number,
  options: TranscriptShareVideoCreateOptions,
) => Promise<MemoirShareVideoJob>;

type GetShareVideoJob = (jobId: string) => Promise<MemoirShareVideoJob>;
type DownloadShareVideoJob = (job: MemoirShareVideoJob) => Promise<string>;
type ShareFile = (input: { filePath: string; fileName?: string }) => Promise<void>;
type ShareVideoStateListener = (patch: Partial<TranscriptShareVideoTaskState>) => void;
type PollTimer = ReturnType<typeof setTimeout>;

export interface TranscriptShareVideoControllerOptions {
  createJob: CreateShareVideoJob;
  getJob: GetShareVideoJob;
  downloadJob: DownloadShareVideoJob;
  shareFile: ShareFile;
  pollIntervalMs?: number;
  maxPollFailures?: number;
  schedule?: (callback: () => void, delayMs: number) => PollTimer;
  cancelSchedule?: (timer: PollTimer) => void;
}

const DEFAULT_POLL_INTERVAL_MS = 1500;
const DEFAULT_MAX_POLL_FAILURES = 3;

function shareVideoRangeText(startMs: number, endMs: number): string {
  return `${formatPlaybackClock(startMs / 1000)}-${formatPlaybackClock(endMs / 1000)}`;
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
    return {
      ...segment,
      arrayIndex,
      selected,
      durationText: formatTranscriptDurationSeconds(Math.max(0, segment.end_ms - segment.start_ms) / 1000),
      timeRangeText: shareVideoRangeText(segment.start_ms, segment.end_ms),
      className: [selected ? "selected" : "", segment.selectable ? "" : "disabled"].filter(Boolean).join(" "),
    };
  });
}

export function shareVideoSelectionPatch(
  context: MemoirTranscriptShareVideoContext | null,
  selectedStartIndex: number,
  selectedEndIndex: number,
): ShareVideoSelectionPatch {
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
    statusText = "选择一段校正文后生成图片或视频";
  } else if (!allSelectable) {
    statusText = "这段文字暂不能截取";
    statusClass = "failed";
  } else if (durationMs < minSelectMs) {
    statusText = `可以生成图片；视频至少需要 ${formatTranscriptDurationSeconds(minSelectMs / 1000)}`;
  } else if (durationMs > maxSelectMs) {
    statusText = `可以生成图片；视频最多选择 ${formatTranscriptDurationSeconds(maxSelectMs / 1000)}`;
  } else if (selectedText) {
    statusText = "可以生成图片或视频";
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

function selectedShareVideoOption<T extends { value: string }>(
  options: T[],
  preferredValue: string,
  defaultValue: string,
): T | null {
  return options.find((option) => option.value === preferredValue)
    || options.find((option) => option.value === defaultValue)
    || options[0]
    || null;
}

export function shareVideoBackgroundPatch(
  context: MemoirTranscriptShareVideoContext | null,
  preferred: ShareVideoBackgroundSettings = readShareVideoBackgroundSettings(),
): ShareVideoBackgroundPatch {
  const sourceOptions = context?.background_sources || [];
  const styleOptions = context?.background_styles || [];
  const modeOptions = context?.background_modes || [];
  const source = selectedShareVideoOption(
    sourceOptions,
    preferred.backgroundSource,
    context?.default_background_source || "",
  );
  const style = selectedShareVideoOption(
    styleOptions,
    preferred.backgroundStyle,
    context?.default_background_style || "",
  );
  const mode = selectedShareVideoOption(
    modeOptions,
    preferred.backgroundMode,
    context?.default_background_mode || "",
  );
  const normalizedSource = source?.value || "";
  return {
    shareVideoBackgroundSource: normalizedSource,
    shareVideoBackgroundSourceLabel: source?.label || "",
    shareVideoBackgroundSourceOptions: sourceOptions,
    shareVideoBackgroundUsesAi: normalizedSource === "ai",
    shareVideoBackgroundUsesCustom: normalizedSource === "custom",
    shareVideoBackgroundStyle: style?.value || "",
    shareVideoBackgroundStyleLabel: style?.label || "",
    shareVideoBackgroundStyleOptions: styleOptions,
    shareVideoBackgroundMode: mode?.value || "",
    shareVideoBackgroundModeLabel: mode?.label || "",
    shareVideoBackgroundModeOptions: modeOptions,
  };
}

export function saveShareVideoBackgroundSelection(patch: ShareVideoBackgroundPatch): void {
  saveShareVideoBackgroundSettings({
    backgroundSource: patch.shareVideoBackgroundSource,
    backgroundStyle: patch.shareVideoBackgroundStyle,
    backgroundMode: patch.shareVideoBackgroundMode,
  });
}

export function initialTranscriptShareVideoTaskState(): TranscriptShareVideoTaskState {
  return {
    shareVideoCreating: false,
    shareVideoPolling: false,
    shareVideoDownloadingPreview: false,
    shareVideoSharing: false,
    shareVideoError: "",
    shareVideoStatusText: "",
    shareVideoStatusClass: "",
    shareVideoJob: null,
    shareVideoPreviewPath: "",
  };
}

export function initialTranscriptShareVideoData(): TranscriptShareVideoData {
  return {
    shareVideoPanelOpen: false,
    shareVideoLoadingContext: false,
    shareVideoContext: null,
    ...shareVideoSelectionPatch(null, -1, -1),
    ...shareVideoBackgroundPatch(null),
    shareVideoCustomBackgroundUploading: false,
    shareVideoCustomBackgroundAssetId: "",
    shareVideoCustomBackgroundPreviewPath: "",
    shareVideoCustomBackgroundStatusText: "",
    ...initialTranscriptShareVideoTaskState(),
  };
}

function shareVideoJobStatusText(job: MemoirShareVideoJob): string {
  if (job.status === "succeeded") {
    if (job.background_source === "custom") {
      return "视频已生成，已使用自定义背景";
    }
    if (job.background_source === "classic") {
      return "视频已生成，已使用经典背景";
    }
    if (job.background_status === "failed" || job.background_status === "fallback") {
      return "视频已生成，已使用经典背景";
    }
    if (job.background_effective_mode === "smart_multi" && (job.background_scene_count || 0) > 1) {
      return job.background_status === "partial"
        ? `视频已生成，已使用 ${job.background_scene_count} 张背景图（部分沿用）`
        : `视频已生成，已使用 ${job.background_scene_count} 张背景图`;
    }
    return "视频已生成";
  }
  if (job.status === "failed") {
    return job.error_message || "视频生成失败";
  }
  if (job.status === "running") {
    if (job.background_source === "custom" || job.background_source === "classic") {
      return "正在合成视频";
    }
    if (job.background_status === "pending" || job.background_status === "planning") {
      return "正在准备背景图";
    }
    if (job.background_status === "generating") {
      return (job.background_scene_count || 0) > 1 ? "正在生成多张背景图" : "正在生成背景图";
    }
    if (
      job.background_status === "ready" ||
      job.background_status === "partial" ||
      job.background_status === "failed" ||
      job.background_status === "fallback" ||
      job.background_status === "disabled" ||
      job.background_status === "classic"
    ) {
      return "正在合成视频";
    }
    return "正在生成视频";
  }
  return "视频任务已创建，等待生成";
}

function isTransientPollError(error: unknown): boolean {
  if (error instanceof ApiError) {
    return error.statusCode === 0 || error.statusCode >= 500;
  }
  return true;
}

function isSameShareVideoJobView(
  current: MemoirShareVideoJob | null,
  next: MemoirShareVideoJob,
): boolean {
  return current === next || Boolean(
    current &&
    current.id === next.id &&
    current.status === next.status &&
    shareVideoJobStatusText(current) === shareVideoJobStatusText(next) &&
    current.error_message === next.error_message &&
    current.output?.download_url === next.output?.download_url &&
    current.output?.mime_type === next.output?.mime_type &&
    current.output?.size_bytes === next.output?.size_bytes
  );
}

export class TranscriptShareVideoController {
  private state = initialTranscriptShareVideoTaskState();
  private listener: ShareVideoStateListener | null = null;
  private pollTimer: PollTimer | null = null;
  private token = 0;
  private pollFailureCount = 0;
  private fileCache: Record<string, string> = {};

  constructor(private readonly options: TranscriptShareVideoControllerOptions) {}

  setStateListener(listener: ShareVideoStateListener): void {
    this.listener = listener;
  }

  reset(): void {
    this.invalidateOperations();
    this.fileCache = {};
    this.replaceState(initialTranscriptShareVideoTaskState());
  }

  clearResult(): void {
    this.invalidateOperations();
    this.fileCache = {};
    this.patchState({
      shareVideoCreating: false,
      shareVideoPolling: false,
      shareVideoDownloadingPreview: false,
      shareVideoSharing: false,
      shareVideoError: "",
      shareVideoJob: null,
      shareVideoPreviewPath: "",
    });
  }

  cancel(): void {
    const hadActiveOperation =
      this.state.shareVideoCreating ||
      this.state.shareVideoPolling ||
      this.state.shareVideoDownloadingPreview ||
      this.state.shareVideoSharing;
    const job = this.state.shareVideoJob;
    this.invalidateOperations();
    this.patchState({
      shareVideoCreating: false,
      shareVideoPolling: false,
      shareVideoDownloadingPreview: false,
      shareVideoSharing: false,
      ...(hadActiveOperation ? {
        shareVideoError: job?.status === "failed" ? (job.error_message || "视频生成失败") : "",
        shareVideoStatusText: job ? shareVideoJobStatusText(job) : "",
        shareVideoStatusClass: job?.status || "",
      } : {}),
    });
  }

  resume(jobId: string): void {
    this.invalidateOperations();
    this.patchState({
      shareVideoPolling: true,
      shareVideoStatusText: "正在生成视频",
      shareVideoStatusClass: "running",
    });
    this.schedulePoll(jobId, this.token);
  }

  async create(input: TranscriptShareVideoCreateInput): Promise<MemoirShareVideoJob | null> {
    this.invalidateOperations();
    this.fileCache = {};
    const requestToken = this.token;
    this.patchState({
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
      const job = await this.options.createJob(
        input.utteranceId,
        input.startChar,
        input.endChar,
        input.options,
      );
      if (!this.isCurrent(requestToken)) {
        return null;
      }
      await this.applyJob(job, requestToken);
      return job;
    } catch (error) {
      if (!this.isCurrent(requestToken)) {
        return null;
      }
      this.patchState({
        shareVideoError: error instanceof Error ? error.message : "视频任务创建失败",
        shareVideoStatusText: "视频任务创建失败",
        shareVideoStatusClass: "failed",
      });
      return null;
    } finally {
      if (this.isCurrent(requestToken)) {
        this.patchState({ shareVideoCreating: false });
      }
    }
  }

  async refresh(jobId: string, requestToken = this.token): Promise<void> {
    if (!this.isCurrent(requestToken)) {
      return;
    }
    this.patchState({
      shareVideoPolling: true,
      ...(!this.state.shareVideoStatusText ? {
        shareVideoStatusText: "正在生成视频",
        shareVideoStatusClass: "running",
      } : {}),
    });
    try {
      const job = await this.options.getJob(jobId);
      if (!this.isCurrent(requestToken)) {
        return;
      }
      this.pollFailureCount = 0;
      await this.applyJob(job, requestToken);
    } catch (error) {
      if (!this.isCurrent(requestToken)) {
        return;
      }
      this.pollFailureCount += 1;
      if (isTransientPollError(error) && this.pollFailureCount < this.maxPollFailures()) {
        this.patchState({
          shareVideoPolling: true,
          shareVideoError: "",
          shareVideoStatusText: "视频状态刷新中断，正在重试",
          shareVideoStatusClass: "running",
        });
        this.schedulePoll(jobId, requestToken);
        return;
      }
      this.clearPollTimer();
      this.pollFailureCount = 0;
      this.patchState({
        shareVideoPolling: false,
        shareVideoError: error instanceof Error ? error.message : "视频生成状态刷新失败",
        shareVideoStatusText: "视频生成状态刷新失败",
        shareVideoStatusClass: "failed",
      });
    }
  }

  async share(fileName: string): Promise<boolean> {
    const job = this.state.shareVideoJob;
    if (!job || job.status !== "succeeded") {
      this.patchState({
        shareVideoError: "请先生成视频",
        shareVideoStatusText: "请先生成视频",
        shareVideoStatusClass: "failed",
      });
      return false;
    }
    if (
      this.state.shareVideoCreating ||
      this.state.shareVideoPolling ||
      this.state.shareVideoDownloadingPreview ||
      this.state.shareVideoSharing
    ) {
      return false;
    }
    const requestToken = this.token;
    this.patchState({
      shareVideoSharing: true,
      shareVideoError: "",
      shareVideoStatusText: "正在准备分享",
      shareVideoStatusClass: "running",
    });
    try {
      const filePath = await this.ensurePreviewPath(job, requestToken, "正在准备分享");
      if (!filePath || !this.isCurrent(requestToken)) {
        return false;
      }
      await this.options.shareFile({ filePath, fileName });
      if (!this.isCurrent(requestToken)) {
        return false;
      }
      this.patchState({
        shareVideoStatusText: "已打开微信分享",
        shareVideoStatusClass: "succeeded",
      });
      return true;
    } catch (error) {
      if (!this.isCurrent(requestToken)) {
        return false;
      }
      if (isShareFileMessageCancelError(error)) {
        this.patchState({
          shareVideoStatusText: "视频已生成，可以预览",
          shareVideoStatusClass: "succeeded",
        });
        return false;
      }
      this.patchState({
        shareVideoError: error instanceof Error ? error.message : "视频分享失败",
        shareVideoStatusText: "视频分享失败",
        shareVideoStatusClass: "failed",
      });
      return false;
    } finally {
      if (this.isCurrent(requestToken)) {
        this.patchState({ shareVideoSharing: false });
      }
    }
  }

  dispose(): void {
    this.invalidateOperations();
    this.fileCache = {};
    this.state = initialTranscriptShareVideoTaskState();
    this.listener = null;
  }

  private async applyJob(job: MemoirShareVideoJob, requestToken: number): Promise<void> {
    const running = job.status === "pending" || job.status === "running";
    this.patchState({
      ...(!isSameShareVideoJobView(this.state.shareVideoJob, job) ? { shareVideoJob: job } : {}),
      shareVideoPolling: running,
      shareVideoStatusText: shareVideoJobStatusText(job),
      shareVideoStatusClass: job.status,
      shareVideoError: job.status === "failed" ? (job.error_message || "视频生成失败") : "",
    });
    if (running) {
      this.schedulePoll(job.id, requestToken);
      return;
    }
    this.clearPollTimer();
    if (job.status === "succeeded") {
      await this.loadPreview(job, requestToken);
    }
  }

  private async ensurePreviewPath(
    job: MemoirShareVideoJob,
    requestToken: number,
    statusText = "正在准备预览",
  ): Promise<string> {
    if (!job.output?.download_url) {
      throw new Error("视频还没有生成完成");
    }
    if (this.state.shareVideoJob?.id === job.id && this.state.shareVideoPreviewPath) {
      return this.state.shareVideoPreviewPath;
    }
    const cachedPath = this.fileCache[job.id];
    const jobStatusText = shareVideoJobStatusText(job);
    const readyStatusText = jobStatusText === "视频已生成" ? "视频已生成，可以预览" : jobStatusText;
    if (cachedPath) {
      this.patchState({
        shareVideoPreviewPath: cachedPath,
        shareVideoStatusText: readyStatusText,
        shareVideoStatusClass: "succeeded",
      });
      return cachedPath;
    }
    this.patchState({
      shareVideoDownloadingPreview: true,
      shareVideoError: "",
      shareVideoStatusText: statusText,
      shareVideoStatusClass: "running",
    });
    try {
      const previewPath = await this.options.downloadJob(job);
      if (!this.isCurrent(requestToken)) {
        return "";
      }
      this.fileCache[job.id] = previewPath;
      this.patchState({
        shareVideoPreviewPath: previewPath,
        shareVideoStatusText: readyStatusText,
        shareVideoStatusClass: "succeeded",
      });
      return previewPath;
    } finally {
      if (this.isCurrent(requestToken)) {
        this.patchState({ shareVideoDownloadingPreview: false });
      }
    }
  }

  private async loadPreview(job: MemoirShareVideoJob, requestToken: number): Promise<void> {
    try {
      await this.ensurePreviewPath(job, requestToken);
    } catch (error) {
      if (!this.isCurrent(requestToken)) {
        return;
      }
      this.patchState({
        shareVideoError: error instanceof Error ? error.message : "视频预览下载失败",
        shareVideoStatusText: "视频已生成，预览下载失败",
        shareVideoStatusClass: "failed",
      });
    }
  }

  private invalidateOperations(): void {
    this.clearPollTimer();
    this.pollFailureCount = 0;
    this.token += 1;
  }

  private isCurrent(requestToken: number): boolean {
    return requestToken === this.token;
  }

  private schedulePoll(jobId: string, requestToken: number): void {
    this.clearPollTimer();
    const schedule = this.options.schedule || ((callback, delayMs) => setTimeout(callback, delayMs));
    this.pollTimer = schedule(() => {
      void this.refresh(jobId, requestToken);
    }, this.options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS);
  }

  private clearPollTimer(): void {
    if (this.pollTimer === null) {
      return;
    }
    const cancelSchedule = this.options.cancelSchedule || clearTimeout;
    cancelSchedule(this.pollTimer);
    this.pollTimer = null;
  }

  private maxPollFailures(): number {
    return this.options.maxPollFailures ?? DEFAULT_MAX_POLL_FAILURES;
  }

  private patchState(patch: Partial<TranscriptShareVideoTaskState>): void {
    const changedPatch: Partial<TranscriptShareVideoTaskState> = {};
    (Object.keys(patch) as Array<keyof TranscriptShareVideoTaskState>).forEach((key) => {
      if (!Object.is(this.state[key], patch[key])) {
        Object.assign(changedPatch, { [key]: patch[key] });
      }
    });
    if (!Object.keys(changedPatch).length) {
      return;
    }
    this.state = { ...this.state, ...changedPatch };
    this.listener?.(changedPatch);
  }

  private replaceState(state: TranscriptShareVideoTaskState): void {
    this.state = state;
    this.listener?.(state);
  }
}

function isChooseBackgroundCancel(error: WechatMiniprogram.GeneralCallbackResult): boolean {
  return String(error.errMsg || "").toLowerCase().includes("cancel");
}

export function chooseShareVideoBackgroundImage(): Promise<string | null> {
  if (typeof wx.chooseMedia === "function") {
    return new Promise((resolve, reject) => {
      wx.chooseMedia?.({
        count: 1,
        mediaType: ["image"],
        sourceType: ["album", "camera"],
        sizeType: ["original", "compressed"],
        success: (result) => resolve(result.tempFiles[0]?.tempFilePath || null),
        fail: (error) => {
          if (isChooseBackgroundCancel(error)) {
            resolve(null);
            return;
          }
          reject(new Error(error.errMsg || "选择背景图片失败"));
        },
      });
    });
  }
  return new Promise((resolve, reject) => {
    wx.chooseImage({
      count: 1,
      sourceType: ["album", "camera"],
      sizeType: ["original", "compressed"],
      success: (result) => resolve(result.tempFilePaths[0] || null),
      fail: (error) => {
        if (isChooseBackgroundCancel(error)) {
          resolve(null);
          return;
        }
        reject(new Error(error.errMsg || "选择背景图片失败"));
      },
    });
  });
}
